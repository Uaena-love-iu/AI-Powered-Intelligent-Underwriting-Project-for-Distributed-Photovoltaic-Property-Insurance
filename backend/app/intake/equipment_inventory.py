"""Read equipment rows from safe, non-macro Excel workbooks."""

from __future__ import annotations

import posixpath
import re
import zipfile
from io import BytesIO
from xml.etree import ElementTree

from app.contracts import EquipmentInventoryItem

_MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_NS = {"m": _MAIN_NS, "r": _REL_NS, "p": _PACKAGE_REL_NS}
_MODEL_PATTERN = re.compile(
    r"\b(?=[A-Z0-9./+_-]*[A-Z])(?=[A-Z0-9./+_-]*\d)"
    r"[A-Z0-9][A-Z0-9./+_-]{2,}\b",
    re.IGNORECASE,
)
_POWER_PATTERN = re.compile(
    r"(?<![A-Z0-9])(\d+(?:\.\d+)?)\s*(kW|W)(?![A-Z])",
    re.IGNORECASE,
)

_HEADER_ALIASES = {
    "item_category": ("一级类别", "类别", "分类"),
    "item_name": ("材料名称", "设备名称", "名称"),
    "item_code": ("编号", "设备编号", "编码"),
    "manufacturer": ("品牌", "厂家", "制造商"),
    "material": ("材质", "材料"),
    "specification": ("规格型号", "规格", "型号"),
    "unit": ("单位",),
    "quantity": ("数量",),
    "unit_price": ("单价",),
    "total_price": ("合计", "总价"),
    "tax_rate": ("税率",),
    "net_amount": ("不含税金额", "未税金额"),
    "remarks": ("备注", "说明"),
}


class EquipmentInventoryParseError(ValueError):
    """Raised when a workbook cannot be read as an equipment inventory."""


def parse_equipment_inventory(
    *,
    material_id: str,
    content: bytes,
) -> list[EquipmentInventoryItem]:
    """Extract tabular rows and source locations without executing workbook content."""

    try:
        with zipfile.ZipFile(BytesIO(content)) as archive:
            names = set(archive.namelist())
            if "xl/workbook.xml" not in names:
                raise EquipmentInventoryParseError("workbook_xml_missing")

            workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
            relationships = ElementTree.fromstring(
                archive.read("xl/_rels/workbook.xml.rels")
            )
            relationship_targets = {
                relation.attrib["Id"]: relation.attrib["Target"]
                for relation in relationships.findall("p:Relationship", _NS)
            }
            shared_strings = _read_shared_strings(archive, names)
            sheets = workbook.findall("m:sheets/m:sheet", _NS)
            if not sheets:
                raise EquipmentInventoryParseError("workbook_has_no_sheets")

            parsed: list[EquipmentInventoryItem] = []
            for sheet in sheets:
                relation_id = sheet.attrib.get(f"{{{_REL_NS}}}id")
                target = relationship_targets.get(relation_id or "")
                if target is None:
                    continue
                sheet_path = (
                    target.lstrip("/")
                    if target.startswith("/")
                    else posixpath.normpath(posixpath.join("xl", target))
                )
                if sheet_path not in names or not sheet_path.startswith("xl/"):
                    continue

                sheet_root = ElementTree.fromstring(archive.read(sheet_path))
                rows = sheet_root.findall("m:sheetData/m:row", _NS)
                header_index, column_map = _find_header(rows, shared_strings)
                if header_index is None:
                    continue

                for row in rows[header_index + 1 :]:
                    row_number = int(row.attrib.get("r", "0"))
                    values = _row_values(row, shared_strings)
                    item_values = {
                        name: values.get(column)
                        for name, column in column_map.items()
                    }
                    if not item_values.get("item_name") and not item_values.get(
                        "specification"
                    ):
                        continue

                    specification = item_values.get("specification")
                    category = item_values.get("item_category")
                    name = item_values.get("item_name")
                    model = _component_model(category, name, specification)
                    power = _rated_power(specification) if model else None
                    parsed.append(
                        EquipmentInventoryItem(
                            source_material_id=material_id,
                            worksheet_name=sheet.attrib.get("name", "Sheet"),
                            row_number=row_number,
                            **item_values,
                            normalized_component_model=model,
                            rated_power_w=power,
                        )
                    )

            if not parsed:
                raise EquipmentInventoryParseError("inventory_rows_not_found")
            return parsed
    except EquipmentInventoryParseError:
        raise
    except (KeyError, OSError, ValueError, zipfile.BadZipFile, ElementTree.ParseError) as exc:
        raise EquipmentInventoryParseError("workbook_could_not_be_parsed") from exc


def _read_shared_strings(
    archive: zipfile.ZipFile,
    names: set[str],
) -> list[str]:
    if "xl/sharedStrings.xml" not in names:
        return []
    root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
    return [
        "".join(node.text or "" for node in item.findall(".//m:t", _NS)).strip()
        for item in root.findall("m:si", _NS)
    ]


def _row_values(
    row: ElementTree.Element,
    shared_strings: list[str],
) -> dict[int, str]:
    values: dict[int, str] = {}
    for cell in row.findall("m:c", _NS):
        reference = cell.attrib.get("r", "")
        column = _column_number(reference)
        if column is None:
            continue
        kind = cell.attrib.get("t")
        if kind == "inlineStr":
            value = "".join(
                node.text or "" for node in cell.findall(".//m:t", _NS)
            ).strip()
        else:
            value_node = cell.find("m:v", _NS)
            value = value_node.text.strip() if value_node is not None and value_node.text else ""
            if kind == "s" and value:
                try:
                    value = shared_strings[int(value)]
                except (IndexError, ValueError):
                    value = ""
        if value:
            values[column] = value
    return values


def _column_number(reference: str) -> int | None:
    match = re.match(r"([A-Z]+)", reference.upper())
    if match is None:
        return None
    number = 0
    for character in match.group(1):
        number = number * 26 + ord(character) - ord("A") + 1
    return number


def _find_header(
    rows: list[ElementTree.Element],
    shared_strings: list[str],
) -> tuple[int | None, dict[str, int]]:
    aliases = {
        re.sub(r"\s+", "", alias).casefold(): name
        for name, names in _HEADER_ALIASES.items()
        for alias in names
    }
    for index, row in enumerate(rows[:20]):
        labels = _row_values(row, shared_strings)
        mapped: dict[str, int] = {}
        for column, label in labels.items():
            key = re.sub(r"\s+", "", label).casefold()
            canonical = aliases.get(key)
            if canonical is not None:
                mapped.setdefault(canonical, column)
        if "item_name" in mapped and "specification" in mapped:
            return index, mapped
    return None, {}


def _component_model(
    category: str | None,
    name: str | None,
    specification: str | None,
) -> str | None:
    if not specification:
        return None
    description = f"{category or ''} {name or ''}"
    if not any(term in description for term in ("组件", "光伏板", "太阳能板")):
        return None
    match = _MODEL_PATTERN.search(specification)
    return match.group(0).rstrip("._-/+") if match else None


def _rated_power(specification: str | None) -> float | None:
    if not specification:
        return None
    match = _POWER_PATTERN.search(specification)
    if match is None:
        return None
    value = float(match.group(1))
    return value * 1000 if match.group(2).casefold() == "kw" else value
