"""Detect contradictory quality output without declaring the photograph passed."""
import re

from app.contracts import DetectionStatus, RiskCategory


_SIMULATION_MARKER = re.compile(
    r"模拟|仿真|测试(?:图|图片|照片|素材|标识|水印)|"
    r"SIMULATED|TEST\s*ONLY|合成图|示意图|AI生成",
    re.IGNORECASE,
)
_VISIBLE_QUALITY_DEFECT = re.compile(
    r"模糊|失焦|过曝|欠曝|过暗|反光|裁切|截断|像素化|分辨率过低|"
    r"遮挡|盖住|覆盖关键|看不清|无法看清|未拍到|未展示关键|关键区域缺失|"
    r"画面缺失|视角不符"
)


def simulation_only_quality_claim(text: str) -> bool:
    """The model cited simulation identity, but no visible image defect."""
    return bool(_SIMULATION_MARKER.search(text) and not _VISIBLE_QUALITY_DEFECT.search(text))


def quality_evidence_conflicts(finding):
    if (finding.category is not RiskCategory.IMAGE_QUALITY
            or finding.detection_status not in {DetectionStatus.DETECTED, DetectionStatus.UNCERTAIN}):
        return False
    text = finding.evidence_text or ""
    # Positive wording alone must not hide an explicit defect or missing region.
    if re.search(r"无法|不能|不清晰|不满足|不足|模糊|过曝|欠曝|遮挡|缺失|缺少|未展示|未覆盖", text):
        return False
    return bool(re.search(r"(?:图片|图像|照片)(?:本身)?(?:质量)?(?:清晰|满足检查要求|满足识别要求)|质量满足检查要求", text))
