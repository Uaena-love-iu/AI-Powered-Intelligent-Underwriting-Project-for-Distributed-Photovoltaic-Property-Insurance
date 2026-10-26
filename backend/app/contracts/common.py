from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ContractModel(BaseModel):
    """所有核保数据对象共同使用的基础模型。"""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )


class Bbox(ContractModel):
    """使用0到1归一化坐标表示图片中的风险区域。"""

    x_min: float = Field(ge=0.0, le=1.0)
    y_min: float = Field(ge=0.0, le=1.0)
    x_max: float = Field(ge=0.0, le=1.0)
    y_max: float = Field(ge=0.0, le=1.0)
    coordinate_space: Literal["normalized_0_1"]

    @model_validator(mode="after")
    def validate_coordinate_order(self) -> Self:
        if self.x_min >= self.x_max:
            raise ValueError("x_min must be less than x_max")
        if self.y_min >= self.y_max:
            raise ValueError("y_min must be less than y_max")
        return self