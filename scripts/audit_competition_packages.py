"""Preflight local competition packages without reading image contents."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"}
CERTIFICATE_TOKENS = ("备案", "备案证", "登记证", "filing", "certificate")
RULE_TOKENS = ("规则", "自核", "高风险行业", "行业清单")
IGNORED_NAMES = {".ds_store", "thumbs.db", "desktop.ini"}


def _files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return [
        path
        for path in directory.rglob("*")
        if path.is_file() and path.name.casefold() not in IGNORED_NAMES
    ]


def _package_dir(root: Path) -> Path:
    outbound = root / "外发"
    return outbound if outbound.is_dir() else root


def audit_packages(root: Path, minimum_images: int = 5) -> dict[str, Any]:
    root = root.expanduser().resolve()
    package_root = _package_dir(root)
    packages: dict[str, Any] = {}
    for suite in ("A", "B", "C"):
        directory = package_root / suite
        files = _files(directory)
        images = [path for path in files if path.suffix.casefold() in IMAGE_SUFFIXES]
        certificates = [
            path.name
            for path in files
            if any(token in path.name.casefold() for token in CERTIFICATE_TOKENS)
            and path.suffix.casefold() in IMAGE_SUFFIXES | {".pdf"}
        ]
        spreadsheets = [path.name for path in files if path.suffix.casefold() in {".xlsx", ".xls"}]
        missing: list[str] = []
        if not directory.is_dir():
            missing.append("测试包目录不存在")
        if len(images) < minimum_images:
            missing.append(f"图片不足：{len(images)}/{minimum_images}")
        if not certificates:
            missing.append("未按文件名识别到备案证，请人工核实")
        packages[suite] = {
            "directory_exists": directory.is_dir(),
            "image_count": len(images),
            "minimum_images": minimum_images,
            "certificate_filename_matches": certificates,
            "spreadsheet_files": spreadsheets,
            "panorama_views": "需人工标注；脚本不根据文件名推断视角",
            "missing_or_manual_checks": missing,
            "automated_file_gate_passed": not missing,
        }

    rule_files = [
        path.name
        for path in _files(root)
        if path.suffix.casefold() in {".xlsx", ".xls", ".pdf", ".docx", ".doc"}
        and any(token.casefold() in path.name.casefold() for token in RULE_TOKENS)
        and path.parent not in {package_root / "A", package_root / "B", package_root / "C"}
    ]
    return {
        "competition_root": str(root),
        "package_root": str(package_root),
        "minimum_images_per_package": minimum_images,
        "official_rule_or_list_filename_matches": sorted(set(rule_files)),
        "official_rules_found_by_filename": bool(rule_files),
        "rule_content_verified": False,
        "packages": packages,
        "all_automated_file_gates_passed": all(
            result["automated_file_gate_passed"] for result in packages.values()
        ),
        "limitations": [
            "仅检查文件存在性、扩展名和文件名，不读取图片或证件内容。",
            "文件名匹配不能证明证件有效，也不能确认全景、俯拍等视角；需人工复核。",
            "本预检不替代模型推理、人工标注或 A/B/C 盲测评测。",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="比赛资料根目录（可包含外发/A、B、C）")
    parser.add_argument("--minimum-images", type=int, default=5)
    parser.add_argument("--output", type=Path, help="可选 JSON 报告路径")
    args = parser.parse_args()
    if args.minimum_images < 1:
        parser.error("--minimum-images must be at least 1")
    if not args.root.is_dir():
        print(f"目录不存在：{args.root}", file=sys.stderr)
        return 2
    report = audit_packages(args.root, minimum_images=args.minimum_images)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0 if report["all_automated_file_gates_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
