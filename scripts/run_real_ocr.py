from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import sys
from pathlib import Path

from app.contracts import (
    Material,
    MaterialCategory,
    MaterialParseStatus,
    MaterialQualityStatus,
)
from app.providers import (
    CompatibleOcrProvider,
    MaterialInput,
    PdfPageOcrProvider,
    ProviderError,
)
from app.settings import (
    ProviderConfigurationError,
    VlmSettings,
)

SUPPORTED_MEDIA_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/png",
}


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "使用真实多模态模型识别一份光伏投保材料。"
        ),
    )
    parser.add_argument(
        "material",
        type=Path,
        help="待识别的 JPEG、PNG 或 PDF 文件路径。",
    )
    parser.add_argument(
        "--category",
        choices=[
            category.value
            for category in MaterialCategory
        ],
        default=(
            MaterialCategory
            .COMPONENT_NAMEPLATE
            .value
        ),
        help="材料类别，默认为 component_nameplate。",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "outputs/real_ocr_result.json"
        ),
        help="OCR 结果 JSON 的保存路径。",
    )
    return parser.parse_args()


def detect_media_type(
    material_path: Path,
) -> str:
    media_type, _ = mimetypes.guess_type(
        material_path.name
    )

    if media_type == "image/jpg":
        media_type = "image/jpeg"

    if media_type not in SUPPORTED_MEDIA_TYPES:
        supported = ", ".join(
            sorted(SUPPORTED_MEDIA_TYPES)
        )
        raise ValueError(
            "不支持的材料格式："
            f"{media_type or 'unknown'}；"
            f"当前支持：{supported}"
        )

    return media_type


def main() -> int:
    arguments = parse_arguments()
    material_path = (
        arguments.material
        .expanduser()
        .resolve()
    )
    output_path = (
        arguments.output
        .expanduser()
        .resolve()
    )

    if not material_path.is_file():
        print(
            f"材料不存在：{material_path}",
            file=sys.stderr,
        )
        return 1

    try:
        content = material_path.read_bytes()

        if not content:
            raise ValueError("材料文件为空")

        media_type = detect_media_type(
            material_path
        )
        settings = (
            VlmSettings.from_environment()
        )

        material = Material(
            material_id="material-real-ocr-001",
            category=MaterialCategory(
                arguments.category
            ),
            file_name=material_path.name,
            media_type=media_type,
            sha256=hashlib.sha256(
                content
            ).hexdigest(),
            captured_at=None,
            longitude=None,
            latitude=None,
            quality_status=(
                MaterialQualityStatus.USABLE
            ),
            quality_confidence=1.0,
            quality_issues=[],
            parse_status=(
                MaterialParseStatus.SUCCESS
            ),
        )

        material_input = MaterialInput(
            material=material,
            content=content,
        )

        image_provider = CompatibleOcrProvider(
            settings=settings,
        )
        provider = PdfPageOcrProvider(
            delegate=image_provider,
        )

        print(f"Model: {provider.model_name}")
        print(f"Material: {material_path}")
        print(f"Media type: {media_type}")
        print(
            f"Category: {material.category.value}"
        )
        print("正在调用 OCR 模型……")

        fields = provider.extract(
            material_input
        )

        result = {
            "model": provider.model_name,
            "material_id": material.material_id,
            "file_name": material.file_name,
            "media_type": material.media_type,
            "category": material.category.value,
            "field_count": len(fields),
            "fields": [
                field.model_dump(mode="json")
                for field in fields
            ],
        }

        output_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        output_path.write_text(
            json.dumps(
                result,
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )

    except (
        OSError,
        ProviderConfigurationError,
        ProviderError,
        ValueError,
    ) as exc:
        print(
            f"调用失败：{exc}",
            file=sys.stderr,
        )
        return 1

    print(
        f"识别完成，共提取 {len(fields)} 个字段。"
    )
    print(f"结果已保存：{output_path}")
    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
