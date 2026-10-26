"""Manufacturer scoped model templates; a family match never proves existence."""

import json
import re
from collections import defaultdict
from pathlib import Path


def model_key(value: str) -> str:
    value = value.translate(
        str.maketrans(
            {
                "－": "-",
                "–": "-",
                "—": "-",
                "‑": "-",
                "／": "/",
                "（": "(",
                "）": ")",
                "．": ".",
            }
        )
    )
    # OCR often inserts a line-break/space around punctuation. Preserve the raw
    # input elsewhere; normalize only spacing, never letters or power digits.
    value = re.sub(r"\s*([/\-])\s*", r"\1", value)
    return re.sub(r"\s+", " ", value).strip().casefold()


class ComponentModelIndex:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.rows = payload["models"]
        self.rules = payload["rules"]
        self._exact: dict[str, list[dict]] = defaultdict(list)
        for row in self.rows:
            self._exact[model_key(row["name"])].append(row)
        self._patterns = [(rule, re.compile(rule["regex"], re.IGNORECASE)) for rule in self.rules]
        self._by_brand = defaultdict(list)
        for item in self._patterns:
            self._by_brand[model_key(item[0]["manufacturer"])].append(item)

    @classmethod
    def from_file(cls, path: str | Path) -> "ComponentModelIndex":
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    def exact(self, model: str, manufacturer: str | None = None) -> list[dict]:
        return [
            row
            for row in self._exact.get(model_key(model), [])
            if manufacturer is None or model_key(row["manufacturer"]) == model_key(manufacturer)
        ]

    def families(self, model: str, manufacturer: str | None = None) -> list[dict]:
        candidates = []
        # Case/width normalization is a candidate comparison, preserving punctuation.
        normalized = model_key(model)
        choices = (
            self._by_brand.get(model_key(manufacturer), []) if manufacturer else self._patterns
        )
        for rule, pattern in choices:
            match = pattern.fullmatch(normalized)
            if not match:
                continue
            value = match.groupdict().get("power")
            candidates.append({**rule, "input_power_w": float(value) if value else None})
        return candidates
