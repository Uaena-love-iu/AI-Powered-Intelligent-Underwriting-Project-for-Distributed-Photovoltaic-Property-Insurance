from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

from app.contracts import (
    EquipmentInventoryItem,
    InstallationType,
    ProjectInfo,
    ProjectType,
)
from app.intake.equipment_inventory import parse_equipment_inventory
from app.intake.files import XLSX_MEDIA_TYPE, inspect_file
from app.main import app
from app.pipeline.core import UnderwritingPipeline
from app.providers.vision.classification import classify_material
from fastapi.testclient import TestClient


def _inline_cell(reference: str, value: str) -> str:
    return (
        f'<c r="{reference}" t="inlineStr"><is><t>{value}</t></is></c>'
    )


def _workbook_bytes(extra_entries: list[tuple[str, str]] | None = None,
                    model: str = "JAM72D42-630W") -> bytes:
    rows = [
        ["一级类别", "材料名称", "编号", "品牌", "规格型号", "单位", "数量", "单价", "合计"],
        ["组件", "光伏板", "gc001", "晶澳", f"{model}，8525块", "块", "8525", "516.6", "4404015"],
        ["组件", "光伏板", "gc001", "晶澳", "625", "块", "324", "562.5", "182250"],
    ]
    sheet_rows = []
    for row_number, row in enumerate(rows, start=1):
        cells = "".join(
            _inline_cell(f"{chr(64 + column)}{row_number}", value)
            for column, value in enumerate(row, start=1)
        )
        sheet_rows.append(f'<row r="{row_number}">{cells}</row>')
    sheet = (
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<sheetData>{''.join(sheet_rows)}</sheetData></worksheet>"
    )
    workbook = (
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="设备清单" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    relationships = (
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="worksheet" Target="worksheets/sheet1.xml"/>'
        "</Relationships>"
    )
    content_types = (
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="xml" ContentType="application/xml"/>'
        "</Types>"
    )
    stream = BytesIO()
    with ZipFile(stream, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", relationships)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
        for name, value in extra_entries or []:
            archive.writestr(name, value)
    return stream.getvalue()


def test_xlsx_device_list_is_inspected_by_file_content() -> None:
    result = inspect_file(
        file_name="equipment.xlsx",
        declared_media_type=XLSX_MEDIA_TYPE,
        content=_workbook_bytes(),
    )

    assert result.status == "accepted"
    assert result.media_type == XLSX_MEDIA_TYPE
    assert result.issues == []


def test_xlsx_is_classified_from_parsed_rows() -> None:
    result = classify_material(_workbook_bytes(), XLSX_MEDIA_TYPE, None)

    assert result["category"] == "equipment_inventory"
    assert result["confidence"] == 0.99


def test_xlsx_classification_endpoint_does_not_require_vision_model() -> None:
    response = TestClient(app).post(
        "/api/v1/vision/lab/classify",
        files={"file": ("equipment.xlsx", _workbook_bytes(), XLSX_MEDIA_TYPE)},
    )

    assert response.status_code == 200, response.text
    assert response.json()["category"] == "equipment_inventory"


def test_corrupt_zip_is_not_accepted_as_xlsx() -> None:
    result = inspect_file(
        file_name="equipment.xlsx",
        declared_media_type=XLSX_MEDIA_TYPE,
        content=b"PK\x03\x04not-a-workbook",
    )

    assert result.status == "rejected"
    assert result.issues == ["corrupt_xlsx"]


def test_xlsx_with_path_traversal_entry_is_rejected() -> None:
    result = inspect_file(
        file_name="equipment.xlsx",
        declared_media_type=XLSX_MEDIA_TYPE,
        content=_workbook_bytes([("../outside.xml", "unsafe")]),
    )

    assert result.status == "rejected"
    assert result.issues == ["invalid_xlsx_archive_path"]


def test_equipment_inventory_rows_are_extracted_with_component_model() -> None:
    items = parse_equipment_inventory(
        material_id="inventory-001",
        content=_workbook_bytes(),
    )

    assert len(items) == 2
    assert isinstance(items[0], EquipmentInventoryItem)
    assert items[0].worksheet_name == "设备清单"
    assert items[0].row_number == 2
    assert items[0].item_name == "光伏板"
    assert items[0].manufacturer == "晶澳"
    assert items[0].normalized_component_model == "JAM72D42-630W"
    assert items[0].rated_power_w == 630
    assert items[0].quantity == "8525"
    assert items[1].normalized_component_model is None


def test_inventory_only_fills_project_model_when_unique() -> None:
    project = ProjectInfo(
        project_name=None,
        insured_name=None,
        project_type=ProjectType.UNKNOWN,
        installation_type=InstallationType.UNKNOWN,
        site_address=None,
        longitude=None,
        latitude=None,
        proposed_start_date=None,
        component_model=None,
    )
    record = EquipmentInventoryItem(
        source_material_id="inventory-001",
        worksheet_name="设备清单",
        row_number=2,
        item_category="组件",
        item_name="光伏板",
        normalized_component_model="JAM72D42-630W",
        rated_power_w=630,
    )

    updated = UnderwritingPipeline._apply_inventory_project_fields(project, [record])
    ambiguous = UnderwritingPipeline._apply_inventory_project_fields(
        project,
        [record, record.model_copy(update={"row_number": 3, "normalized_component_model": "OTHER-600W"})],
    )

    assert updated.component_model == "JAM72D42-630W"
    assert ambiguous.component_model is None
