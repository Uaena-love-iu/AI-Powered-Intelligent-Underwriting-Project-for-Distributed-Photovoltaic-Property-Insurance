from pathlib import Path

import pymupdf

from app.contracts import MaterialCategory, OcrField

from ..common import MaterialInput, ProviderError
from .base import OcrProvider

PDF_MEDIA_TYPE = "application/pdf"
DEFAULT_MAX_PDF_PAGES = 5
DEFAULT_PDF_RENDER_DPI = 144


class PdfPageOcrProvider(OcrProvider):
    """把 PDF 页面渲染为图片后交给下游 OCR Provider。"""

    def __init__(
        self,
        delegate: OcrProvider,
        *,
        max_pages: int = DEFAULT_MAX_PDF_PAGES,
        render_dpi: int = DEFAULT_PDF_RENDER_DPI,
    ) -> None:
        if max_pages < 1:
            raise ValueError(
                "max_pages must be greater than zero"
            )

        if render_dpi < 1:
            raise ValueError(
                "render_dpi must be greater than zero"
            )

        self._delegate = delegate
        self._max_pages = max_pages
        self._render_dpi = render_dpi

    @property
    def name(self) -> str:
        return self._delegate.name

    @property
    def model_name(self) -> str:
        return self._delegate.model_name

    def extract(
        self,
        material_input: MaterialInput,
    ) -> list[OcrField]:
        if (
            material_input.material.media_type
            != PDF_MEDIA_TYPE
        ):
            return self._delegate.extract(
                material_input
            )

        if not material_input.content:
            raise ProviderError(
                "PDF OCR provider received an empty PDF"
            )

        rendered_pages = self._render_pages(
            material_input.content
        )

        best_fields: dict[
            str,
            tuple[int, OcrField],
        ] = {}
        critical_fields: list[tuple[int, OcrField]] = []
        is_filing_candidate = material_input.material.category in {
            MaterialCategory.FILING_CERTIFICATE,
            MaterialCategory.PROJECT_DOCUMENT,
        }

        for page_number, page_content in rendered_pages:
            page_input = self._page_material_input(
                material_input=material_input,
                page_number=page_number,
                page_content=page_content,
            )

            page_fields = self._delegate.extract(
                page_input
            )

            for field in page_fields:
                if is_filing_candidate and field.field_name in {
                    "project_name", "construction_content",
                }:
                    # Keep every page's evidence and assessment. A later page may
                    # contain a change or prohibition absent from the first page.
                    critical_fields.append((page_number, field))
                    continue
                current = best_fields.get(
                    field.field_name
                )

                if (
                    current is None
                    or field.confidence
                    > current[1].confidence
                ):
                    best_fields[
                        field.field_name
                    ] = (
                        page_number,
                        field,
                    )

        return self._merge_fields(
            material_id=(
                material_input
                .material
                .material_id
            ),
            best_fields=best_fields,
            critical_fields=critical_fields,
        )

    def _render_pages(
        self,
        content: bytes,
    ) -> list[tuple[int, bytes]]:
        try:
            with pymupdf.open(
                stream=content,
                filetype="pdf",
            ) as document:
                if document.needs_pass:
                    raise ProviderError(
                        "PDF OCR provider cannot "
                        "process a password-protected PDF"
                    )

                if document.page_count < 1:
                    raise ProviderError(
                        "PDF OCR provider received "
                        "a PDF without pages"
                    )

                page_count = min(
                    document.page_count,
                    self._max_pages,
                )

                rendered_pages: list[
                    tuple[int, bytes]
                ] = []

                for page_index in range(
                    page_count
                ):
                    page = document.load_page(
                        page_index
                    )
                    pixmap = page.get_pixmap(
                        dpi=self._render_dpi,
                        alpha=False,
                    )

                    rendered_pages.append(
                        (
                            page_index + 1,
                            pixmap.tobytes("png"),
                        )
                    )

                return rendered_pages

        except ProviderError:
            raise
        except (
            OSError,
            RuntimeError,
            ValueError,
        ) as exc:
            raise ProviderError(
                "PDF OCR provider could not "
                "render the PDF"
            ) from exc

    @staticmethod
    def _page_material_input(
        *,
        material_input: MaterialInput,
        page_number: int,
        page_content: bytes,
    ) -> MaterialInput:
        original_material = (
            material_input.material
        )
        file_stem = Path(
            original_material.file_name
        ).stem

        page_material = (
            original_material.model_copy(
                update={
                    "file_name": (
                        f"{file_stem}-page-"
                        f"{page_number}.png"
                    ),
                    "media_type": "image/png",
                    "sha256": None,
                }
            )
        )

        return MaterialInput(
            material=page_material,
            content=page_content,
            ocr_task=material_input.ocr_task,
        )

    @staticmethod
    def _merge_fields(
        *,
        material_id: str,
        best_fields: dict[
            str,
            tuple[int, OcrField],
        ],
        critical_fields: list[tuple[int, OcrField]],
    ) -> list[OcrField]:
        merged_fields: list[OcrField] = []

        page_fields = list(best_fields.values()) + critical_fields
        for index, (page_number, field) in enumerate(page_fields, start=1):
            field_name = field.field_name

            evidence_text = (
                f"PDF 第 {page_number} 页"
            )

            if field.evidence_text:
                evidence_text += (
                    f"：{field.evidence_text}"
                )

            merged_fields.append(
                field.model_copy(
                    update={
                        "field_id": (
                            f"{material_id}:"
                            f"{field_name}:"
                            f"{index}"
                        ),
                        "material_id": material_id,
                        "page_number": page_number,
                        "evidence_text": (
                            evidence_text
                        ),
                    }
                )
            )

        return merged_fields
