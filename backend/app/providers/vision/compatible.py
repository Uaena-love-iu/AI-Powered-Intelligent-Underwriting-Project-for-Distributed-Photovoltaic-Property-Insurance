import base64
from io import BytesIO
import json
from pathlib import Path
from typing import Any, Literal

import httpx
from PIL import Image, UnidentifiedImageError
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)

from app.contracts import (
    AisleAssessment,
    Bbox,
    DetectionStatus,
    EnvironmentRelation,
    GeometryAnalysis,
    InstallationAnalysis,
    OcrField,
    RiskCategory,
    RiskFinding,
    RiskSeverity,
)
from app.contracts.enums import RETIRED_RISK_CATEGORIES
from app.contracts.findings import EvidenceObservation
from app.prompts.stages import stage_prompt
from app.prompts.vision import builtin_bundle, compose_prompt, load_bundle
from app.providers.routing import SCENE_PHOTO_CATEGORIES
from app.rules.quality_evidence import simulation_only_quality_claim
from app.settings import VlmSettings

from ..common import (
    MaterialInput,
    ProviderError,
    post_with_connect_retry,
)
from .base import VisionAnalysis, VisionProvider

SUPPORTED_IMAGE_TYPES = {
    "image/jpeg",
    "image/png",
}

DEFAULT_CONFIDENCE_THRESHOLD = 0.65

RISK_CATEGORY_VALUES = "、".join(
    category.value
    for category in RiskCategory
    if category not in RETIRED_RISK_CATEGORIES
)

SYSTEM_PROMPT = builtin_bundle()['prompt']


PROMPT_DEFAULT_PATH = Path(__file__).resolve().parents[4] / "data" / "vision_prompt_default.json"


def default_prompt() -> str:
    return load_bundle(PROMPT_DEFAULT_PATH)['prompt']


def material_prompt(category) -> str:
    bundle = load_bundle(PROMPT_DEFAULT_PATH)
    return compose_prompt(bundle['prompt'], bundle['specialized_prompts'].get(category.value, ''), category)


class VisionFindingPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: RiskCategory
    label: str = Field(min_length=1)
    detection_status: DetectionStatus
    severity: RiskSeverity
    confidence: float = Field(
        ge=0.0,
        le=1.0,
    )
    environment_relation: EnvironmentRelation = EnvironmentRelation.UNCERTAIN
    bbox: Bbox | None = None
    evidence_text: str = Field(min_length=1)
    evidence_coverage: list[EvidenceObservation] = Field(default_factory=list, max_length=20)
    requires_manual_review: bool
    uncertainty_kind: Literal['none', 'visual', 'missing', 'classification', 'conflict'] = 'none'
    area_id: str | None = None
    object_id: str | None = None

    @model_validator(mode="before")
    @classmethod
    def add_default_coordinate_space(
        cls,
        value: Any,
    ) -> Any:
        if not isinstance(value, dict):
            return value

        bbox = value.get("bbox")

        if (
            not isinstance(bbox, dict)
            or "coordinate_space" in bbox
        ):
            return value

        return {
            **value,
            "bbox": {
                **bbox,
                "coordinate_space": "normalized_0_1",
            },
        }


class VisionResponsePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    findings: list[VisionFindingPayload]
    watermark_present: bool | None = None
    watermark_bbox: Bbox | None = None
    watermark_evidence: str | None = None


