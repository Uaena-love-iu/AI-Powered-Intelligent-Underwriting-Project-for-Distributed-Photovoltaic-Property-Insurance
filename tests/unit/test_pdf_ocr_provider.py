from io import BytesIO

import pymupdf
import pytest
from app.contracts import (
    Material,
    MaterialCategory,
    MaterialParseStatus,
    MaterialQualityStatus,
    OcrField,
    OcrValueStatus,
)
from app.providers import (
    MaterialInput,
    OcrProvider,
    PdfPageOcrProvider,
    ProviderError,
)
from PIL import Image


def pdf_bytes(
    page_count: int = 2,
) -> bytes:
    document = pymupdf.open()

    try:
        for page_number in range(
            1,
            page_count + 1,
        ):
            page = document.new_page(
                width=300,
                height=200,
            )
            page.insert_text(
                (30, 60),
                f"PDF test page {page_number}",
            )

        return document.tobytes()
    finally:
        document.close()


def png_bytes() -> bytes:
    buffer = BytesIO()

    Image.new(
        "RGB",
        (80, 60),
        color="white",
    ).save(
        buffer,
        format="PNG",
    )

    return buffer.getvalue()


def material_input(
    *,
    media_type: str,
    content: bytes,
    file_name: str,
) -> MaterialInput:
    material = Material(
        material_id="material-pdf-001",
        category=(
            MaterialCategory
            .FILING_CERTIFICATE
        ),
        file_name=file_name,
        media_type=media_type,
        quality_status=(
            MaterialQualityStatus.USABLE
        ),
        quality_confidence=0.95,
        quality_issues=[],
        parse_status=(
            MaterialParseStatus.SUCCESS
        ),
    )

    return MaterialInput(
        material=material,
        content=content,
    )


class RecordingOcrProvider(OcrProvider):
    def __init__(self) -> None:
        self.received_inputs: list[
            MaterialInput
        ] = []

    @property
    def name(self) -> str:
        return "recording-ocr-provider"

    @property
    def model_name(self) -> str:
        return "recording-ocr-v1"

    def extract(
        self,
        item: MaterialInput,
    ) -> list[OcrField]:
        self.received_inputs.append(item)

        if "-page-2.png" in (
            item.material.file_name
        ):
            return [
                self._field(
                    item=item,
                    field_name="project_name",
                    value="第二页高置信度项目名称",
                    confidence=0.95,
                )
            ]

        fields = [
            self._field(
                item=item,
                field_name="project_name",
                value="第一页项目名称",
                confidence=0.70,
            )
        ]

        if "-page-1.png" in (
            item.material.file_name
        ):
            fields.append(
                self._field(
                    item=item,
                    field_name="project_entity",
                    value="示例新能源有限公司",
                    confidence=0.90,
                )
            )

        return fields

    def _field(
        self,
        *,
        item: MaterialInput,
        field_name: str,
        value: str,
        confidence: float,
    ) -> OcrField:
        return OcrField(
            field_id=(
                f"delegate:"
                f"{field_name}:"
                f"{len(self.received_inputs)}"
            ),
            material_id=(
                item.material.material_id
            ),
            field_name=field_name,
            raw_value=value,
            normalized_value=value,
            value_status=(
                OcrValueStatus.EXTRACTED
            ),
            confidence=confidence,
            bbox=None,
            provider=self.name,
            model=self.model_name,
            evidence_text=value,
        )


def test_forwards_image_directly_to_delegate() -> None:
    delegate = RecordingOcrProvider()
    provider = PdfPageOcrProvider(delegate)

    source = material_input(
        media_type="image/png",
        content=png_bytes(),
        file_name="filing.png",
    )

    fields = provider.extract(source)

    assert len(delegate.received_inputs) == 1
    assert delegate.received_inputs[0] is source
    assert len(fields) == 1
    assert (
        fields[0].raw_value
        == "第一页项目名称"
    )
    assert fields[0].field_id.startswith(
        "delegate:"
    )


def test_renders_pdf_pages_and_merges_best_fields() -> None:
    delegate = RecordingOcrProvider()
    provider = PdfPageOcrProvider(delegate)

    fields = provider.extract(
        material_input(
            media_type="application/pdf",
            content=pdf_bytes(2),
            file_name="filing.pdf",
        )
    )

    assert len(
        delegate.received_inputs
    ) == 2

    first_page = (
        delegate.received_inputs[0]
    )
    second_page = (
        delegate.received_inputs[1]
    )

    assert (
        first_page.material.media_type
        == "image/png"
    )
    assert first_page.material.file_name == (
        "filing-page-1.png"
    )
    assert (
        first_page.material.sha256 is None
    )
    assert first_page.content.startswith(
        b"\x89PNG\r\n\x1a\n"
    )

    assert second_page.material.file_name == (
        "filing-page-2.png"
    )

    assert len(fields) == 3

    project_names = [field for field in fields if field.field_name == "project_name"]
    project_entity = next(
        field
        for field in fields
        if field.field_name
        == "project_entity"
    )

    assert [field.raw_value for field in project_names] == [
        "第一页项目名称", "第二页高置信度项目名称",
    ]
    assert [field.page_number for field in project_names] == [1, 2]
    assert project_names[1].evidence_text == "PDF 第 2 页：第二页高置信度项目名称"

    assert project_entity.field_id == "material-pdf-001:project_entity:1"
    assert project_entity.evidence_text == (
        "PDF 第 1 页："
        "示例新能源有限公司"
    )


def test_limits_number_of_rendered_pages() -> None:
    delegate = RecordingOcrProvider()
    provider = PdfPageOcrProvider(
        delegate,
        max_pages=2,
    )

    provider.extract(
        material_input(
            media_type="application/pdf",
            content=pdf_bytes(4),
            file_name="long-filing.pdf",
        )
    )

    assert len(
        delegate.received_inputs
    ) == 2
    assert (
        delegate
        .received_inputs[-1]
        .material
        .file_name
        == "long-filing-page-2.png"
    )


def test_rejects_empty_pdf() -> None:
    provider = PdfPageOcrProvider(
        RecordingOcrProvider()
    )

    with pytest.raises(
        ProviderError,
        match="received an empty PDF",
    ):
        provider.extract(
            material_input(
                media_type="application/pdf",
                content=b"",
                file_name="empty.pdf",
            )
        )


def test_rejects_corrupt_pdf() -> None:
    provider = PdfPageOcrProvider(
        RecordingOcrProvider()
    )

    with pytest.raises(
        ProviderError,
        match="could not render the PDF",
    ):
        provider.extract(
            material_input(
                media_type="application/pdf",
                content=b"not-a-real-pdf",
                file_name="broken.pdf",
            )
        )


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (
            {"max_pages": 0},
            "max_pages must be greater than zero",
        ),
        (
            {"render_dpi": 0},
            "render_dpi must be greater than zero",
        ),
    ],
)
def test_rejects_invalid_configuration(
    arguments: dict[str, int],
    message: str,
) -> None:
    with pytest.raises(
        ValueError,
        match=message,
    ):
        PdfPageOcrProvider(
            RecordingOcrProvider(),
            **arguments,
        )
