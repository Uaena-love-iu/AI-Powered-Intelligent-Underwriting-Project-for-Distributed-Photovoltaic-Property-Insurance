"""Isolated prompt experiments; only explicit promotion changes the live prompt."""
import hashlib
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.contracts import Material, MaterialCategory, ProjectInfo, RiskCategory, WatermarkStatus
from app.contracts.enums import RETIRED_RISK_CATEGORIES
from app.intake import inspect_file, read_upload_limited
from app.material_catalog import DOCUMENT_CATEGORIES, MATERIAL_CATALOG, canonical_category
from app.prompts.documents import VERSION as DOCUMENT_VERSION
from app.prompts.documents import compose as compose_document
from app.prompts.documents import load as load_documents
from app.prompts.simulation import ensure_simulation_rule
from app.prompts.vision import (
    ROOT,
    builtin_bundle,
    compose_prompt,
    expected_checks,
    load_bundle,
    output_contract,
)
from app.providers import MaterialInput, ProviderError
from app.providers.ocr.compatible import SYSTEM_PROMPT as OCR_CONTRACT
from app.providers.ocr.compatible import CompatibleOcrProvider
from app.providers.ocr.tasks import extract_tasks
from app.providers.routing import VISION_MATERIAL_CATEGORIES
from app.providers.vision.compatible import (
    PROMPT_DEFAULT_PATH,
    SYSTEM_PROMPT,
    CompatibleVisionProvider,
)
from app.rules.engine import RuleEngine
from app.settings import ProviderConfigurationError, VlmSettings

logger = logging.getLogger(__name__)
router = APIRouter()
FRONTEND = Path(__file__).resolve().parents[3] / 'frontend'
LAB_CONTRACT = """
最终输出顶层仅允许 findings 和 watermark_present。
只定位风险或疑似风险部位，不定位水印；不输出水印文字。
已发现且能可靠定位的风险必须提供 bbox，无法定位时返回 null，不得猜位置。
"""


@router.get('/vision-lab', include_in_schema=False)
def page():
    return FileResponse(FRONTEND / 'vision-lab.html')


@router.get('/vision-lab.js', include_in_schema=False)
def script():
    return FileResponse(FRONTEND / 'vision-lab.js', media_type='application/javascript')


def expected(category):
    return expected_checks(category)


@router.get('/api/v1/vision/lab/config')
def config():
    try:
        model = VlmSettings.from_environment().for_stage('vision').model
    except ProviderConfigurationError:
        model = None
    bundle = load_bundle(PROMPT_DEFAULT_PATH)
    legacy = (ROOT / 'legacy_v1.txt').read_text(encoding='utf-8').replace(
        '{RISK_CATEGORY_VALUES}', '、'.join(
            c.value for c in RiskCategory if c not in RETIRED_RISK_CATEGORIES
        ),
    )
    return {'model': model, **bundle, 'original_prompt': SYSTEM_PROMPT,
            'builtin_bundle': builtin_bundle(), 'legacy_prompt': legacy, 'contract': output_contract(), 'legacy_contract': LAB_CONTRACT,
            'categories': {c: expected(MaterialCategory(c)) for c in MATERIAL_CATALOG},
            'material_types': [{'category':c,'label':v[0], 'pipeline':'document' if MaterialCategory(c) in DOCUMENT_CATEGORIES else 'vision'} for c,v in MATERIAL_CATALOG.items()],
            'document_contract': OCR_CONTRACT, 'document_version_name': DOCUMENT_VERSION,
            **load_documents(PROMPT_DEFAULT_PATH)}


class PromptUpdate(BaseModel):
    prompt: str = Field(min_length=30, max_length=30000)
    specialized_prompts: dict[MaterialCategory, str] | None = None
    document_prompt: str | None = Field(default=None, min_length=30, max_length=30000)
    document_specialized_prompts: dict[str, str] | None = None
    version_name: str = Field(default="自定义组合版本", min_length=1, max_length=80)