class CompatibleVisionProvider(VisionProvider):
    """调用 OpenAI Chat Completions 兼容接口的视觉识别 Provider。"""

    def __init__(
        self,
        settings: VlmSettings,
        *,
        transport: httpx.BaseTransport | None = None,
        system_prompt: str | None = None,
        confidence_threshold: float = (
            DEFAULT_CONFIDENCE_THRESHOLD
        ),
    ) -> None:
        if not 0.0 <= confidence_threshold <= 1.0:
            raise ValueError(
                "confidence_threshold must be "
                "between zero and one"
            )
        self._system_prompt = system_prompt
        self._settings = settings
        self._transport = transport
        self._confidence_threshold = (
            confidence_threshold
        )

    @property
    def name(self) -> str:
        return "openai-compatible-vision"

    @property
    def model_name(self) -> str:
        return self._settings.model

    def analyze(
        self,
        material_input: MaterialInput,
    ) -> list[RiskFinding]:
        return self.analyze_with_watermark(material_input).findings

    def _stage_prompt(self, key, **variables):
        policy = getattr(self, "rule_policy", None)
        if not policy:
            return stage_prompt(key, **variables)
        text = policy.snapshot["stage_prompts"][key]
        for name, value in variables.items():
            text = text.replace("{{" + name + "}}", value)
        return text

    def analyze_installation(self, materials: list[MaterialInput],
                             *, filing_fields: list[OcrField] | None = None) -> InstallationAnalysis:
        """Recognize support structures from the original photos in one grouped request."""
        images = [item for item in materials if item.material.media_type in SUPPORTED_IMAGE_TYPES]
        if not images:
            return super().analyze_installation(materials, filing_fields=filing_fields)
        context_fields = [field for field in filing_fields or []
                          if field.value_status.value == "extracted"
                          and field.confidence >= 0.65
                          and field.field_name in {
                              "project_name", "site_address", "construction_content",
                              "project_content", "project_use", "construction_type",
                              "project_type",
                          }]
        filing_context = [{
            "field_id": field.field_id,
            "material_id": field.material_id,
            "field_name": field.field_name,
            "raw_value": field.raw_value or field.normalized_value,
            "source": field.evidence_text,
        } for field in context_fields]
        content: list[dict[str, Any]] = [{
            "type": "text",
            "text": self._stage_prompt(
                "installation",
                FILING_CONTEXT=json.dumps(filing_context, ensure_ascii=False),
            ),
        }]
        # Two editable instructions, one set of images and one model response.
        policy = getattr(self, "rule_policy", None)
        if policy is None or policy.enabled("aisle"):
            content.append({"type": "text", "text": policy.snapshot["stage_prompts"]["aisle_group"] if policy else stage_prompt("aisle_group")})
        aisle_ids = [item.material.material_id for item in images
                     if item.material.category.value in {"panorama", "roof_connection", "parapet"}
                     or any(category.value in {"panorama", "roof_connection", "parapet"}
                            for category in item.material.additional_categories)]
        content.append({"type": "text", "text":
                        "可见通道阻塞观察优先参照这些相关图片编号："
                        + json.dumps(aisle_ids, ensure_ascii=False)
                        + "。其他图片只可作为可见场景背景，不能替代通道证据。"})
        for item in images:
            content.append({"type": "text", "text": f"图片编号：{item.material.material_id}"})
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{item.material.media_type};base64,"
                              f"{base64.b64encode(item.content).decode('ascii')}"},
            })
        body = {
            "model": self.model_name,
            "messages": [{"role": "user", "content": content}],
            "temperature": 0, "enable_thinking": False,
            "response_format": {"type": "json_object"},
        }
        try:
            with httpx.Client(
                timeout=httpx.Timeout(self._settings.timeout_seconds,
                                      connect=min(self._settings.timeout_seconds, 15.0)),
                transport=self._transport, trust_env=self._settings.trust_env,
            ) as client:
                response = post_with_connect_retry(
                    client, f"{self._settings.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self._settings.api_key}",
                             "Content-Type": "application/json"}, json=body,
                )
                response.raise_for_status()
            raw = response.json()["choices"][0]["message"]["content"]
            payload = json.loads(self._extract_json_object(raw))
            raw_aisle = payload.get("aisle_assessment") if isinstance(payload, dict) else None
            if raw_aisle is not None:
                try:
                    AisleAssessment.model_validate(raw_aisle)
                except ValidationError:
                    payload["aisle_assessment"] = None
            analysis = InstallationAnalysis.model_validate({
                **payload, "provider": self.name, "model": self.model_name,
            })
            known_ids = {item.material.material_id for item in images}
            if len(analysis.areas) != 1 or analysis.areas[0].area_id != "project" or any(
                not set(area.material_ids).issubset(known_ids) or not area.material_ids
                or any(region.material_id not in area.material_ids
                       for region in area.evidence_regions)
                for area in analysis.areas
            ) or not set(analysis.filing_field_ids).issubset(
                {field.field_id for field in context_fields}
            ):
                raise ValueError("invalid project installation evidence references")
            aisle = analysis.aisle_assessment
            if aisle is not None:
                if (aisle.material_id not in known_ids
                        or not set(aisle.supporting_material_ids).issubset(known_ids)
                        or aisle.material_id not in aisle.supporting_material_ids):
                    aisle.status = "uncertain"
                    aisle.material_id = aisle_ids[0] if aisle_ids else images[0].material.material_id
                    aisle.supporting_material_ids = aisle_ids or [aisle.material_id]
                    aisle.cross_image_reasoning = "组图通道证据引用了未上传的图片，需内部复核"
                    aisle.confidence = 0.0
                    aisle.bbox = None
                if aisle.confidence < self._confidence_threshold:
                    aisle.status = "uncertain"
                    aisle.bbox = None
                elif aisle.status == "detected" and aisle.bbox is None:
                    source = next(item for item in images
                                  if item.material.material_id == aisle.material_id)
                    aisle.bbox = self._localize_aisle(source, aisle.evidence_text)
            self._localize_installation_regions(images, analysis)
            return analysis
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
            raise ProviderError("group installation recognition failed") from exc

    def _localize_aisle(self, image: MaterialInput, evidence_text: str) -> Bbox | None:
        """A second look only adds coordinates; it cannot change the group verdict."""
        content = [{"type": "text", "text": (
            "组图已确认可见通道阻塞。只在这张原图定位已确认的阻塞物和受影响通道，"
            "不重新判断风险状态，不框通道外物品。证据：" + evidence_text
            + "。只返回 JSON：{\"bbox\":{\"x_min\":0到1数值,"
              "\"y_min\":0到1数值,\"x_max\":0到1数值,"
              "\"y_max\":0到1数值}或null}。无法可靠定位返回 null，不猜测。")},
            {"type": "image_url", "image_url": {"url":
                f"data:{image.material.media_type};base64,"
                f"{base64.b64encode(image.content).decode('ascii')}"}}]
        body = {"model": self.model_name,
                "messages": [{"role": "user", "content": content}],
                "temperature": 0, "enable_thinking": False,
                "response_format": {"type": "json_object"}}
        try:
            with httpx.Client(
                timeout=httpx.Timeout(self._settings.timeout_seconds,
                                      connect=min(self._settings.timeout_seconds, 15.0)),
                transport=self._transport, trust_env=self._settings.trust_env,
            ) as client:
                response = post_with_connect_retry(
                    client, f"{self._settings.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self._settings.api_key}",
                             "Content-Type": "application/json"}, json=body,
                )
                response.raise_for_status()
            payload = json.loads(self._extract_json_object(
                response.json()["choices"][0]["message"]["content"]))
            if not isinstance(payload, dict) or not isinstance(payload.get("bbox"), dict):
                return None
            return Bbox.model_validate({**payload["bbox"],
                                        "coordinate_space": "normalized_0_1"})
        except (httpx.HTTPError, ValidationError, ValueError, KeyError, IndexError, TypeError):
            return None

    def _localize_installation_regions(
        self, images: list[MaterialInput], analysis: InstallationAnalysis,
    ) -> None:
        """Only fill missing visual locations; never alter the underwriting finding."""
        area = analysis.areas[0]
        if area.status != "confirmed":
            return
        missing = [(index, region) for index, region in enumerate(area.evidence_regions)
                   if region.bbox is None]
        if not missing:
            return
        image_by_id = {item.material.material_id: item for item in images}
        content: list[dict[str, Any]] = [{
            "type": "text",
            "text": self._stage_prompt(
                "installation_localization",
                EVIDENCE=json.dumps([
                    {"evidence_index": index, "material_id": region.material_id,
                     "description": region.description}
                    for index, region in missing
                ], ensure_ascii=False),
            ),
        }]
        for material_id in dict.fromkeys(region.material_id for _, region in missing):
            item = image_by_id.get(material_id)
            if item is None:
                continue
            content.extend([
                {"type": "text", "text": f"图片编号：{material_id}"},
                {"type": "image_url", "image_url": {"url":
                    f"data:{item.material.media_type};base64,"
                    f"{base64.b64encode(item.content).decode('ascii')}"}},
            ])
        if len(content) == 1:
            return
        body = {"model": self.model_name,
                "messages": [{"role": "user", "content": content}],
                "temperature": 0, "enable_thinking": False,
                "response_format": {"type": "json_object"}}
        try:
            with httpx.Client(
                timeout=httpx.Timeout(self._settings.timeout_seconds,
                                      connect=min(self._settings.timeout_seconds, 15.0)),
                transport=self._transport, trust_env=self._settings.trust_env,
            ) as client:
                response = post_with_connect_retry(
                    client, f"{self._settings.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self._settings.api_key}",
                             "Content-Type": "application/json"}, json=body,
                )
                response.raise_for_status()
            raw = response.json()["choices"][0]["message"]["content"]
            payload = json.loads(self._extract_json_object(raw))
            if not isinstance(payload, dict) or not isinstance(payload.get("regions"), list):
                return
            expected = {index: region for index, region in missing}
            for located in payload["regions"]:
                if not isinstance(located, dict):
                    continue
                index = located.get("evidence_index")
                region = expected.get(index) if isinstance(index, int) else None
                if region is None or located.get("material_id") != region.material_id:
                    continue
                bbox = located.get("bbox")
                if isinstance(bbox, dict):
                    try:
                        region.bbox = Bbox.model_validate({
                            **bbox, "coordinate_space": "normalized_0_1",
                        })
                    except ValueError:
                        continue
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
            # Localization is optional evidence metadata; the scene judgment remains valid.
            return

    def estimate_geometry(self, materials: list[MaterialInput],
                          installation: InstallationAnalysis) -> GeometryAnalysis:
        images = [item for item in materials if item.material.media_type in SUPPORTED_IMAGE_TYPES]
        if not images:
            return super().estimate_geometry(materials, installation)
        content: list[dict[str, Any]] = [{
            "type": "text",
            "text": self._stage_prompt(
                "geometry",
                INSTALLATION_ANALYSIS=installation.model_dump_json(
                    exclude={"provider", "model"}
                ),
            ),
        }]
        for item in images:
            content.append({"type": "text", "text": f"图片编号：{item.material.material_id}"})
            content.append({"type": "image_url", "image_url": {
                "url": f"data:{item.material.media_type};base64,"
                       f"{base64.b64encode(item.content).decode('ascii')}",
            }})
        body = {"model": self.model_name, "messages": [{"role": "user", "content": content}],
                "temperature": 0, "enable_thinking": False,
                "response_format": {"type": "json_object"}}
        try:
            with httpx.Client(
                timeout=httpx.Timeout(self._settings.timeout_seconds,
                                      connect=min(self._settings.timeout_seconds, 15.0)),
                transport=self._transport, trust_env=self._settings.trust_env,
            ) as client:
                response = post_with_connect_retry(
                    client, f"{self._settings.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self._settings.api_key}",
                             "Content-Type": "application/json"}, json=body,
                )
                response.raise_for_status()
            payload = json.loads(self._extract_json_object(
                response.json()["choices"][0]["message"]["content"]))
            result = GeometryAnalysis.model_validate({
                **payload, "provider": self.name, "model": self.model_name,
            })
            area_ids = {area.area_id for area in installation.areas}
            image_ids = {item.material.material_id for item in images}
            if len(result.areas) != 1 or {area.area_id for area in result.areas} != area_ids or any(
                not set(metric.material_ids).issubset(image_ids)
                for area in result.areas for metric in (
                    area.tilt_degrees, area.height_above_ground_m, area.ground_roughness)
            ):
                raise ValueError("invalid geometry evidence references")
            return result
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
            raise ProviderError("group geometry estimation failed") from exc

    def analyze_with_watermark(
        self,
        material_input: MaterialInput,
    ) -> VisionAnalysis:
        material = material_input.material

        if material.media_type not in SUPPORTED_IMAGE_TYPES:
            return VisionAnalysis(findings=[])

        if not material_input.content:
            raise ProviderError(
                "vision provider received an empty image"
            )

        try:
            response_payload = self._request_model(material_input)
        except ProviderError as exc:
            # Only retry a malformed response; never turn failed validation into findings.
            if not str(exc).startswith(('vision provider returned invalid response fields:',
                                        'vision provider returned an invalid response')):
                raise
            response_payload = self._request_model(material_input, repair_hint=str(exc))

        if material.category.value == "panorama" and (not getattr(self, "rule_policy", None) or self.rule_policy.enabled("shading")):
            response_payload = self._verify_panorama_edge_shading(
                material_input, response_payload,
            )

        return VisionAnalysis(
            findings=self._to_risk_findings(
                material_id=material.material_id,
                response_payload=response_payload,
            ),
            watermark_bbox=response_payload.watermark_bbox,
            watermark_evidence=response_payload.watermark_evidence,
            watermark_present=(
                response_payload.watermark_present
                if material.category in SCENE_PHOTO_CATEGORIES else None
            ),
        )

    def _verify_panorama_edge_shading(
        self,
        material_input: MaterialInput,
        response_payload: VisionResponsePayload,
    ) -> VisionResponsePayload:
        """Recheck small panel-edge occlusions in enlarged, traceable crops."""
        shading = next(
            (item for item in response_payload.findings
             if item.category is RiskCategory.MINOR_SHADING),
            None,
        )
        if shading is None or shading.detection_status is not DetectionStatus.NOT_DETECTED:
            return response_payload
        if any(item.category is RiskCategory.SEVERE_SHADING
               and item.detection_status is DetectionStatus.DETECTED
               for item in response_payload.findings):
            return response_payload

        try:
            source = Image.open(BytesIO(material_input.content))
            source.load()
        except (UnidentifiedImageError, OSError, ValueError):
            return response_payload
        width, height = source.size
        if width < 400 or height < 300:
            return response_payload

        focus_prompt = (
            Path(__file__).resolve().parents[2]
            / "prompts" / "vision" / "shading_focus.txt"
        ).read_text(encoding="utf-8")
        if getattr(self, "rule_policy", None):
            focus_prompt = self.rule_policy.snapshot["shading_focus_prompt"]
        for x_start, x_end in ((0.0, 0.55), (0.45, 1.0)):
            y_start, y_end = 0.12, 0.78
            region = (
                round(x_start * width), round(y_start * height),
                round(x_end * width), round(y_end * height),
            )
            cropped = source.crop(region).convert("RGB")
            output = BytesIO()
            cropped.save(output, format="PNG")
            crop_material = material_input.material.model_copy(
                update={"media_type": "image/png"},
            )
            try:
                focused = self._request_model(
                    MaterialInput(material=crop_material, content=output.getvalue()),
                    focus_prompt=focus_prompt,
                )
            except ProviderError as exc:
                if not str(exc).startswith((
                    "vision provider returned invalid response fields:",
                    "vision provider returned an invalid response",
                )):
                    continue
                try:
                    focused = self._request_model(
                        MaterialInput(material=crop_material, content=output.getvalue()),
                        focus_prompt=focus_prompt,
                        repair_hint=str(exc),
                    )
                except ProviderError:
                    continue
            candidate = next(
                (item for item in focused.findings
                 if item.category is RiskCategory.MINOR_SHADING
                 and item.detection_status is DetectionStatus.DETECTED
                 and item.confidence >= self._confidence_threshold
                 and item.bbox is not None),
                None,
            )
            if candidate is None:
                continue
            box = candidate.bbox
            mapped = Bbox(
                x_min=x_start + box.x_min * (x_end - x_start),
                y_min=y_start + box.y_min * (y_end - y_start),
                x_max=x_start + box.x_max * (x_end - x_start),
                y_max=y_start + box.y_max * (y_end - y_start),
                coordinate_space="normalized_0_1",
            )
            replacement = candidate.model_copy(update={
                "bbox": mapped,
                "evidence_text": "局部放大复核：" + candidate.evidence_text,
            })
            return response_payload.model_copy(update={
                "findings": [
                    replacement if item is shading else item
                    for item in response_payload.findings
                ],
            })
        return response_payload

    def _request_model(
        self,
        material_input: MaterialInput,
        *, repair_hint: str | None = None,
        focus_prompt: str | None = None,
    ) -> VisionResponsePayload:
        encoded_image = base64.b64encode(
            material_input.content
        ).decode("ascii")

        data_url = (
            f"data:{material_input.material.media_type};"
            f"base64,{encoded_image}"
        )
        quality_preflight = (
            "、".join(material_input.material.quality_issues)
            or "未发现明显预检异常；这不等于图片质量已通过"
        )
        request_body = {
            "model": self.model_name,
            "messages": [
                {
                    "role": "system",
                    "content": focus_prompt if focus_prompt is not None else self.rule_policy.vision_prompt(material_input.material.category) if getattr(self, "rule_policy", None) else self._system_prompt if self._system_prompt is not None else material_prompt(material_input.material.category),
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": ("请只核验这张局部放大图的组件表面遮挡；bbox 坐标相对于本张裁切图。严格返回 JSON。"
                                if focus_prompt is not None else
                                (
                                "请检查这份光伏投保图片。"
                                "材料类别："
                                f"{material_input.material.category.value}。"
                                "文件预检质量提示："
                                f"{quality_preflight}。"
                                "只依据可见证据返回检查状态；核保动作由后续规则处理。"
                                "每项可增加uncertainty_kind：none无疑点、visual视觉不确定、missing关键证据不足、classification证据足但业务分类存疑、conflict同一对象证据矛盾。不得自行制定核保口径。"
                                "可见通道阻塞由相关照片的组图任务单独判断，当前单图不要返回 aisle_obstruction。"
                                "image_quality 表示画质缺陷，不是图片存在：清晰且关键区域可见时返回 not_detected；"
                                "只有明确的模糊、曝光问题、遮挡或关键区域缺失才返回 detected，并说明具体缺陷。"
                                "模拟或测试图标识不等于画质缺陷。状态必须与 evidence_text 一致。"
                                "请按照 JSON 格式返回识别结果。"
                            )),
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": data_url
                            },
                        },
                    ],
                },
            ],
            "temperature": 0,
            "enable_thinking": False,
            "response_format": {
                "type": "json_object",
            },
        }

        if repair_hint:
            request_body['messages'].append({
                'role': 'user',
                'content': '上次输出未通过格式校验：' + repair_hint +
                    '。请重新检查同一图片，严格遵守输出字段和枚举。'
                    '无法可靠定位的 bbox 必须为 null，不能提供零面积框。'
                    '不要编造结论来满足格式。只返回合法 JSON。',
            })

        try:
            with httpx.Client(
                timeout=httpx.Timeout(
                    self._settings.timeout_seconds,
                    connect=min(
                        self._settings.timeout_seconds,
                        15.0,
                    ),
                ),
                transport=self._transport,
                trust_env=self._settings.trust_env,
            ) as client:
                response = post_with_connect_retry(
                    client,
                    (
                        f"{self._settings.base_url}"
                        "/chat/completions"
                    ),
                    headers={
                        "Authorization": (
                            "Bearer "
                            f"{self._settings.api_key}"
                        ),
                        "Content-Type": (
                            "application/json"
                        ),
                    },
                    json=request_body,
                )

                response.raise_for_status()

        except httpx.ConnectTimeout as exc:
            raise ProviderError(
                "vision provider connection timed out"
            ) from exc

        except httpx.ReadTimeout as exc:
            raise ProviderError(
                "vision provider response timed out"
            ) from exc

        except httpx.TimeoutException as exc:
            raise ProviderError(
                "vision provider request timed out"
            ) from exc

        except httpx.ConnectError as exc:
            raise ProviderError(
                "vision provider connection failed"
            ) from exc

        except httpx.HTTPStatusError as exc:
            raise ProviderError(
                "vision provider returned HTTP "
                f"{exc.response.status_code}"
            ) from exc

        except httpx.RequestError as exc:
            raise ProviderError(
                "vision provider request failed"
            ) from exc

        return self._parse_response(response)

    def _parse_response(
        self,
        response: httpx.Response,
    ) -> VisionResponsePayload:
        try:
            response_data: Any = response.json()

            content = response_data[
                "choices"
            ][0]["message"]["content"]

            if not isinstance(content, str):
                raise TypeError

            json_text = self._extract_json_object(
                content
            )

        except (
            json.JSONDecodeError,
            KeyError,
            IndexError,
            TypeError,
        ) as exc:
            raise ProviderError(
                "vision provider returned "
                "an invalid response"
            ) from exc

        try:
            payload = json.loads(json_text)
            if isinstance(payload, dict) and isinstance(payload.get("findings"), list):
                payload["findings"] = [
                    item for item in payload["findings"]
                    if not (
                        isinstance(item, dict)
                        and item.get("category") in RETIRED_RISK_CATEGORIES
                    )
                ]
            return VisionResponsePayload.model_validate(payload)

        except ValidationError as exc:
            summary = self._validation_error_summary(
                exc
            )

            raise ProviderError(
                "vision provider returned invalid "
                f"response fields: {summary}"
            ) from exc

    @staticmethod
    def _extract_json_object(
        content: str,
    ) -> str:
        stripped = content.strip()
        first_brace = stripped.find("{")
        last_brace = stripped.rfind("}")

        if (
            first_brace == -1
            or last_brace <= first_brace
        ):
            raise json.JSONDecodeError(
                "JSON object not found",
                stripped,
                0,
            )

        return stripped[
            first_brace:last_brace + 1
        ]

    @staticmethod
    def _validation_error_summary(
        error: ValidationError,
    ) -> str:
        issues: list[str] = []

        for item in error.errors(
            include_url=False,
            include_context=False,
            include_input=False,
        )[:5]:
            location = ".".join(
                str(part)
                for part in item["loc"]
            )
            issues.append(
                f"{location}:{item['type']}"
            )

        return ", ".join(issues) or "unknown validation error"

    def _to_risk_findings(
        self,
        *,
        material_id: str,
        response_payload: VisionResponsePayload,
    ) -> list[RiskFinding]:
        findings: list[RiskFinding] = []

        for index, item in enumerate(
            response_payload.findings,
            start=1,
        ):
            if item.category is RiskCategory.AISLE_OBSTRUCTION:
                continue
            simulation_only_quality = (
                item.category is RiskCategory.IMAGE_QUALITY
                and item.detection_status in {DetectionStatus.DETECTED, DetectionStatus.UNCERTAIN}
                and simulation_only_quality_claim(item.evidence_text)
            )
            low_confidence = (
                item.confidence
                < self._confidence_threshold
            )

            detection_status = (
                DetectionStatus.NOT_DETECTED if simulation_only_quality
                else DetectionStatus.UNCERTAIN
                if low_confidence and item.detection_status is not DetectionStatus.NOT_APPLICABLE
                else item.detection_status
            )

            findings.append(
                RiskFinding(
                    finding_id=(
                        f"{material_id}:"
                        f"{item.category.value}:"
                        f"{index}"
                    ),
                    material_id=material_id,
                    category=item.category,
                    label=item.label,
                    detection_status=(
                        detection_status
                    ),
                    severity=RiskSeverity.INFO if simulation_only_quality else item.severity,
                    confidence=item.confidence,
                    environment_relation=item.environment_relation,
                    evidence_coverage=item.evidence_coverage,
                    uncertainty_kind=item.uncertainty_kind,
                    area_id=item.area_id,
                    object_id=item.object_id,
                    bbox=None if simulation_only_quality else item.bbox,
                    evidence_text=(
                        "仅识别到模拟/测试标识，模型未提供可见画质缺陷；"
                        "该标识不作为图片质量风险证据。"
                        if simulation_only_quality else item.evidence_text
                    ),
                    provider=self.name,
                    model=self.model_name,
                    requires_manual_review=(
                        (item.requires_manual_review and not simulation_only_quality)
                        or detection_status
                        is DetectionStatus.UNCERTAIN
                    ),
                )
            )

        return findings
