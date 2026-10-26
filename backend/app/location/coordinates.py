"""Parse explicit decimal coordinates without guessing OCR digits or datums."""
import re
import unicodedata


def parse_decimal_coordinate(value: str, *, axis: str) -> float | None:
    if axis not in {"longitude", "latitude"}:
        raise ValueError("Unsupported coordinate axis")
    text = unicodedata.normalize("NFKC", value).strip().upper()
    match = re.fullmatch(
        r"\s*(东经|西经|北纬|南纬|E|W|N|S)?\s*([+-]?\d+(?:\.\d+)?)\s*(?:°|度)?\s*(东经|西经|北纬|南纬|E|W|N|S)?\s*",
        text,
    )
    if match is None:
        return None
    prefix, numeric, suffix = match.groups()
    if prefix and suffix and prefix != suffix:
        return None
    direction = prefix or suffix
    signs = ({"E": 1, "东经": 1, "W": -1, "西经": -1} if axis == "longitude"
             else {"N": 1, "北纬": 1, "S": -1, "南纬": -1})
    number = float(numeric)
    if direction:
        if direction not in signs:
            return None
        if numeric.startswith("-") and signs[direction] > 0:
            return None
        if numeric.startswith("+") and signs[direction] < 0:
            return None
        number = abs(number) * signs[direction]
    limit = 180 if axis == "longitude" else 90
    return number if abs(number) <= limit else None
