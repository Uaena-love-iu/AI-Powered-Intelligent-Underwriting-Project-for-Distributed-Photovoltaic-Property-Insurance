from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import Literal
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

import pymupdf
from PIL import Image, ImageFilter, ImageStat, UnidentifiedImageError
from pydantic import Field

from app.contracts import ContractModel, MaterialQualityStatus

MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024
MAX_FILES_PER_REQUEST = 20
MAX_TOTAL_UPLOAD_SIZE_BYTES = 100 * 1024 * 1024
MAX_XLSX_EXPANDED_SIZE_BYTES = 64 * 1024 * 1024
MAX_XLSX_ENTRIES = 4096
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
QUALITY_METRIC_MAX_SIDE = 512
MIN_PHOTO_SHORT_SIDE = 600
MIN_MEAN_LUMINANCE = 24.0
MIN_LAPLACIAN_VARIANCE = 35.0


class FileInspectionResult(ContractModel):
    """上传文件的基础检查结果。"""

    file_name: str = Field(min_length=1)
    media_type: str | None
    size_bytes: int = Field(ge=0)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    status: Literal["accepted", "rejected", "duplicate"]
    issues: list[str]
    page_count: int | None = Field(default=None, ge=1)
    width: int | None = Field(default=None, ge=1)
    height: int | None = Field(default=None, ge=1)
    quality_status: MaterialQualityStatus | None = None
    quality_issues: list[str] = Field(default_factory=list)
    mean_luminance: float | None = Field(default=None, ge=0.0, le=255.0)
    sharpness_score: float | None = Field(default=None, ge=0.0)


def safe_file_name(file_name: str | None) -> str:
    """移除客户端文件名中的目录部分。"""

    normalized = Path(file_name or "unnamed").name
    return normalized or "unnamed"


def inspect_file(
    *,
    file_name: str | None,
    declared_media_type: str | None,
    content: bytes,
    seen_sha256: set[str] | None = None,
) -> FileInspectionResult:
    """检查单个上传文件，不保存文件内容。"""

    normalized_name = safe_file_name(file_name)
    size_bytes = len(content)

    if size_bytes == 0:
        return FileInspectionResult(
            file_name=normalized_name,
            media_type=None,
            size_bytes=0,
            sha256=None,
            status="rejected",
            issues=["empty_file"],
        )

    if size_bytes > MAX_FILE_SIZE_BYTES:
        return FileInspectionResult(
            file_name=normalized_name,
            media_type=None,
            size_bytes=size_bytes,
            sha256=None,
            status="rejected",
            issues=["file_too_large"],
        )

    digest = sha256(content).hexdigest()
    media_type, page_count, width, height, issues = inspect_content(content)

    if issues:
        return FileInspectionResult(
            file_name=normalized_name,
            media_type=media_type,
            size_bytes=size_bytes,
            sha256=digest,
            status="rejected",
            issues=issues,
            page_count=page_count,
            width=width,
            height=height,
        )

    accepted_issues: list[str] = []
    if declared_media_type and declared_media_type != media_type:
        accepted_issues.append("declared_media_type_mismatch")

    quality_status = None
    quality_issues: list[str] = []
    mean_luminance = None
    sharpness_score = None
    if media_type in {"image/jpeg", "image/png"}:
        quality_status, quality_issues, mean_luminance, sharpness_score = (
            assess_image_quality(content)
        )

    if seen_sha256 is not None and digest in seen_sha256:
        return FileInspectionResult(
            file_name=normalized_name,
            media_type=media_type,
            size_bytes=size_bytes,
            sha256=digest,
            status="duplicate",
            issues=[*accepted_issues, "duplicate_file"],
            page_count=page_count,
            width=width,
            height=height,
            quality_status=quality_status,
            quality_issues=quality_issues,
            mean_luminance=mean_luminance,
            sharpness_score=sharpness_score,
        )

    if seen_sha256 is not None:
        seen_sha256.add(digest)

    return FileInspectionResult(
        file_name=normalized_name,
        media_type=media_type,
        size_bytes=size_bytes,
        sha256=digest,
        status="accepted",
        issues=accepted_issues,
        page_count=page_count,
        width=width,
        height=height,
        quality_status=quality_status,
        quality_issues=quality_issues,
        mean_luminance=mean_luminance,
        sharpness_score=sharpness_score,
    )


