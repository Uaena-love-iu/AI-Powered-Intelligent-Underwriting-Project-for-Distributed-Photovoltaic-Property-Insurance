import base64
import json
import math
from typing import Any, Self

import httpx
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from app.contracts import (
    Bbox,
    FilingProhibitionAssessment,
    MaterialCategory,
    OcrField,
    OcrValueStatus,
)
from app.settings import VlmSettings

from ..common import (
    MaterialInput,
    ProviderError,
    post_with_connect_retry,
)
from .base import OcrProvider

SUPPORTED_IMAGE_TYPES = {
    "image/jpeg",
    "image/png",
}

DEFAULT_CONFIDENCE_THRESHOLD = 0.65

BBOX_COORDINATE_FIELDS = (
    "x_min",
    "y_min",
    "x_max",
    "y_max",
)

CATEGORY_FIELD_GUIDANCE = {
    MaterialCategory.PROJECT_DOCUMENT: (
        "先识别可见文档标题和文档种类。完整提取 project_name、site_address、"
        "construction_content（建设内容及规模的连续原文，不改写为结论）；"
        "建设内容栏目明确未载明时，返回 construction_content 的 missing 状态；"
        "栏目存在但不可读时，返回 uncertain 状态并说明原因。"
    ),
    MaterialCategory.PANORAMA: (
        "视觉模型已确认现场水印存在；仅提取水印中的 "
        "watermark_date（YYYY-MM-DD）、watermark_longitude、watermark_latitude、"
        "watermark_address（仅水印确有文字地址时提取）。"
        "watermark_date 的 bbox 只紧贴日期字符，不框整条日期与经纬度水印；"
        "日期可读但位置无法可靠定位时 bbox=null。坐标或日期看不清时不要猜。"
    ),
    MaterialCategory.FILING_CERTIFICATE: (
        "重点提取 project_name、project_entity、insured_name、site_address、insured_address、"
        "province、city、district 等项目备案信息；完整提取 construction_content"
        "（建设内容及规模的连续原文），不要只摘关键词。"
        "建设内容栏目明确未载明时，返回 construction_content 的 missing 状态；"
        "栏目存在但不可读时，返回 uncertain 状态并说明原因。"
        "若备案证列出组件型号，也提取 component_model。"
    ),
    MaterialCategory.GRID_CONNECTION_DOCUMENT: (
        "重点提取 project_name、insured_name、project_entity、"
        "site_address、grid_connection_date 等并网信息。"
    ),
    MaterialCategory.COMPONENT_NAMEPLATE: (
        "重点提取 component_model、manufacturer、rated_power_w、"
        "maximum_system_voltage_v、serial_number 等组件铭牌信息。"
    ),
    MaterialCategory.INVERTER_NAMEPLATE: (
        "重点提取 inverter_model、manufacturer、rated_power_w、"
        "maximum_input_voltage_v、serial_number 等逆变器铭牌信息。"
    ),
    MaterialCategory.ELECTRICAL_GROUNDING: (
        "接地照片只提取实际可见的设备标识或文字；没有检测记录时不要求电阻值。"
        "若材料明确为接地检测记录，再提取 grounding_resistance_ohm、inspection_date、"
        "inspection_result。禁止根据接地排外观推算电阻。"
    ),
    MaterialCategory.MONITORING_OPTIONAL: (
        "如材料直接证明监控系统存在或运行状态，提取 monitoring_present、"
        "monitoring_model、online_status、coverage_period；不能从宣传文案推断实际在线。"
    ),
    MaterialCategory.COMBINER_BOX: (
        "重点提取 combiner_box_model、manufacturer、rated_voltage_v、"
        "rated_current_a、serial_number 等汇流箱信息。"
    ),
}

DEFAULT_FIELD_GUIDANCE = (
    "只提取图片中清晰可见、与光伏财产险核保有关的文字字段。"
)

