"""Explicit, case-scoped human facts. Draft values are never accepted here."""
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, field_validator, model_validator
from .common import ContractModel

PARAMETER_NAMES = {"rated_power_w": ("额定功率", "W", "not_applicable"),
                   "front_static_load_pa": ("正面机械载荷", "Pa", "front"),
                   "back_static_load_pa": ("背面机械载荷", "Pa", "back")}


class CaseParameter(ContractModel):
    value: float = Field(gt=0, allow_inf_nan=False)
    unit: Literal["W", "kW", "Pa", "kPa"]
    direction: Literal["front", "back", "not_applicable"]
    load_basis: Literal["test", "design", "unspecified"] = "unspecified"
    document_title: str = Field(min_length=2, max_length=200)
    document_version: str = Field(min_length=1, max_length=100)
    applicability: str = Field(min_length=2, max_length=2000)
    quote: str = Field(min_length=2, max_length=2000)
    page: str = Field(min_length=1, max_length=100)
    source_url: str | None = Field(default=None, max_length=2000)
    source_material_id: str | None = Field(default=None, min_length=1, max_length=200)
    source_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    installation_confirmed: bool = False

    @field_validator("value", mode="before")
    @classmethod
    def numeric_not_boolean(cls, value):
        if isinstance(value, bool):
            raise ValueError("数值不能为布尔值")
        return value

    @model_validator(mode="after")
    def evidence(self):
        if not self.source_url and not self.source_material_id:
            raise ValueError("每项参数需要来源链接或本轮上传的厂家资料")
        if self.source_url:
            url = urlparse(self.source_url)
            if url.scheme != "https" or not url.hostname or url.username or url.password:
                raise ValueError("来源链接必须是 HTTPS 且不含登录信息")
        if bool(self.source_material_id) != bool(self.source_sha256):
            raise ValueError("上传来源必须绑定文件摘要，替换后需要重新确认")
        return self


class CaseComponentReview(ContractModel):
    scope: Literal["case_only"] = "case_only"
    component_model: str = Field(min_length=2, max_length=100)
    manufacturer: str = Field(min_length=2, max_length=120)
    identity_material_id: str = Field(min_length=1, max_length=200)
    identity_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    model_reason: str = Field(min_length=2, max_length=2000)
    reviewer_name: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=2, max_length=2000)
    confirmed: Literal[True]
    parameters: dict[Literal["rated_power_w", "front_static_load_pa", "back_static_load_pa"], CaseParameter] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def dimensions(self):
        if any(char in self.component_model for char in "*?\r\n"):
            raise ValueError("必须填写单个完整型号，不接受通配符")
        for key, item in self.parameters.items():
            _, unit, direction = PARAMETER_NAMES[key]
            if item.unit not in (unit, "k" + unit) or item.direction != direction:
                raise ValueError(f"{key} 的单位或载荷方向不符")
            if key == "rated_power_w" and item.load_basis != "unspecified":
                raise ValueError("额定功率不使用载荷口径")
        return self
