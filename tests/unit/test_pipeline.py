from app.contracts import (
    DecisionType,
    Material,
    MaterialCategory,
    MaterialParseStatus,
    MaterialQualityStatus,
    MaterialReviewAction,
    OcrField,
    ProcessingStatus,
    ProcessingStep,
    ProjectInfo,
)
from app.pipeline import UnderwritingPipeline
from app.providers import (
    MaterialInput,
    MockOcrProvider,
    MockVisionProvider,
    OcrProvider,
    ProviderError,
)
from app.providers.vision.base import VisionAnalysis


def project_info() -> ProjectInfo:
    return ProjectInfo(
        project_name="示例光伏项目",
        insured_name="示例企业",
        project_type="rooftop",
        installation_type="color_steel_roof",
        site_address="广东省示例市",
        longitude=113.25,
        latitude=23.12,
        proposed_start_date="2026-10-01",
        component_model="PV-MODULE-580W",
    )


def material_input(
    material_id: str,
    category: MaterialCategory,
    quality_status: MaterialQualityStatus = MaterialQualityStatus.USABLE,
) -> MaterialInput:
    material = Material(
        material_id=material_id,
        category=category,
        file_name=f"{material_id}.jpg",
        media_type="image/jpeg",
        quality_status=quality_status,
        quality_confidence=0.95,
        quality_issues=[],
        parse_status=MaterialParseStatus.SUCCESS,
    )
    return MaterialInput(material=material, content=b"mock-image-content")


class FailingOcrProvider(OcrProvider):
    @property
    def name(self) -> str:
        return "failing-ocr"

    @property
    def model_name(self) -> str:
        return "failing-ocr-v1"

    def extract(self, material_input: MaterialInput) -> list[OcrField]:
        raise ProviderError("sensitive upstream error details")


class PartiallyFailingOcrProvider(MockOcrProvider):
    @property
    def name(self) -> str:
        return "partially-failing-ocr"

    def extract(self, material_input: MaterialInput) -> list[OcrField]:
        if material_input.material.category is MaterialCategory.PANORAMA:
            raise ProviderError("sensitive upstream error details")
        return super().extract(material_input)


def test_panorama_vision_precedes_ocr_and_skips_it_without_watermark() -> None:
    events: list[str] = []

    class NoWatermarkVision(MockVisionProvider):
        def analyze_with_watermark(self, item: MaterialInput) -> VisionAnalysis:
            events.append("vision")
            return VisionAnalysis(findings=self.analyze(item), watermark_present=False)

    class RecordingOcr(MockOcrProvider):
        def extract(self, item: MaterialInput) -> list[OcrField]:
            events.append("ocr")
            return super().extract(item)

    case = UnderwritingPipeline(
        ocr_provider=RecordingOcr(),
        vision_provider=NoWatermarkVision(),
        progress_callback=lambda step, percent: events.append(step),
    ).run(
        case_id="watermark-order",
        project=project_info(),
        materials=[material_input("material-panorama", MaterialCategory.PANORAMA)],
    )

    assert events.index("正在逐份识别并匹配材料规则") < events.index("vision")
    assert events.count("vision") == 1
    assert "ocr" not in events
    assert case.materials[0].watermark_status.value == "absent"
    assert next(trace for trace in case.processing_trace
                if trace.step is ProcessingStep.VISION).status is ProcessingStatus.SUCCESS


def test_pipeline_builds_case_with_mock_provider_results() -> None:
    pipeline = UnderwritingPipeline(
        ocr_provider=MockOcrProvider(),
        vision_provider=MockVisionProvider(),
    )

    case = pipeline.run(
        case_id="case-001",
        project=project_info(),
        materials=[
            material_input("material-nameplate", MaterialCategory.COMPONENT_NAMEPLATE),
            material_input("material-panorama", MaterialCategory.PANORAMA),
        ],
    )

    assert len(case.materials) == 2
    assert len(case.ocr_fields) == 1
    assert len(case.findings) == 1
    assert len(case.material_reviews) == 2
    assert any(trace.step is ProcessingStep.INSTALLATION_GROUP for trace in case.processing_trace)
    assert any(trace.step is ProcessingStep.COMPLETENESS for trace in case.processing_trace)
    assert case.decision is not None
    assert case.decision.decision is DecisionType.REQUEST_MORE
    assert "ENV-SITE-RELATION-REVIEW-001" in case.decision.decisive_rule_ids
    assert case.pre_catastrophe_outcome == "request_more"


