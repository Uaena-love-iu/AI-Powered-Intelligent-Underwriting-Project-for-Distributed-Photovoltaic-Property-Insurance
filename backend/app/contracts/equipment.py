from pydantic import Field

from .common import ContractModel


class EquipmentInventoryItem(ContractModel):
    """A parsed row from a submitted equipment inventory workbook."""

    source_material_id: str = Field(min_length=1)
    worksheet_name: str = Field(min_length=1)
    row_number: int = Field(ge=1)
    item_category: str | None = None
    item_name: str | None = None
    item_code: str | None = None
    manufacturer: str | None = None
    material: str | None = None
    specification: str | None = None
    unit: str | None = None
    quantity: str | None = None
    unit_price: str | None = None
    total_price: str | None = None
    tax_rate: str | None = None
    net_amount: str | None = None
    remarks: str | None = None
    normalized_component_model: str | None = None
    rated_power_w: float | None = Field(default=None, gt=0)
