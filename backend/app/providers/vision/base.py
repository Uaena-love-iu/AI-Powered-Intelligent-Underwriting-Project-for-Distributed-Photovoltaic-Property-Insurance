from abc import ABC, abstractmethod

from pydantic import BaseModel

from app.contracts import (
    Bbox,
    GeometryAnalysis,
    GeometryArea,
    GeometryMetric,
    InstallationAnalysis,
    InstallationArea,
    InstallationSceneType,
    OcrField,
    RiskFinding,
)

from ..common import MaterialInput


class VisionAnalysis(BaseModel):
    findings: list[RiskFinding]
    watermark_present: bool | None = None
    watermark_bbox: Bbox | None = None
    watermark_evidence: str | None = None


class VisionProvider(ABC):
    """所有视觉识别服务必须实现的统一接口。"""

    def plan_verification(self, context):
        from app.verification.model import plan
        return plan(self, context)

    def verify_images(self, materials, context):
        from app.verification.model import verify
        return verify(self, materials, context)

    @property
    @abstractmethod
    def name(self) -> str:
        """返回Provider名称。"""

    @property
    @abstractmethod
    def model_name(self) -> str:
        """返回模型名称或版本。"""

    @property
    def installation_model_name(self) -> str:
        return self.model_name

    @property
    def geometry_model_name(self) -> str:
        return self.model_name

    @abstractmethod
    def analyze(self, material_input: MaterialInput) -> list[RiskFinding]:
        """分析一份图片材料并返回风险点。"""

    def analyze_with_watermark(self, material_input: MaterialInput) -> VisionAnalysis:
        """同时返回风险点和全景照水印判断。"""
        return VisionAnalysis(findings=self.analyze(material_input))

    def analyze_installation(self, materials: list[MaterialInput],
                             *, filing_fields: list[OcrField] | None = None) -> InstallationAnalysis:
        """Group recognition is optional for legacy providers; unknown never means out of scope."""
        return InstallationAnalysis(
            areas=[InstallationArea(
                area_id="project", scene_type=InstallationSceneType.UNKNOWN,
                status="unknown", material_ids=[item.material.material_id for item in materials],
                evidence_text="当前视觉服务未提供组图安装类型识别结果",
                pending_question="补充能展示组件承载结构和连接关系的现场照片，或人工核验",
            )], provider=self.name, model=self.model_name,
        )

    def estimate_geometry(self, materials: list[MaterialInput],
                          installation: InstallationAnalysis) -> GeometryAnalysis:
        """Legacy providers explicitly return unknown values instead of invented defaults."""
        unknown = lambda: GeometryMetric(status="unknown", evidence_text="没有可靠的组图估计结果")
        return GeometryAnalysis(
            areas=[GeometryArea(area_id=area.area_id, tilt_degrees=unknown(),
                                height_above_ground_m=unknown(), ground_roughness=unknown())
                   for area in installation.areas],
            provider=self.name, model=self.model_name,
        )
