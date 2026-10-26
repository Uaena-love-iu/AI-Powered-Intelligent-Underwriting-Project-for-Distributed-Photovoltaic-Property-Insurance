"""Recompute a clearly simulated case, without calling real recognition services."""
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.catastrophe import CatastropheAssessmentEngine
from app.contracts import UnderwritingCase
from app.decision import DecisionEngine
from app.rules import RuleEngine


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python scripts/verify_material_positive_case.py CASE.json")
    case = UnderwritingCase.model_validate_json(Path(sys.argv[1]).read_text(encoding="utf-8"))
    engine = RuleEngine()
    case.package_assessment = engine.assess_package(
        case.materials, project_type=case.project.project_type,
    )
    case.material_reviews = engine.evaluate_materials(
        case.materials, findings=case.findings, ocr_fields=case.ocr_fields,
        project=case.project,
    )
    case.catastrophe_assessment = CatastropheAssessmentEngine().assess(
        component=case.component_profile, weather=case.weather_profile,
    )
    case.decision = DecisionEngine().decide(case)
    print(json.dumps({
        "scope": "SIMULATED：只验证结构化规则，不验证真实图片识别或真实气象数据",
        "scene_photo_count": case.package_assessment.image_count,
        "document_image_count": case.package_assessment.document_image_count,
        "minimum_gate_passed": case.package_assessment.minimum_gate_passed,
        "decision": case.decision.decision.value,
        "missing_requirements": case.decision.missing_requirements,
    }, ensure_ascii=False, indent=2))
    if case.decision.decision.value != "accept":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