@router.post('/api/v1/vision/lab/default')
def promote(data: PromptUpdate):
    PROMPT_DEFAULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp = PROMPT_DEFAULT_PATH.with_suffix(f'.{uuid4().hex}.tmp')
    bundle = load_bundle(PROMPT_DEFAULT_PATH)
    bundle['prompt'] = data.prompt
    bundle['version_name'] = data.version_name
    if data.specialized_prompts is not None:
        for category, value in data.specialized_prompts.items():
            if category not in VISION_MATERIAL_CATEGORIES or not 1 <= len(value) <= 12000:
                raise HTTPException(422, '专属提示词类别无效或长度不在 1 到 12000 字符之间')
            bundle['specialized_prompts'][category.value] = value
    bundle.update(load_documents(PROMPT_DEFAULT_PATH))
    if data.document_prompt is not None:
        bundle['document_prompt'] = data.document_prompt
    if data.document_specialized_prompts is not None:
        for category, value in data.document_specialized_prompts.items():
            if category not in {'project_document','equipment_inventory'} or not 1 <= len(value) <= 12000:
                raise HTTPException(422, '文档专属提示词类别无效或长度超限')
            bundle['document_specialized_prompts'][category] = value
    temp.write_text(json.dumps(bundle, ensure_ascii=False), encoding='utf-8')
    temp.replace(PROMPT_DEFAULT_PATH)
    return {'status': 'saved'}


@router.post('/api/v1/vision/lab/analyze')
async def analyze(
    file: Annotated[UploadFile, File()],
    category: Annotated[MaterialCategory, Form()],
    prompt: Annotated[str, Form(min_length=30, max_length=30000)],
    specialized_prompt: Annotated[
        str | None,
        Form(min_length=1, max_length=12000),
    ] = None,
    extract_text: Annotated[bool, Form()] = False,
):
    if category not in VISION_MATERIAL_CATEGORIES:
        raise HTTPException(422, '请选择支持视觉识别的照片类别')
    try:
        content = await read_upload_limited(file)
    finally:
        await file.close()
    inspection = inspect_file(file_name=file.filename, declared_media_type=file.content_type,
                              content=content)
    if inspection.status != 'accepted' or inspection.media_type not in {'image/jpeg', 'image/png'}:
        raise HTTPException(422, '请上传有效的 JPEG 或 PNG 图片，大小不超过 20 MB')
    try:
        settings = VlmSettings.from_environment().for_stage('vision')
    except ProviderConfigurationError as exc:
        raise HTTPException(503, '模型服务未配置') from exc
    material = Material(material_id='lab-' + uuid4().hex, category=category,
                        file_name=inspection.file_name, media_type=inspection.media_type,
                        quality_status=inspection.quality_status or 'unknown',
                        quality_issues=inspection.quality_issues, parse_status='success')
    actual_prompt = (compose_prompt(prompt, specialized_prompt, category)
                     if specialized_prompt is not None
                     else ensure_simulation_rule(prompt + '\n' + LAB_CONTRACT))
    provider = CompatibleVisionProvider(settings, system_prompt=actual_prompt)
    started = perf_counter()
    try:
        result = await run_in_threadpool(provider.analyze_with_watermark,
                                        MaterialInput(material=material, content=content))
    except ProviderError as exc:
        reason = str(exc)
        logger.warning('Vision lab failed (%s): %s', category.value, reason)
        if 'timed out' in reason:
            detail = '模型服务响应超时，请稍后重试；无需修改提示词。'
        elif 'connection' in reason or 'request failed' in reason:
            detail = '无法连接模型服务，请检查网络或代理后重试。'
        elif 'HTTP ' in reason:
            code = reason.rsplit('HTTP ', 1)[-1]
            detail = f'模型服务返回 HTTP {code}，请稍后重试。'
        else:
            detail = '模型输出未通过格式校验，自动重试后仍失败。' + reason
        raise HTTPException(502, detail) from exc
    text_result = {}
    if extract_text:
        material.watermark_status = (WatermarkStatus.PRESENT if result.watermark_present is True else
                                     WatermarkStatus.ABSENT if result.watermark_present is False else WatermarkStatus.UNCERTAIN)
        ocr_settings = VlmSettings.from_environment().for_stage('ocr')
        fields, executions = await run_in_threadpool(extract_tasks, CompatibleOcrProvider(ocr_settings),
                                                     MaterialInput(material=material, content=content))
        text_result = {'ocr_fields': [f.model_dump(mode='json') for f in fields],
                       'ocr_task_executions': executions}
    return {'id': uuid4().hex, 'created_at': datetime.now(UTC).isoformat(),
            'model': settings.model, 'category': category, 'file_name': inspection.file_name,
            'image_hash': hashlib.sha256(content).hexdigest(),
            'prompt': prompt, 'specialized_prompt': specialized_prompt,
            'prompt_mode': 'composed' if specialized_prompt is not None else 'legacy',
            'actual_prompt': actual_prompt,
            'user_context': {'category': category, 'quality_issues': material.quality_issues},
            'elapsed_ms': round((perf_counter() - started) * 1000),
            'expected_checks': expected(category), **result.model_dump(mode='json'), **text_result}


