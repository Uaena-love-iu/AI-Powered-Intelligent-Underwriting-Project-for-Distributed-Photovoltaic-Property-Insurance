from pathlib import Path

from app.contracts import (
    Material,
    MaterialCategory,
    MaterialParseStatus,
    MaterialQualityStatus,
    ProjectInfo,
)
from app.pipeline import UnderwritingPipeline
from app.providers import MaterialInput, MockOcrProvider, MockVisionProvider
from app.reports import generate_markdown_report


def create_material(
    *,
    material_id: str,
    category: MaterialCategory,
    file_name: str,
) -> MaterialInput:
    material = Material(
        material_id=material_id,
        category=category,
        file_name=file_name,
        media_type="image/jpeg",
        quality_status=MaterialQualityStatus.USABLE,
        quality_confidence=0.95,
        quality_issues=[],
        parse_status=MaterialParseStatus.SUCCESS,
    )

    return MaterialInput(
        material=material,
        content=b"mock-file-content",
    )


def main() -> None:
    project = ProjectInfo(
        project_name="示例分布式光伏项目",
        insured_name="示例制造企业有限公司",
        project_entity="示例新能源有限公司",
        project_type="rooftop",
        installation_type="color_steel_roof",
        site_address="广东省示例市示例区",
        province="广东省",
        city="示例市",
        district="示例区",
        longitude=113.25,
        latitude=23.12,
        proposed_start_date="2026-10-01",
        component_model="PV-MODULE-580W",
        submission_ip="192.0.2.10",
    )

    materials = [
        create_material(
            material_id="material-nameplate-001",
            category=MaterialCategory.COMPONENT_NAMEPLATE,
            file_name="component-nameplate.jpg",
        ),
        create_material(
            material_id="material-panorama-001",
            category=MaterialCategory.PANORAMA,
            file_name="site-panorama.jpg",
        ),
    ]

    pipeline = UnderwritingPipeline(
        ocr_provider=MockOcrProvider(),
        vision_provider=MockVisionProvider(),
    )

    case = pipeline.run(
        case_id="case-demo-001",
        project=project,
        materials=materials,
    )

    output_directory = Path("outputs")
    output_directory.mkdir(parents=True, exist_ok=True)

    json_output_path = output_directory / "mock_case_result.json"
    json_output_path.write_text(
        case.model_dump_json(indent=2),
        encoding="utf-8",
    )

    report_output_path = output_directory / "mock_underwriting_report.md"
    report_output_path.write_text(
        generate_markdown_report(case),
        encoding="utf-8",
    )

    decision = (
        case.decision.decision.value
        if case.decision is not None
        else "none"
    )

    print(f"JSON generated: {json_output_path.resolve()}")
    print(f"Report generated: {report_output_path.resolve()}")
    print(f"Decision: {decision}")
    print(f"OCR fields: {len(case.ocr_fields)}")
    print(f"Risk findings: {len(case.findings)}")
    print(f"Material reviews: {len(case.material_reviews)}")
    print(f"Processing traces: {len(case.processing_trace)}")


if __name__ == "__main__":
    main()
