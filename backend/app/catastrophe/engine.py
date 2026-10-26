"""Project adapter for the delivered component workbench; no legacy risk formulas."""
from app.contracts import CatastropheAssessment, ExpectedLossRisk, ResistanceLevel


class CatastropheAssessmentEngine:
    name = "component-workbench-assessment"

    def __init__(self, config=None):
        # Accept older callers without applying the retired ratio thresholds.
        pass

    def assess(self, *, component, weather=None, module_result=None):
        result = module_result or (component.workbench_result if component else {})
        profile = result.get("profile") or {}
        fields = profile.get("ui_fields", {})
        factors = []
        for key, label in (("wind_load_pa", "抗风"), ("snow_load_pa", "抗雪")):
            item = fields.get(key, {})
            value = item.get("value")
            factors.append(f"{label}：" + (f"{value:g} {item.get('unit', '')}" if value is not None else "未取得")
                           + ("；" + item["mapping_note"] if item.get("mapping_note") else ""))
        state = result.get("status", "not_found")
        return CatastropheAssessment(
            module="component-workbench-v1", module_result=result,
            resistance_level=ResistanceLevel.UNKNOWN,
            expected_loss_risk=ExpectedLossRisk.UNKNOWN,
            factors=factors,
            explanation="组件抗灾模块已按完整型号查询并保存风、雪参数、机械载荷及来源证据。"
                        "机械映射值按原模块口径展示；本交付模块不输出项目风险等级或出险概率，综合承保仍需复核。",
            triggered_rule_ids=["CW-" + state.upper().replace("_", "-")],
            requires_manual_review=True,
        )

    def comparisons(self, component, weather):
        # Retained only for callers of the old method; never run old formulas.
        return []
