"""Image-only category suggestions; confirmation is always required."""
import base64
from io import BytesIO

import httpx
import pymupdf
from PIL import Image, ImageOps
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.intake.equipment_inventory import EquipmentInventoryParseError, parse_equipment_inventory
from app.material_catalog import MATERIAL_CATALOG
from app.prompts.stages import stage_prompt
from app.providers.common import ProviderError, post_with_connect_retry
from app.settings import VlmSettings

GUIDE = {key: value[1] for key, value in MATERIAL_CATALOG.items()}
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class Candidate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    category: str
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(min_length=1, max_length=1000)


class ClassificationPayload(Candidate):
    alternatives: list[Candidate] = Field(default_factory=list, max_length=3)


def classify_image(content: bytes, settings: VlmSettings) -> dict:
    with Image.open(BytesIO(content)) as original:
        picture = ImageOps.exif_transpose(original).convert('RGB')
        picture.thumbnail((1600, 1600))
        output = BytesIO()
        picture.save(output, format='JPEG', quality=90)
    prompt = stage_prompt(
        'classification',
        CATEGORY_GUIDE='；'.join(f'{key}: {value}' for key, value in GUIDE.items()),
    )
    try:
        with httpx.Client(timeout=settings.timeout_seconds, trust_env=settings.trust_env) as client:
            response = post_with_connect_retry(client, settings.base_url + '/chat/completions',
                headers={'Authorization': 'Bearer ' + settings.api_key}, json={
                    'model': settings.model, 'temperature': 0, 'enable_thinking': False,
                    'response_format': {'type': 'json_object'},
                    'messages': [{'role': 'system', 'content': prompt},
                                 {'role': 'user', 'content': [
                                     {'type': 'text', 'text': '推荐这张照片的材料类别并解释可见证据。'},
                                     {'type': 'image_url', 'image_url': {'url':
                                      'data:image/jpeg;base64,' + base64.b64encode(output.getvalue()).decode()}}]}]})
            response.raise_for_status()
        payload = ClassificationPayload.model_validate_json(response.json()['choices'][0]['message']['content'])
        if any(c.category not in GUIDE for c in [payload, *payload.alternatives]):
            raise ValueError('unsupported category')
    except httpx.TimeoutException as exc:
        raise ProviderError('分类服务响应超时，可以重试或手动选择类别。') from exc
    except httpx.ConnectError as exc:
        raise ProviderError('无法连接分类模型服务，请检查服务进程的联网权限、网络和模型地址；修复后重试分类，材料无需重新上传。') from exc
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 403:
            try:
                body = exc.response.json()
            except (ValueError, TypeError):
                body = {}
            error = body.get('error', {}) if isinstance(body, dict) else {}
            if isinstance(error, dict) and error.get('code') == 'AllocationQuota.FreeTierOnly':
                raise ProviderError(
                    '分类模型的免费调用额度已用尽。请在模型服务控制台检查额度和付费设置；'
                    '启用付费调用可能产生费用。也可手动确认材料类别。'
                ) from exc
        raise ProviderError(f'分类服务返回 HTTP {exc.response.status_code}，请重试或手动选择类别。') from exc
    except (httpx.HTTPError, ValidationError, ValueError, KeyError, IndexError, TypeError) as exc:
        raise ProviderError('自动分类失败或输出格式无效，请重试或手动选择类别。') from exc
    candidates = [payload.model_dump(exclude={'alternatives'})]
    for candidate in payload.alternatives:
        if candidate.category not in {c['category'] for c in candidates}:
            candidates.append(candidate.model_dump())
    return {'category': payload.category, 'suggested_category': payload.category,
            'confidence': payload.confidence, 'reason': payload.reason,
            'candidates': candidates, 'requires_confirmation': True,
            'model': settings.model, 'capture_view': 'unknown'}


def classify_material(content: bytes, media_type: str, settings: VlmSettings | None) -> dict:
    """Classify every supported upload from its content, before underwriting tasks."""
    if media_type in {"image/jpeg", "image/png"}:
        if settings is None:
            raise ProviderError("图片分类模型未配置")
        return classify_image(content, settings)
    if media_type == XLSX_MEDIA_TYPE:
        try:
            rows = parse_equipment_inventory(material_id="classification", content=content)
        except EquipmentInventoryParseError:
            category, confidence, reason = "other", 0.65, "工作簿没有可确认的设备清单表格，请人工选择材料类别"
        else:
            category, confidence, reason = "equipment_inventory", 0.99, f"工作簿含 {len(rows)} 条可解析的设备清单记录"
        return {"category": category, "suggested_category": category,
                "confidence": confidence, "reason": reason,
                "candidates": [{"category": category, "confidence": confidence, "reason": reason}],
                "requires_confirmation": True, "model": "xlsx-parser", "capture_view": "unknown"}
    if media_type == "application/pdf":
        if settings is None:
            raise ProviderError("PDF 分类模型未配置")
        try:
            with pymupdf.open(stream=content, filetype="pdf") as document:
                if document.needs_pass or document.page_count < 1:
                    raise ProviderError("PDF 无法读取页面，请检查文件")
                pages = [document.load_page(i).get_pixmap(dpi=144, alpha=False).tobytes("png")
                         for i in range(min(document.page_count, 3))]
        except ProviderError:
            raise
        except (OSError, RuntimeError, ValueError) as exc:
            raise ProviderError("PDF 分类时无法渲染页面") from exc
        best = None
        for page_number, page in enumerate(pages, start=1):
            result = classify_image(page, settings)
            result["reason"] = f"第 {page_number} 页：{result['reason']}"
            if best is None or (best["category"] == "other" and result["category"] != "other") or (
                best["category"] == result["category"] and result["confidence"] > best["confidence"]
            ):
                best = result
            if result["category"] != "other":
                break
        if best is not None:
            return best
    raise ProviderError("暂不支持该文件类型的材料分类")
