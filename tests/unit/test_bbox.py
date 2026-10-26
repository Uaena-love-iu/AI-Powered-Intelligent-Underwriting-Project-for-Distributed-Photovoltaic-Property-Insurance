import pytest
from app.contracts.common import Bbox
from pydantic import ValidationError


def test_bbox_accepts_valid_normalized_coordinates() -> None:
    bbox = Bbox(
        x_min=0.1,
        y_min=0.2,
        x_max=0.8,
        y_max=0.9,
        coordinate_space="normalized_0_1",
    )

    assert bbox.x_min == 0.1
    assert bbox.x_max == 0.8
    assert bbox.coordinate_space == "normalized_0_1"


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("x_min", -0.01),
        ("y_min", -0.01),
        ("x_max", 1.01),
        ("y_max", 1.01),
    ],
)
def test_bbox_rejects_coordinates_outside_normalized_range(
    field_name: str,
    invalid_value: float,
) -> None:
    payload = {
        "x_min": 0.1,
        "y_min": 0.2,
        "x_max": 0.8,
        "y_max": 0.9,
        "coordinate_space": "normalized_0_1",
    }
    payload[field_name] = invalid_value

    with pytest.raises(ValidationError):
        Bbox.model_validate(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {
            "x_min": 0.5,
            "y_min": 0.2,
            "x_max": 0.5,
            "y_max": 0.9,
            "coordinate_space": "normalized_0_1",
        },
        {
            "x_min": 0.8,
            "y_min": 0.2,
            "x_max": 0.1,
            "y_max": 0.9,
            "coordinate_space": "normalized_0_1",
        },
        {
            "x_min": 0.1,
            "y_min": 0.9,
            "x_max": 0.8,
            "y_max": 0.2,
            "coordinate_space": "normalized_0_1",
        },
    ],
)
def test_bbox_rejects_empty_or_reversed_area(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Bbox.model_validate(payload)


def test_bbox_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        Bbox(
            x_min=0.1,
            y_min=0.2,
            x_max=0.8,
            y_max=0.9,
            coordinate_space="normalized_0_1",
            pixel_width=1920,
        )