SYSTEM_PROMPT = """你是分布式光伏财产险的材料 OCR 与字段提取模块。

只允许根据图片中直接可见的文字提取信息，不得猜测、补全或引用图片外的信息。
必须返回一个合法 JSON 对象，顶层字段固定为 fields。
fields 必须是数组。

每个字段只能包含：
field_name、raw_value、normalized_value、value_status、confidence、
bbox、evidence_text；备案证的 project_name 和 construction_content
还可以包含 filing_assessment。

字段要求：
- field_name 使用简短的英文 snake_case。
- 按本次任务范围提取：拍摄水印任务只读拍摄信息，业务文字任务只读材料对应的业务字段。
  无法确认时返回 uncertain，不混合两类任务。
- 每个语义字段最多返回一次。
- raw_value 保存图片中直接读取到的原文。
- normalized_value 保存标准化后的值；无法可靠标准化时与 raw_value 相同。
- value_status 通常为 extracted 或 uncertain；备案关键字段允许 missing。
- confidence 必须是 0 到 1 之间的小数。
- evidence_text 应包含支持该字段的简短原文或位置说明。
- 无法识别或图片中不存在的字段不要返回。
- 不要为了凑齐字段而返回 null 字段。
- 不得虚构型号、厂商、功率、日期、地址或单位。

数值标准化要求：
- 功率字段统一为数字字符串，单位由 field_name 表示，例如 rated_power_w。
- 电压字段统一为数字字符串，单位由 field_name 表示，例如 rated_voltage_v。
- 电流字段统一为数字字符串，单位由 field_name 表示，例如 rated_current_a。
- 日期优先标准化为 YYYY-MM-DD；无法确定完整日期时保留原文并标记 uncertain。
- 型号、编号和企业名称不得擅自改写。

bbox 无法可靠定位时必须返回 null。
bbox 能够可靠定位时必须包含：
x_min、y_min、x_max、y_max、coordinate_space。
拍摄水印中的 watermark_date 若可读且可定位，bbox 应只框日期文字，
不能把后面的经纬度一起框入。
坐标必须是 0 到 1 之间的归一化小数。
coordinate_space 必须固定为 normalized_0_1。
如果不能返回正确的归一化坐标，请将 bbox 返回为 null。
不得返回像素坐标。

如果没有识别到可靠字段，返回：
{"fields": []}

不要返回 Markdown。
不要返回代码块。
不要返回 JSON 以外的说明。
"""


