"""Shared evidence boundary for simulated and test imagery."""

SIMULATION_EVIDENCE_RULE = (
    "【模拟图片证据边界】图片出现‘模拟’‘测试’‘SIMULATED / TEST ONLY’等标识，"
    "或画面呈现合成、示意风格，只说明材料来源或用途；不得仅因此认定图片质量不合格、"
    "现场有风险、需要补拍或人工复核，也不得把标识框为风险部位。"
    "应继续依据画面中独立可见的具体对象判断：真实可见的遮挡、通道阻塞、模糊、"
    "过曝、欠曝或关键区域缺失仍按对应检查项报告，并明确具体证据。"
    "若只有模拟/测试标识而没有相应风险对象，不能输出 detected 风险。"
)


def ensure_simulation_rule(prompt: str) -> str:
    if "【模拟图片证据边界】" in prompt:
        return prompt
    return prompt.rstrip() + "\n\n" + SIMULATION_EVIDENCE_RULE