def test_pipeline_keeps_running_when_ocr_provider_fails() -> None:
    pipeline = UnderwritingPipeline(
        ocr_provider=FailingOcrProvider(),
        vision_provider=MockVisionProvider(),
    )

    case = pipeline.run(
        case_id="case-002",
        project=project_info(),
        materials=[
            material_input("material-panorama", MaterialCategory.PANORAMA),
        ],
    )

    ocr_trace = next(trace for trace in case.processing_trace
                     if trace.step is ProcessingStep.OCR
                     and trace.status is ProcessingStatus.FAILED)

    assert case.ocr_fields == []
    assert len(case.findings) == 1
    assert ocr_trace.status is ProcessingStatus.FAILED
    assert ocr_trace.error_code == "OCR_PROVIDER_FAILURE"
    assert "sensitive upstream error details" not in (ocr_trace.error_message or "")
    assert case.decision is not None
    assert case.decision.decision is DecisionType.REQUEST_MORE
    assert "ENV-SITE-RELATION-REVIEW-001" in case.decision.decisive_rule_ids
    assert "SYS-PROVIDER-FAILURE" in case.decision.decisive_rule_ids


def test_pipeline_marks_partially_failed_provider() -> None:
    pipeline = UnderwritingPipeline(
        ocr_provider=PartiallyFailingOcrProvider(),
        vision_provider=MockVisionProvider(),
    )

    case = pipeline.run(
        case_id="case-003",
        project=project_info(),
        materials=[
            material_input("material-nameplate", MaterialCategory.COMPONENT_NAMEPLATE),
            material_input("material-panorama", MaterialCategory.PANORAMA),
        ],
    )

    ocr_traces = [trace for trace in case.processing_trace
                  if trace.step is ProcessingStep.OCR and trace.status is not ProcessingStatus.SKIPPED]

    assert len(case.ocr_fields) == 1
    assert {trace.status for trace in ocr_traces} == {
        ProcessingStatus.SUCCESS, ProcessingStatus.FAILED,
    }
    assert any(trace.error_code == "OCR_PROVIDER_FAILURE"
               for trace in ocr_traces if trace.status is ProcessingStatus.FAILED)

def test_pipeline_adds_request_more_review_for_poor_material() -> None:
    pipeline = UnderwritingPipeline(
        ocr_provider=MockOcrProvider(),
        vision_provider=MockVisionProvider(),
    )

    case = pipeline.run(
        case_id="case-004",
        project=project_info(),
        materials=[
            material_input(
                "material-poor",
                MaterialCategory.PANORAMA,
                MaterialQualityStatus.POOR,
            ),
        ],
    )

    assert len(case.material_reviews) == 1
    assert case.material_reviews[0].action is MaterialReviewAction.REQUEST_MORE
    assert "MAT-QUALITY-001" in case.material_reviews[0].triggered_rule_ids
    assert "ENV-EXCLUDED-001" not in case.material_reviews[0].triggered_rule_ids
    assert "ENV-SITE-RELATION-REVIEW-001" in case.material_reviews[0].triggered_rule_ids
    assert case.decision is not None
    assert case.decision.decision is DecisionType.REQUEST_MORE
    assert "ENV-SITE-RELATION-REVIEW-001" in case.decision.decisive_rule_ids
    assert "MAT-QUALITY-001" in case.decision.decisive_rule_ids
    rule_trace = next(
        trace
        for trace in case.processing_trace
        if trace.step is ProcessingStep.RULE_ENGINE
    )
    assert rule_trace.status is ProcessingStatus.SUCCESS
