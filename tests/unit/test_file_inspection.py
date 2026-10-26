from io import BytesIO

import pymupdf
from app.contracts import MaterialQualityStatus
from app.intake import FileInspectionResult, inspect_file, safe_file_name
from PIL import Image


def image_bytes(image_format: str) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (32, 24), color="white").save(buffer, format=image_format)
    return buffer.getvalue()


def pdf_bytes() -> bytes:
    document = pymupdf.open()
    document.new_page()
    content = document.tobytes()
    document.close()
    return content


def test_inspects_valid_jpeg() -> None:
    result = inspect_file(
        file_name="panel.jpg",
        declared_media_type="image/jpeg",
        content=image_bytes("JPEG"),
    )

    assert isinstance(result, FileInspectionResult)
    assert result.status == "accepted"
    assert result.media_type == "image/jpeg"
    assert result.width == 32
    assert result.height == 24
    assert result.quality_status is MaterialQualityStatus.POOR
    assert "low_resolution" in result.quality_issues
    assert result.page_count is None
    assert result.sha256 is not None
    assert len(result.sha256) == 64


def test_inspects_valid_pdf() -> None:
    result = inspect_file(
        file_name="filing.pdf",
        declared_media_type="application/pdf",
        content=pdf_bytes(),
    )

    assert result.status == "accepted"
    assert result.media_type == "application/pdf"
    assert result.page_count == 1
    assert result.width is None
    assert result.height is None


def test_rejects_empty_file() -> None:
    result = inspect_file(
        file_name="empty.jpg",
        declared_media_type="image/jpeg",
        content=b"",
    )

    assert result.status == "rejected"
    assert result.issues == ["empty_file"]
    assert result.sha256 is None


def test_rejects_corrupt_pdf() -> None:
    result = inspect_file(
        file_name="broken.pdf",
        declared_media_type="application/pdf",
        content=b"%PDF-broken",
    )

    assert result.status == "rejected"
    assert result.media_type == "application/pdf"
    assert result.issues == ["corrupt_pdf"]


def test_rejects_unsupported_content() -> None:
    result = inspect_file(
        file_name="notes.txt",
        declared_media_type="text/plain",
        content=b"plain text is not a supported underwriting material",
    )

    assert result.status == "rejected"
    assert result.issues == ["unsupported_or_corrupt_file"]


def test_marks_repeated_valid_content_as_duplicate() -> None:
    content = image_bytes("PNG")
    seen: set[str] = set()

    first = inspect_file(
        file_name="first.png",
        declared_media_type="image/png",
        content=content,
        seen_sha256=seen,
    )
    second = inspect_file(
        file_name="second.png",
        declared_media_type="image/png",
        content=content,
        seen_sha256=seen,
    )

    assert first.status == "accepted"
    assert second.status == "duplicate"
    assert second.issues == ["duplicate_file"]
    assert first.sha256 == second.sha256


def test_reports_declared_media_type_mismatch() -> None:
    result = inspect_file(
        file_name="panel.jpg",
        declared_media_type="image/png",
        content=image_bytes("JPEG"),
    )

    assert result.status == "accepted"
    assert result.media_type == "image/jpeg"
    assert result.issues == ["declared_media_type_mismatch"]


def test_removes_directory_parts_from_file_name() -> None:
    assert safe_file_name("folder/subfolder/panel.jpg") == "panel.jpg"
    assert safe_file_name("") == "unnamed"