def assess_image_quality(
    content: bytes,
) -> tuple[MaterialQualityStatus, list[str], float | None, float | None]:
    """Run conservative preflight checks; visual review remains authoritative.

    Sharpness and brightness are deterministic heuristics, not calibrated
    probabilities. Any flag therefore requests better material rather than
    claiming a numeric confidence score.
    """

    try:
        with Image.open(BytesIO(content)) as source:
            width, height = source.size
            grayscale = source.convert("L")
            grayscale.thumbnail(
                (QUALITY_METRIC_MAX_SIDE, QUALITY_METRIC_MAX_SIDE),
                Image.Resampling.LANCZOS,
            )
            mean_luminance = ImageStat.Stat(grayscale).mean[0]
            if grayscale.width < 3 or grayscale.height < 3:
                # A 3x3 Laplacian kernel cannot be applied to tiny valid images.
                # Such an image is already below the minimum usable resolution.
                sharpness_score = 0.0
            else:
                laplacian = grayscale.filter(
                    ImageFilter.Kernel(
                        (3, 3),
                        [-1, -1, -1, -1, 8, -1, -1, -1, -1],
                        scale=1,
                        offset=0,
                    )
                )
                sharpness_score = ImageStat.Stat(laplacian).var[0]
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError, ValueError):
        return MaterialQualityStatus.UNKNOWN, ["quality_metrics_unavailable"], None, None

    issues: list[str] = []
    if min(width, height) < MIN_PHOTO_SHORT_SIDE:
        issues.append("low_resolution")
    if mean_luminance < MIN_MEAN_LUMINANCE:
        issues.append("underexposed")
    if sharpness_score < MIN_LAPLACIAN_VARIANCE:
        issues.append("suspected_blur_or_insufficient_detail")

    return (
        MaterialQualityStatus.POOR if issues else MaterialQualityStatus.USABLE,
        issues,
        round(mean_luminance, 2),
        round(sharpness_score, 2),
    )


def inspect_content(
    content: bytes,
) -> tuple[str | None, int | None, int | None, int | None, list[str]]:
    """根据真实文件内容判断格式和可打开性。"""

    if content.startswith(b"%PDF-"):
        return inspect_pdf(content)
    if content.startswith(b"PK\x03\x04"):
        return inspect_xlsx(content)
    return inspect_image(content)


def inspect_xlsx(
    content: bytes,
) -> tuple[str, None, None, None, list[str]]:
    """Validate a macro-free OOXML workbook container without expanding arbitrary archives."""

    try:
        with ZipFile(BytesIO(content)) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_XLSX_ENTRIES:
                return XLSX_MEDIA_TYPE, None, None, None, ["xlsx_too_many_entries"]
            if any(entry.flag_bits & 0x1 for entry in entries):
                return XLSX_MEDIA_TYPE, None, None, None, ["encrypted_xlsx"]
            if any(
                name.startswith(("/", "\\")) or ".." in name.replace("\\", "/").split("/")
                for name in archive.namelist()
            ):
                return XLSX_MEDIA_TYPE, None, None, None, ["invalid_xlsx_archive_path"]
            if sum(entry.file_size for entry in entries) > MAX_XLSX_EXPANDED_SIZE_BYTES:
                return XLSX_MEDIA_TYPE, None, None, None, ["xlsx_expanded_size_too_large"]

            names = set(archive.namelist())
            required = {"[Content_Types].xml", "xl/workbook.xml", "xl/_rels/workbook.xml.rels"}
            if not required.issubset(names) or any(
                name.lower().endswith("vbaproject.bin") for name in names
            ):
                return XLSX_MEDIA_TYPE, None, None, None, ["invalid_or_macro_enabled_xlsx"]

            content_types = ElementTree.fromstring(archive.read("[Content_Types].xml"))
            workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
            if not list(workbook.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}sheet")):
                return XLSX_MEDIA_TYPE, None, None, None, ["xlsx_has_no_sheets"]
            if not list(content_types):
                return XLSX_MEDIA_TYPE, None, None, None, ["invalid_xlsx_content_types"]
    except (BadZipFile, KeyError, OSError, ValueError, ElementTree.ParseError):
        return XLSX_MEDIA_TYPE, None, None, None, ["corrupt_xlsx"]

    return XLSX_MEDIA_TYPE, None, None, None, []


def inspect_pdf(
    content: bytes,
) -> tuple[str, int | None, None, None, list[str]]:
    """检查 PDF 是否可打开、是否加密以及是否包含页面。"""

    try:
        with pymupdf.open(stream=content, filetype="pdf") as document:
            if document.needs_pass:
                return "application/pdf", None, None, None, ["password_protected_pdf"]
            if document.page_count < 1:
                return "application/pdf", None, None, None, ["empty_pdf"]
            return "application/pdf", document.page_count, None, None, []
    except (OSError, RuntimeError, ValueError):
        return "application/pdf", None, None, None, ["corrupt_pdf"]


def inspect_image(
    content: bytes,
) -> tuple[str | None, None, int | None, int | None, list[str]]:
    """检查 JPEG 或 PNG 是否可打开并读取尺寸。"""

    try:
        with Image.open(BytesIO(content)) as image:
            image_format = image.format
            width, height = image.size
            image.verify()
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError, ValueError):
        return None, None, None, None, ["unsupported_or_corrupt_file"]

    media_types = {
        "JPEG": "image/jpeg",
        "PNG": "image/png",
    }
    media_type = media_types.get(image_format or "")
    if media_type is None:
        return None, None, width, height, ["unsupported_image_format"]

    return media_type, None, width, height, []