@router.post('/api/v1/vision/lab/classify')
async def classify(file: Annotated[UploadFile, File()]):
    """Recommend a category without applying it to any case or running risk analysis."""
    from app.providers.vision.classification import classify_material
    try:
        content = await read_upload_limited(file)
    finally:
        await file.close()
    inspection = inspect_file(file_name=file.filename, declared_media_type=file.content_type,
                              content=content)
    if inspection.status != 'accepted' or inspection.media_type not in {
        'image/jpeg', 'image/png', 'application/pdf',
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    }:
        raise HTTPException(422, '请上传有效的 JPEG、PNG、PDF 或 XLSX 文件，大小不超过 20 MB')
    settings = None
    if inspection.media_type != 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet':
        try:
            settings = VlmSettings.from_environment().for_stage('classification')
        except ProviderConfigurationError as exc:
            raise HTTPException(503, '模型服务未配置，请手动选择材料类别') from exc
    try:
        result = await run_in_threadpool(classify_material, content, inspection.media_type, settings)
        result['requires_confirmation'] = False
        return result
    except ProviderError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.post('/api/v1/vision/lab/document/analyze')
async def analyze_document(
    file: Annotated[UploadFile, File()],
    category: Annotated[MaterialCategory, Form()],
    prompt: Annotated[str, Form(min_length=30, max_length=30000)],
    specialized_prompt: Annotated[str, Form(min_length=1, max_length=12000)],
):
    if category not in DOCUMENT_CATEGORIES:
        raise HTTPException(422, '请选择项目证照／协议或设备清单')
    try:
        content = await read_upload_limited(file)
    finally:
        await file.close()
    inspection = inspect_file(file_name=file.filename, declared_media_type=file.content_type, content=content)
    if inspection.status != 'accepted' or inspection.media_type not in {'image/jpeg','image/png'}:
        raise HTTPException(422, '文档实验请上传有效 JPEG 或 PNG；PDF 请在材料测试中处理')
    try:
        settings = VlmSettings.from_environment().for_stage(
            'filing' if category is MaterialCategory.PROJECT_DOCUMENT else 'ocr'
        )
    except ProviderConfigurationError as exc:
        raise HTTPException(503, '模型服务未配置') from exc
    material = Material(material_id='lab-'+uuid4().hex, category=category, file_name=inspection.file_name,
                        media_type=inspection.media_type, quality_status=inspection.quality_status or 'unknown',
                        quality_issues=inspection.quality_issues, parse_status='success')
    actual_prompt = compose_document(prompt,specialized_prompt,category,OCR_CONTRACT)
    started = perf_counter()
    try:
        fields = await run_in_threadpool(CompatibleOcrProvider(settings,system_prompt=actual_prompt).extract,
                                        MaterialInput(material=material,content=content,ocr_task="business"))
        fields = [f.model_copy(update={"extraction_task":"business"}) for f in fields if not f.field_name.startswith("watermark_")]
    except ProviderError as exc:
        raise HTTPException(502, '文档字段提取失败：'+str(exc)) from exc
    from app.pipeline.core import UnderwritingPipeline
    resolved = UnderwritingPipeline._apply_ocr_metadata([MaterialInput(material=material,content=content)],fields)[0].material
    reviews = RuleEngine().evaluate_materials([resolved],ocr_fields=fields,project=ProjectInfo(project_name=None,insured_name=None,project_type='unknown',installation_type='unknown',site_address=None,longitude=None,latitude=None,proposed_start_date=None,component_model=None))
    return {'id':uuid4().hex,'created_at':datetime.now(UTC).isoformat(),'model':settings.model,
            'category':canonical_category(category),'resolved_category':resolved.category.value,'pipeline':'document',
            'file_name':inspection.file_name,'image_hash':hashlib.sha256(content).hexdigest(),
            'prompt':prompt,'specialized_prompt':specialized_prompt,'prompt_mode':'composed',
            'actual_prompt':actual_prompt,'elapsed_ms':round((perf_counter()-started)*1000),
            'expected_checks':[],'findings':[],'watermark_present':None,
            'ocr_fields':[f.model_dump(mode='json') for f in fields],
            'material_reviews':[r.model_dump(mode='json') for r in reviews]}
