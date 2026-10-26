from app.contracts import MaterialCategory, OcrField, OcrValueStatus

from ..common import MaterialInput
from .base import OcrProvider


class MockOcrProvider(OcrProvider):
    """用于本地开发和测试的固定OCR实现。"""

    @property
    def name(self) -> str:
        return "mock-ocr"

    @property
    def model_name(self) -> str:
        return "mock-ocr-v1"

    def extract(self, material_input: MaterialInput) -> list[OcrField]:
        material = material_input.material

        if material.category is not MaterialCategory.COMPONENT_NAMEPLATE:
            return []

        return [
            OcrField(
                field_id=f"{material.material_id}:component-model",
                material_id=material.material_id,
                field_name="component_model",
                raw_value="JAM66D42-580/MB",
                normalized_value="JAM66D42-580/MB",
                value_status=OcrValueStatus.EXTRACTED,
                confidence=0.98,
                bbox=None,
                provider=self.name,
                model=self.model_name,
                evidence_text="组件型号：JAM66D42-580/MB",
            )
        ]
