from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)

from app.contracts import (
    Material,
    MaterialCategory,
    MaterialParseStatus,
    MaterialQualityStatus,
    OcrField,
)
from app.intake import (
    MAX_FILE_SIZE_BYTES,
    inspect_file,
    read_upload_limited,
)
from app.providers import (
    CompatibleOcrProvider,
    MaterialInput,
    OcrProvider,
    PdfPageOcrProvider,
    ProviderError,
)
from app.providers.routing import (
    RoutedOcrProvider,
)
from app.settings import (
    ProviderConfigurationError,
    VlmSettings,
)

router = APIRouter()

OCR_MEDIA_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/png",
}


def get_ocr_provider() -> OcrProvider:
    """根据本地环境配置创建支持图片和 PDF 的真实 OCR Provider。"""

    try:
        settings = VlmSettings.from_environment()
    except ProviderConfigurationError as exc:
        raise HTTPException(
            status_code=(
                status.HTTP_503_SERVICE_UNAVAILABLE
            ),
            detail="OCR provider is not configured",
        ) from exc

    image_provider = CompatibleOcrProvider(
        settings=settings.for_stage("ocr"),
    )

    pdf_provider = PdfPageOcrProvider(
        delegate=image_provider,
    )

    return RoutedOcrProvider(
        delegate=pdf_provider,
        filing_delegate=PdfPageOcrProvider(
            delegate=CompatibleOcrProvider(settings=settings.for_stage("filing")),
        ),
    )


@router.post(
    "/api/v1/ocr/extract",
    response_model=list[OcrField],
    tags=["ocr"],
)
async def extract_uploaded_image_text(
    ocr_provider: Annotated[
        OcrProvider,
        Depends(get_ocr_provider),
    ],
    file: Annotated[
        UploadFile,
        File(
            description=(
                "需要进行文字识别和字段提取的 "
                "JPEG、PNG 或 PDF 文件"
            )
        ),
    ],
    material_id: Annotated[
        str,
        Form(
            min_length=1,
            description="材料唯一编号",
        ),
    ] = "material-upload-001",
    category: Annotated[
        MaterialCategory,
        Form(
            description="材料类别",
        ),
    ] = MaterialCategory.COMPONENT_NAMEPLATE,
) -> list[OcrField]:
    """检查上传材料并调用真实模型提取 OCR 字段。"""

    try:
        content = await read_upload_limited(file)
    finally:
        await file.close()

    inspection = inspect_file(
        file_name=file.filename,
        declared_media_type=file.content_type,
        content=content,
    )

    if inspection.status != "accepted":
        if "file_too_large" in inspection.issues:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=f"Maximum file size is {MAX_FILE_SIZE_BYTES} bytes",
            )
        raise HTTPException(
            status_code=(
                status.HTTP_422_UNPROCESSABLE_CONTENT
            ),
            detail={
                "message": "Uploaded file was rejected",
                "issues": inspection.issues,
            },
        )

    if inspection.media_type not in OCR_MEDIA_TYPES:
        raise HTTPException(
            status_code=(
                status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
            ),
            detail=(
                "OCR extraction supports only "
                "JPEG, PNG and PDF files"
            ),
        )

    if (
        inspection.media_type is None
        or inspection.sha256 is None
    ):
        raise HTTPException(
            status_code=(
                status.HTTP_422_UNPROCESSABLE_CONTENT
            ),
            detail=(
                "Uploaded material could not be inspected"
            ),
        )

    material = Material(
        material_id=material_id,
        category=category,
        file_name=inspection.file_name,
        media_type=inspection.media_type,
        sha256=inspection.sha256,
        quality_status=(
            MaterialQualityStatus.UNKNOWN
        ),
        quality_confidence=None,
        quality_issues=inspection.issues,
        parse_status=MaterialParseStatus.SUCCESS,
    )

    material_input = MaterialInput(
        material=material,
        content=content,
    )

    try:
        return ocr_provider.extract(
            material_input
        )
    except ProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="OCR provider request failed",
        ) from exc
