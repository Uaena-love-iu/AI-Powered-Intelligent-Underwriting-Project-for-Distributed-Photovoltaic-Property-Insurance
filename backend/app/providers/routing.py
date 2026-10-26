from app.contracts import (
    MaterialCategory,
    OcrField,
    RiskFinding,
    WatermarkStatus,
)

from .common import MaterialInput
from .ocr.base import OcrProvider
from .vision.base import VisionAnalysis, VisionProvider

OCR_MEDIA_TYPES = frozenset(
    {
        "application/pdf",
        "image/jpeg",
        "image/png",
    }
)

VISION_MEDIA_TYPES = frozenset(
    {
        "image/jpeg",
        "image/png",
    }
)

OCR_MATERIAL_CATEGORIES = frozenset(
    {
        MaterialCategory.PROJECT_DOCUMENT,
        MaterialCategory.EQUIPMENT_INVENTORY,
        MaterialCategory.FILING_CERTIFICATE,
        MaterialCategory.GRID_CONNECTION_DOCUMENT,
        MaterialCategory.ELECTRICAL_GROUNDING,
        MaterialCategory.COMPONENT_NAMEPLATE,
        MaterialCategory.INVERTER_NAMEPLATE,
        MaterialCategory.COMBINER_BOX,
        MaterialCategory.MONITORING_OPTIONAL,
        MaterialCategory.OTHER,
    }
)

VISION_MATERIAL_CATEGORIES = frozenset(
    {
        MaterialCategory.PANORAMA,
        MaterialCategory.ROOF_CONNECTION,
        MaterialCategory.COMPONENT_SURFACE,
        MaterialCategory.PARAPET,
        MaterialCategory.WORKSHOP,
        MaterialCategory.ELECTRICAL_GROUNDING,
        MaterialCategory.COMPONENT_NAMEPLATE,
        MaterialCategory.INVERTER_NAMEPLATE,
        MaterialCategory.COMBINER_BOX,
        MaterialCategory.MONITORING_OPTIONAL,
        MaterialCategory.OTHER,
    }
)


# Includes scene photos and equipment photos; documents do not inherit this task.
SCENE_PHOTO_CATEGORIES = VISION_MATERIAL_CATEGORIES


def should_run_watermark_ocr(material_input: MaterialInput) -> bool:
    material=material_input.material
    return material.media_type in VISION_MEDIA_TYPES and material.category in SCENE_PHOTO_CATEGORIES and (
        material.watermark_status in {WatermarkStatus.PRESENT, WatermarkStatus.UNCERTAIN}
        or material.category is MaterialCategory.PANORAMA and material.watermark_status is WatermarkStatus.NOT_CHECKED)


def should_run_business_ocr(material_input: MaterialInput) -> bool:
    return material_input.material.media_type in OCR_MEDIA_TYPES and material_input.material.category in OCR_MATERIAL_CATEGORIES


def should_run_ocr(material_input: MaterialInput) -> bool:
    if material_input.ocr_task=='watermark': return should_run_watermark_ocr(material_input)
    if material_input.ocr_task=='business': return should_run_business_ocr(material_input)
    return should_run_watermark_ocr(material_input) or should_run_business_ocr(material_input)


def should_run_vision(
    material_input: MaterialInput,
) -> bool:
    """判断当前材料是否需要调用视觉风险识别。"""

    material = material_input.material

    return (
        material.media_type in VISION_MEDIA_TYPES
        and material.category
        in VISION_MATERIAL_CATEGORIES
    )


class RoutedOcrProvider(OcrProvider):
    """仅把适合 OCR 的材料交给下游 Provider。"""

    def __init__(
        self,
        delegate: OcrProvider,
        filing_delegate: OcrProvider | None = None,
    ) -> None:
        self._delegate = delegate
        self._filing_delegate = filing_delegate or delegate

    @property
    def name(self) -> str:
        return self._delegate.name

    @property
    def model_name(self) -> str:
        return self._delegate.model_name

    def _provider_for(self, material_input: MaterialInput) -> OcrProvider:
        if material_input.material.category in {
            MaterialCategory.FILING_CERTIFICATE,
            MaterialCategory.PROJECT_DOCUMENT,
        }:
            return self._filing_delegate
        return self._delegate

    def model_name_for_materials(self, materials: list[MaterialInput]) -> str:
        models = {self._provider_for(item).model_name for item in materials
                  if should_run_ocr(item)}
        return ", ".join(sorted(models)) if models else self.model_name

    def extract(
        self,
        material_input: MaterialInput,
    ) -> list[OcrField]:
        if not should_run_ocr(material_input):
            return []

        if material_input.ocr_task == 'auto':
            from app.providers.ocr.tasks import extract_tasks
            return extract_tasks(self, material_input, raise_errors=True)[0]
        return self._provider_for(material_input).extract(
            material_input
        )


class RoutedVisionProvider(VisionProvider):
    """仅把适合风险识别的图片交给下游 Provider。"""

    def __init__(
        self,
        delegate: VisionProvider,
        installation_delegate: VisionProvider | None = None,
        geometry_delegate: VisionProvider | None = None,
    ) -> None:
        self._delegate = delegate
        self._installation_delegate = installation_delegate or delegate
        self._geometry_delegate = geometry_delegate or delegate

    @property
    def name(self) -> str:
        return self._delegate.name

    @property
    def model_name(self) -> str:
        return self._delegate.model_name

    @property
    def installation_model_name(self) -> str:
        return self._installation_delegate.model_name

    @property
    def geometry_model_name(self) -> str:
        return self._geometry_delegate.model_name

    def analyze(
        self,
        material_input: MaterialInput,
    ) -> list[RiskFinding]:
        if not should_run_vision(material_input):
            return []

        return self._delegate.analyze(
            material_input
        )

    def analyze_with_watermark(self, material_input: MaterialInput) -> VisionAnalysis:
        if not should_run_vision(material_input):
            return VisionAnalysis(findings=[])
        return self._delegate.analyze_with_watermark(material_input)

    def analyze_installation(self, materials: list[MaterialInput], *, filing_fields=None):
        return self._installation_delegate.analyze_installation(
            materials, filing_fields=filing_fields,
        )

    def estimate_geometry(self, materials: list[MaterialInput], installation):
        return self._geometry_delegate.estimate_geometry(materials, installation)