class OcrFieldPayload(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    field_name: str = Field(min_length=1)
    raw_value: str | None = None
    normalized_value: str | None = None
    value_status: OcrValueStatus
    confidence: float = Field(
        ge=0.0,
        le=1.0,
    )
    bbox: Bbox | None = None
    evidence_text: str | None = None
    filing_assessment: FilingProhibitionAssessment | None = None

    @field_validator(
        "raw_value",
        "normalized_value",
        "evidence_text",
        mode="before",
    )
    @classmethod
    def coerce_scalar_text_values(
        cls,
        value: Any,
    ) -> Any:
        if value is None or isinstance(value, str):
            return value

        if isinstance(value, bool):
            return str(value).lower()

        if (
            isinstance(value, (int, float))
            and math.isfinite(value)
        ):
            return str(value)

        return value

    @field_validator(
        "bbox",
        mode="before",
    )
    @classmethod
    def sanitize_optional_bbox(
        cls,
        value: Any,
    ) -> Any:
        if value is None:
            return None

        if not isinstance(value, dict):
            return None

        coordinates: dict[str, float] = {}

        for field_name in BBOX_COORDINATE_FIELDS:
            coordinate = value.get(field_name)

            if (
                isinstance(coordinate, bool)
                or not isinstance(
                    coordinate,
                    (int, float),
                )
            ):
                return None

            normalized_coordinate = float(
                coordinate
            )

            if (
                not math.isfinite(
                    normalized_coordinate
                )
                or normalized_coordinate < 0.0
                or normalized_coordinate > 1.0
            ):
                return None

            coordinates[field_name] = (
                normalized_coordinate
            )

        if (
            coordinates["x_min"]
            >= coordinates["x_max"]
            or coordinates["y_min"]
            >= coordinates["y_max"]
        ):
            return None

        return {
            **coordinates,
            "coordinate_space": (
                "normalized_0_1"
            ),
        }

    @model_validator(mode="after")
    def validate_extracted_value(self) -> Self:
        if (
            self.value_status is OcrValueStatus.EXTRACTED
            and self.raw_value is None
            and self.normalized_value is None
        ):
            raise ValueError(
                "an extracted OCR field must contain a value"
            )

        return self


class OcrResponsePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fields: list[OcrFieldPayload]


class CompatibleOcrProvider(OcrProvider):
    """调用 OpenAI Chat Completions 兼容接口的 OCR Provider。"""

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
        self._confidence_threshold = confidence_threshold

    @property
    def name(self) -> str:
        return "openai-compatible-ocr"

    @property
    def model_name(self) -> str:
        return self._settings.model

    def extract(
        self,
        material_input: MaterialInput,
    ) -> list[OcrField]:
        material = material_input.material

        if material.media_type not in SUPPORTED_IMAGE_TYPES:
            return []

        if not material_input.content:
            raise ProviderError(
                "OCR provider received an empty image"
            )

        response_payload = self._request_model(
            material_input
        )

        return self._to_ocr_fields(
            material_id=material.material_id,
            response_payload=response_payload,
        )

    def _request_model(
        self,
        material_input: MaterialInput,
    ) -> OcrResponsePayload:
        encoded_image = base64.b64encode(
            material_input.content
        ).decode("ascii")

        data_url = (
            f"data:{material_input.material.media_type};"
            f"base64,{encoded_image}"
        )

        field_guidance = CATEGORY_FIELD_GUIDANCE.get(
            material_input.material.category,
            DEFAULT_FIELD_GUIDANCE,
        )
        policy = getattr(self, "rule_policy", None)
        if policy:
            field_guidance = policy.snapshot["ocr_guidance"][material_input.material.category.value]

        if material_input.ocr_task == 'watermark':
            from app.prompts.stages import stage_prompt
            field_guidance = policy.snapshot["stage_prompts"]["watermark_guidance"] if policy else stage_prompt('watermark_guidance')
        elif material_input.ocr_task == "business":
            field_guidance += ' 本次只提取业务文字，不返回任何 watermark_ 开头字段，拍摄水印由独立任务处理。'
        from app.material_catalog import DOCUMENT_CATEGORIES
        from app.prompts.documents import material_prompt
        actual_prompt = self._system_prompt or (material_prompt(material_input.material.category, SYSTEM_PROMPT)
            if material_input.material.category in DOCUMENT_CATEGORIES else SYSTEM_PROMPT)
        if policy:
            actual_prompt = policy.snapshot["ocr_prompts"][material_input.material.category.value]
        if (not policy or policy.enabled("filing")) and material_input.material.category in {
            MaterialCategory.PROJECT_DOCUMENT, MaterialCategory.FILING_CERTIFICATE,
        }:
            from app.prompts.stages import stage_prompt
            actual_prompt += '\n' + (policy.snapshot["stage_prompts"]["filing_assessment"] if policy else stage_prompt('filing_assessment'))
        elif policy:
            actual_prompt += "\n本次不执行备案准入或禁投判断，只提取事实字段，禁止输出 filing_assessment。"
        request_body = {
            "model": self.model_name,
            "messages": [
                {
                    "role": "system",
                    "content": actual_prompt,
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "请识别这份光伏投保材料中的文字并提取结构化字段。"
                                "材料类别："
                                f"{material_input.material.category.value}。"
                                f"{field_guidance}"
                                "请严格按照 JSON 格式返回结果。"
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": data_url,
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
                        "Content-Type": "application/json",
                    },
                    json=request_body,
                )

                response.raise_for_status()

        except httpx.ConnectTimeout as exc:
            raise ProviderError(
                "OCR provider connection timed out"
            ) from exc

        except httpx.ReadTimeout as exc:
            raise ProviderError(
                "OCR provider response timed out"
            ) from exc

        except httpx.TimeoutException as exc:
            raise ProviderError(
                "OCR provider request timed out"
            ) from exc

        except httpx.ConnectError as exc:
            raise ProviderError(
                "OCR provider connection failed"
            ) from exc

        except httpx.HTTPStatusError as exc:
            raise ProviderError(
                "OCR provider returned HTTP "
                f"{exc.response.status_code}"
            ) from exc

        except httpx.RequestError as exc:
            raise ProviderError(
                "OCR provider request failed"
            ) from exc

        return self._parse_response(response)

    def _parse_response(
        self,
        response: httpx.Response,
    ) -> OcrResponsePayload:
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
                "OCR provider returned an invalid response"
            ) from exc

        try:
            return OcrResponsePayload.model_validate_json(
                json_text
            )

        except ValidationError as exc:
            summary = self._validation_error_summary(
                exc
            )

            raise ProviderError(
                "OCR provider returned invalid "
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

        return ", ".join(
            issues
        ) or "unknown validation error"

    def _to_ocr_fields(
        self,
        *,
        material_id: str,
        response_payload: OcrResponsePayload,
    ) -> list[OcrField]:
        best_fields: dict[str, OcrFieldPayload] = {}

        for item in response_payload.fields:
            if (item.value_status is OcrValueStatus.MISSING
                    and item.field_name not in {"construction_content", "project_name"}):
                continue

            current = best_fields.get(
                item.field_name
            )

            if (
                current is None
                or item.confidence
                > current.confidence
            ):
                best_fields[item.field_name] = item

        fields: list[OcrField] = []

        for index, item in enumerate(
            best_fields.values(),
            start=1,
        ):
            low_confidence = (
                item.confidence
                < self._confidence_threshold
            )

            value_status = (
                OcrValueStatus.UNCERTAIN
                if low_confidence
                else item.value_status
            )

            fields.append(
                OcrField(
                    field_id=(
                        f"{material_id}:"
                        f"{item.field_name}:"
                        f"{index}"
                    ),
                    material_id=material_id,
                    field_name=item.field_name,
                    raw_value=item.raw_value,
                    normalized_value=(
                        item.normalized_value
                    ),
                    value_status=value_status,
                    confidence=item.confidence,
                    bbox=item.bbox,
                    page_number=1,
                    filing_assessment=item.filing_assessment,
                    provider=self.name,
                    model=self.model_name,
                    evidence_text=(
                        item.evidence_text
                    ),
                )
            )

        return fields
