"""Run the fixed carport shading materials against the real local pipeline."""

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from uuid import uuid4

import httpx


FILENAMES = (
    "01_全景_正面平视_模拟_轻微树荫.png",
    "02_全景_俯拍_模拟_轻微树荫.png",
    "03_支架连接节点_模拟.png",
    "04_JA_Solar_JAM72S20-450-MR.jpg",
    "05_电气接地_模拟.png",
    "车棚顶.pdf",
)
DEFAULT_SUITE = Path(__file__).resolve().parents[1] / "测试素材/车棚树枝遮挡_稳定复核集_2026-10-16"


def check_result(case: dict, expected: dict) -> dict:
    materials = {item["material_id"]: item for item in case.get("materials", [])}
    panoramas = {mid for mid, item in materials.items() if item["category"] == "panorama"}
    shade = [item for item in case.get("findings", [])
             if item.get("category") == "minor_shading" and item.get("detection_status") == "detected"]
    boxed = {item["material_id"] for item in shade if item.get("bbox") is not None}
    review_ids = {item["material_id"] for item in case.get("material_reviews", [])
                  if item.get("requires_manual_review")}
    quantified = case.get("pv_load_screening_assessment")
    wind = quantified.get("wind", {}) if isinstance(quantified, dict) else {}
    snow = quantified.get("snow", {}) if isinstance(quantified, dict) else {}
    reasons = case.get("decision", {}).get("reasons", [])
    checks = {
        "manual_review": case.get("decision", {}).get("decision") == expected["expected_decision"],
        "shading_causes_review": bool(review_ids & panoramas) and any("遮挡" in reason for reason in reasons),
        "quantification": isinstance(quantified, dict) and quantified.get("mode") == "real"
                          and quantified.get("status") is not None
                          and isinstance(wind.get("load_pa", {}).get("max"), (int, float))
                          and isinstance(snow.get("load_pa", {}).get("max"), (int, float)),
        "both_panorama_boxes": len(boxed & panoramas) >= expected["required_panorama_evidence_count"],
        "material_hashes": len(materials) == len(expected["material_sha256"])
                           and all(item["sha256"] == expected["material_sha256"].get(item["file_name"])
                                   for item in materials.values()),
    }
    return {
        "case_id": case["case_id"], "checks": checks,
        "decision": case.get("decision", {}).get("decision"),
        "shade": [{key: item.get(key) for key in
                   ("material_id", "detection_status", "severity", "bbox", "evidence_text")}
                  for item in shade],
        "quant_status": quantified.get("status") if isinstance(quantified, dict) else None,
        "wind_load_pa": wind.get("load_pa"),
        "snow_load_pa": snow.get("load_pa"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", type=Path, default=DEFAULT_SUITE)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--timeout-seconds", type=int, default=360)
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be positive")
    suite = args.suite.resolve()
    manifest = json.loads((suite / "manifest.template.json").read_text())
    expected = json.loads((suite / "expected.json").read_text())
    for name in FILENAMES:
        digest = hashlib.sha256((suite / "上传材料" / name).read_bytes()).hexdigest()
        if digest != expected["material_sha256"][name]:
            raise ValueError(f"fixture hash changed: {name}")
    outputs = suite / "运行结果"
    outputs.mkdir(exist_ok=True)
    summaries = []
    with httpx.Client(base_url=args.base_url, timeout=45) as client:
        for index in range(1, args.runs + 1):
            current = {**manifest, "case_id": "SHADE-REVIEW-" + uuid4().hex[:12].upper()}
            files = [("files", (name, (suite / "上传材料" / name).read_bytes(),
                                "application/pdf" if name.endswith(".pdf") else
                                "image/jpeg" if name.endswith(".jpg") else "image/png"))
                     for name in FILENAMES]
            response = client.post("/api/v1/underwriting/jobs",
                                   data={"manifest": json.dumps(current, ensure_ascii=False)}, files=files)
            response.raise_for_status()
            job_id = response.json()["job_id"]
            print(f"run {index}/{args.runs}: {current['case_id']} job={job_id}", flush=True)
            deadline = time.monotonic() + args.timeout_seconds
            while True:
                job = client.get(f"/api/v1/underwriting/jobs/{job_id}").json()
                if job["status"] in ("completed", "failed"):
                    break
                if time.monotonic() > deadline:
                    raise TimeoutError(f"job still live: {job_id}")
                time.sleep(4)
            if job["status"] != "completed":
                raise RuntimeError(f"job failed: {job_id}: {job.get('error_code')}")
            case = job["result"]
            (outputs / f"{case['case_id']}.json").write_text(
                json.dumps(case, ensure_ascii=False, indent=2) + "\n")
            summary = check_result(case, expected)
            summaries.append(summary)
            print(json.dumps(summary, ensure_ascii=False), flush=True)
    (outputs / "三次运行核验.json").write_text(json.dumps(summaries, ensure_ascii=False, indent=2) + "\n")
    return 0 if all(all(item["checks"].values()) for item in summaries) else 1


if __name__ == "__main__":
    sys.exit(main())
