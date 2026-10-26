from app.contracts import (
    Bbox,
    DetectionStatus,
    EnvironmentRelation,
    MaterialCategory,
    RiskCategory,
    RiskFinding,
    RiskSeverity,
)

from ..common import MaterialInput
from .base import VisionAnalysis, VisionProvider


class MockVisionProvider(VisionProvider):
    """用于本地开发和测试的固定视觉识别实现。"""

    @property
    def name(self) -> str:
        return "mock-vision"

    @property
    def model_name(self) -> str:
        return "mock-vision-v1"

    def analyze_with_watermark(self, material_input: MaterialInput) -> VisionAnalysis:
        return VisionAnalysis(
            findings=self.analyze(material_input),
            watermark_present=(
                True if material_input.material.category is MaterialCategory.PANORAMA else None
            ),
        )

    def analyze(self, material_input: MaterialInput) -> list[RiskFinding]:
        material = material_input.material

        if material.category is not MaterialCategory.PANORAMA:
            return []

        return [
            RiskFinding(
                finding_id=f"{material.material_id}:water-adjacent",
                material_id=material.material_id,
                category=RiskCategory.WATER_ADJACENT_ENVIRONMENT,
                label="临近水体",
                detection_status=DetectionStatus.DETECTED,
                severity=RiskSeverity.HIGH,
                confidence=0.86,
                environment_relation=EnvironmentRelation.UNCERTAIN,
                bbox=Bbox(
                    x_min=0.52,
                    y_min=0.18,
                    x_max=0.96,
                    y_max=0.84,
                    coordinate_space="normalized_0_1",
                ),
                evidence_text="全景照片右侧发现大面积水体",
                provider=self.name,
                model=self.model_name,
                requires_manual_review=True,
            )
        ]
