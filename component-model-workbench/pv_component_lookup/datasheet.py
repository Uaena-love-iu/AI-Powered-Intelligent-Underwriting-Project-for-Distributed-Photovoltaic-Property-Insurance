"""Evidence-preserving datasheet extraction: units, direction and load basis matter."""

import hashlib
import math
import re
from dataclasses import dataclass, field

import pymupdf as fitz

from .document_review import VERIFIED_DOCUMENT_METADATA

TERMS = {
    "wind_load_pa": [
        r"(?:(?:design|test(?:ed)?)\s+)?wind\s+(?:load|pressure|suction)",
        r"风(?:荷载|载荷|载|压)",
        r"抗风(?:荷载|载荷|压)",
        r"Windlast",
        r"charge\s+(?:de\s+)?vent",
        r"carga\s+(?:de\s+)?(?:viento|vento)",
        r"carico\s+(?:del\s+)?vento",
    ],
    "snow_load_pa": [
        r"(?:(?:design|test(?:ed)?)\s+)?snow\s+(?:load|pressure)",
        r"雪(?:荷载|载荷|载|压)",
        r"抗雪(?:荷载|载荷|压)",
        r"Schneelast",
        r"charge\s+(?:de\s+)?neige",
        r"carga\s+(?:de\s+)?(?:nieve|neve)",
        r"carico\s+(?:di\s+)?neve",
    ],
    "front_static_load_pa": [
        r"(?:front(?:\s+side)?|positive)\s+(?:static\s+|mechanical\s+|test\s+|design\s+)*(?:load|pressure)",
        r"(?:load|pressure)\s*[,:(]?\s*(?:front|positive)\s*\)?",
        r"正面(?:静态|机械|试验|设计|最大|载荷|荷载|压力)*",
        r"Vorderseite",
        r"face\s+avant",
    ],
    "back_static_load_pa": [
        r"(?:back(?:\s+side)?|rear(?:\s+side)?|negative)\s+(?:static\s+|mechanical\s+|test\s+|design\s+)*(?:load|pressure)",
        r"(?:load|pressure)\s*[,:(]?\s*(?:back|rear|negative)\s*\)?",
        r"背面(?:静态|机械|试验|设计|最大|载荷|荷载|压力)*",
        r"R[üu]ckseite",
        r"face\s+arri[eè]re",
    ],
    "hail_resistance_mm": [
        r"hail(?:stone)?\s+(?:resistance|impact|test|diameter|size)",
        r"hailstones?",
        r"(?:抗冰雹|耐冰雹|冰雹|抗雹|耐雹)(?:直径|冲击|试验|测试|尺寸)?",
        r"Hagel(?:best[aä]ndigkeit|test|korn)?",
        r"gr[eê]le",
        r"granizo",
        r"grandine",
    ],
}
LOAD_FACTORS = {
    "pa": 1.0,
    "n/m2": 1.0,
    "kpa": 1000.0,
    "kn/m2": 1000.0,
    "psf": 47.88025898033584,
    "lb/ft2": 47.88025898033584,
    "lbs/ft2": 47.88025898033584,
}
LENGTH_FACTORS = {"mm": 1.0, "cm": 10.0, "inch": 25.4, "inches": 25.4, "in": 25.4, '"': 25.4}
SPEED_FACTORS = {"m/s": 1.0, "mps": 1.0, "km/h": 1 / 3.6, "mph": 0.44704, "ft/s": 0.3048}
NUMBER = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:[.,]\d+)?"
LOAD_VALUE = re.compile(
    r"(?P<number>"
    + NUMBER
    + r")\s*(?P<unit>kPa|Pa|kN\s*/\s*m[²2]|N\s*/\s*m[²2]|psf|lbs?\s*/\s*ft[²2])\b",
    re.IGNORECASE,
)
LENGTH_VALUE = re.compile(
    r"(?P<number>" + NUMBER + r')\s*(?P<unit>mm|cm|inches|inch|in\b|")', re.IGNORECASE
)
SPEED_VALUE = re.compile(
    r"(?P<number>" + NUMBER + r")\s*(?P<unit>m\s*/\s*s|mps|km\s*/\s*h|mph|ft\s*/\s*s)\b",
    re.IGNORECASE,
)
ALL_LABELS = re.compile(
    "|".join("(?:" + term + ")" for terms in TERMS.values() for term in terms), re.IGNORECASE
)
NON_HAIL_LABELS = re.compile(
    "|".join(
        "(?:" + term + ")"
        for key, terms in TERMS.items()
        if key != "hail_resistance_mm"
        for term in terms
    ),
    re.IGNORECASE,
)
OTHER_PROPERTIES = re.compile(
    r"\b(?:weight|dimensions?|thickness|frame|cables?|connectors?|junction|voltage|current)\b|重量|尺寸|厚度|边框|电压|电流",
    re.IGNORECASE,
)


def numeric(value: str) -> float:
    if re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?", value):
        value = value.replace(",", "")
    elif "," in value and "." not in value:
        value = value.replace(",", ".")
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ValueError("non-positive physical value")
    return result


def unit_key(value: str) -> str:
    return re.sub(r"\s+", "", value).lower().replace("²", "2")


def basis(text: str) -> str:
    design = bool(
        re.search(
            r"\bdesign\s+(?:(?:wind|snow|static|mechanical)\s+)*load\b|设计(?:荷载|载荷)|Bemessungslast",
            text,
            re.IGNORECASE,
        )
    )
    test = bool(
        re.search(
            r"\btest(?:ed)?\s+(?:(?:wind|snow|static|mechanical)\s+)*load\b|load\s+test|试验|测试|Pr[üu]flast",
            text,
            re.IGNORECASE,
        )
    )
    return "design" if design and not test else "test" if test and not design else "unspecified"


@dataclass
class DatasheetExtraction:
    evidence: dict[str, list[dict]] = field(default_factory=dict)
    pages_read: int = 0
    status: str = "text_extracted"
    sha256: str = ""
    page_texts: dict[int, str] = field(default_factory=dict)
    ocr_pages: list[int] = field(default_factory=list)

    def values(self) -> dict[str, float]:
        values = {}
        for key, records in self.evidence.items():
            usable = [x for x in records if x["status"] == "found"]
            distinct = {round(x["value"], 6) for x in usable}
            # Conflicting installation/market/test versions must not be reduced to max/min.
            if len(distinct) == 1:
                values[key] = usable[0]["value"]
            elif len(distinct) > 1:
                for record in usable:
                    record["status"] = "conflict"
        return values


def _record(
    result: DatasheetExtraction,
    key: str,
    match,
    text: str,
    label: str,
    page: int,
    source_url: str,
    context: str,
) -> None:
    factors = LENGTH_FACTORS if key == "hail_resistance_mm" else LOAD_FACTORS
    unit = unit_key(match["unit"])
    value = numeric(match["number"]) * factors[unit]
    if (key == "hail_resistance_mm" and not 1 <= value <= 150) or (
        key != "hail_resistance_mm" and not 20 <= value <= 100000
    ):
        return
    record = {
        "status": "found",
        "value": value,
        "unit": "mm" if key == "hail_resistance_mm" else "Pa",
        "raw_label": label,
        "raw_value": match["number"],
        "raw_unit": match["unit"],
        "quote": text[:500],
        "page": page,
        "source_url": source_url,
        "source_kind": "datasheet",
        "load_basis": basis(text),
        "load_direction": (
            "front"
            if key == "front_static_load_pa"
            else "back"
            if key == "back_static_load_pa"
            else "unspecified"
        ),
        "installation_conditions": context[:500],
        "document_sha256": result.sha256,
    }
    result.evidence.setdefault(key, []).append(record)


def extract_page(text: str, page: int, source_url: str, result: DatasheetExtraction) -> None:
    text = text.replace("−", "-").replace("₂", "2").replace("㎜", "mm")
    # A manufacturer's explicit snow/wind annotation establishes a hazard relation.
    # Direction alone never does. Keep the original test/design basis and direction.
    direction = r"(?:front(?:\s+side)?|rear(?:\s+side)?|back(?:\s+side)?|正面|背面)"
    hazard = r"(?:wind\s+and\s+snow\s+load|snow(?:\s+load)?|wind(?:\s+load)?|雪(?:载荷|荷载)?|风(?:载荷|荷载)?)"
    value = r"(?P<number>" + NUMBER + r")\s*(?P<unit>kPa|Pa)\b"
    patterns = [
        value + r"\s*(?:Max\s*)?(?:from\s+(?:the\s+)?)?(?P<direction>" + direction + r")\s*[（(]\s*(?P<hazard>" + hazard + r")\s*[）)]",
        r"(?P<direction>" + direction + r")\s*(?:(?:mechanical|static|test|design)\s+)*(?:load|pressure|机械载荷|机械荷载)?\s*[（(]\s*(?P<hazard>" + hazard + r")\s*[）)]\s*[:：]?\s*" + value,
        r"(?P<direction>" + direction + r")\s*(?:of|为|[:：])?\s*" + value + r"\s*[（(]\s*(?:i\.e\.\s*)?(?P<hazard>" + hazard + r")\s*[）)]",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            side = match['direction'].casefold()
            kind = match['hazard'].casefold()
            is_front = side.startswith(('front', '正面'))
            categories = ['wind','snow'] if 'and' in kind else ['snow'] if kind.startswith(('snow','雪')) else ['wind']
            # Do not infer a heading across unrelated rows or a previous bullet.
            prefix = text[max(0, match.start()-100):match.start()]
            prefix = re.split(r'[\n•;；]', prefix)[-1]
            quote = prefix + match.group()
            physical = 'front_static_load_pa' if is_front else 'back_static_load_pa'
            # A front load labelled wind AND snow remains a front mechanical value;
            # don't mistake it for the separate rear/uplift wind rating.
            targets = [physical] + (['snow_load_pa'] if 'snow' in categories else ['wind_load_pa'])
            for key in targets:
                before=len(result.evidence.get(key,[]))
                _record(result, key, match, quote, match.group(), page, source_url, quote)
                if len(result.evidence.get(key,[]))>before:
                    record = result.evidence[key][-1]
                    record.update(load_direction='front' if is_front else 'back',hazard_mapping='manufacturer_explicit',hazard_categories=categories)
    for key, terms in TERMS.items():
        if key.startswith("hail_"):
            continue
        for term in terms:
            for label in re.finditer(term, text, re.IGNORECASE):
                # Narrow, bounded window; stop before another property label.
                end = min(len(text), label.end() + 180)
                # A hazard name closing an annotation belongs to the value BEFORE
                # it, never to a different direction/condition in the next clause.
                if key in ('wind_load_pa','snow_load_pa') and re.match(r'\s*[)）]',text[label.end():]):
                    end=label.end()
                stopper = NON_HAIL_LABELS if key == "hail_resistance_mm" else ALL_LABELS
                next_label = stopper.search(text, label.end() + 1, end)
                if next_label:
                    end = next_label.start()
                other_property = OTHER_PROPERTIES.search(text, label.end() + 1, end)
                if other_property:
                    end = other_property.start()
                snippet = text[label.start() : end]
                if key in {"front_static_load_pa", "back_static_load_pa"} and re.search(
                    r"front\s*[/,]\s*(?:rear|back)|正面\s*[/,]\s*背面", snippet, re.IGNORECASE
                ):
                    continue
                matcher = LENGTH_VALUE if key == "hail_resistance_mm" else LOAD_VALUE
                previous = list(matcher.finditer(text[max(0, label.start() - 60) : label.start()]))
                adjacent = previous[-1] if previous else None
                if adjacent:
                    previous_start = max(0, label.start() - 60)
                    between = text[previous_start + adjacent.end() : label.start()]
                    if re.fullmatch(r"[ \t:(),-]{0,10}", between):
                        snippet = text[
                            previous_start + adjacent.start() : end
                            if key == "hail_resistance_mm"
                            else label.end()
                        ]
                        values = list(matcher.finditer(snippet))
                    else:
                        values = list(matcher.finditer(snippet))
                else:
                    values = list(matcher.finditer(snippet))
                if not values:
                    continue
                # Pa + a rounded parenthesized psf conversion is one measurement, not two tests.
                factors = LENGTH_FACTORS if key == "hail_resistance_mm" else LOAD_FACTORS
                if len(values) > 1 and len({unit_key(m["unit"]) for m in values}) > 1:
                    converted = [
                        numeric(m["number"]) * factors[unit_key(m["unit"])] for m in values
                    ]
                    if "(" in snippet and all(
                        abs(v - converted[0]) / converted[0] < 0.03 for v in converted
                    ):
                        values = values[:1]
                for match in values:
                    _record(
                        result,
                        key,
                        match,
                        snippet,
                        label.group(),
                        page,
                        source_url,
                        text[max(0, label.start() - 70) : min(len(text), end + 70)],
                    )
                if key == "hail_resistance_mm" and len(values) == 1:
                    speed = SPEED_VALUE.search(snippet)
                    if speed:
                        value = numeric(speed["number"]) * SPEED_FACTORS[unit_key(speed["unit"])]
                        result.evidence.setdefault("hail_impact_velocity_m_s", []).append(
                            {
                                "status": "found",
                                "value": value,
                                "unit": "m/s",
                                "raw_label": "hail impact velocity",
                                "raw_value": speed["number"],
                                "raw_unit": speed["unit"],
                                "quote": snippet[:500],
                                "page": page,
                                "source_url": source_url,
                                "source_kind": "datasheet",
                                "load_basis": "test",
                                "document_sha256": result.sha256,
                            }
                        )
    # Direction-labelled paired mechanical load rows, common in EN/CN datasheets.
    pair = re.compile(
        r"(?:mechanical|static|test|design|机械|静态|试验|设计)[^\n]{0,70}"
        r"(?:front\s*[/,]\s*(?:rear|back)|正面\s*[/,]\s*背面)"
        r"[^\d\n]{0,30}(?P<a>" + NUMBER + r")\s*(?P<u>kPa|Pa)?\s*[/,]\s*"
        r"(?P<b>" + NUMBER + r")\s*(?P<v>kPa|Pa)",
        re.IGNORECASE,
    )
    for match in pair.finditer(text):
        unit = match["u"] or match["v"]
        for key, raw, chosen_unit in [
            ("front_static_load_pa", match["a"], unit),
            ("back_static_load_pa", match["b"], match["v"]),
        ]:
            synthetic = {"number": raw, "unit": chosen_unit}
            _record(
                result,
                key,
                synthetic,
                match.group(),
                match.group(),
                page,
                source_url,
                match.group(),
            )


def text_covers_model(text: str, model: str, template: str | None, power_w: float | None) -> bool:
    """Require exact model or preserved non-power code plus the actual power column."""
    from .naming import model_key

    if re.search(
        r"(?<![a-z0-9])" + re.escape(model_key(model)) + r"(?![a-z0-9/_.+()-])", model_key(text)
    ):
        return True
    if not template or "{power}" not in template or power_w is None:
        return False
    left, right = template.split("{power}")
    # Datasheet headings use '/' '_' '-' or spaces between the same code tokens.
    # Dots/parentheses/letters inside tokens remain significant.
    parts = [part.strip(" -_/") for part in (left, right) if part.strip(" -_/")]
    if len(parts) == 2:
        family_pattern = re.escape(parts[0]) + r"[\s\dXxWw_/.–-]{0,70}" + re.escape(parts[1])
        if not re.search(family_pattern, text, re.IGNORECASE):
            return False
    elif not all(re.search(re.escape(part), text, re.IGNORECASE) for part in parts):
        return False
    power_token = str(power_w).removesuffix(".0")
    return bool(re.search(r"(?<!\d)" + re.escape(power_token) + r"(?!\d)", text))


def extract_datasheet(
    content: bytes,
    *,
    source_url: str,
    model: str,
    template: str | None,
    power_w: float | None,
    page_ocr=None,
    max_pages: int = 40,
    api_series_info: dict | None = None,
) -> DatasheetExtraction:
    result = DatasheetExtraction(sha256=hashlib.sha256(content).hexdigest())
    pages = []
    try:
        with fitz.open(stream=content, filetype="pdf") as document:
            if document.is_encrypted:
                result.status = "encrypted_pdf"
                return result
            for number in range(min(len(document), max_pages)):
                page = document[number]
                text = page.get_text("text", sort=True)
                if len(text.strip()) < 30 and page_ocr:
                    text = page_ocr(page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5)).tobytes("png"))
                    result.ocr_pages.append(number + 1)
                pages.append((number + 1, text))
            result.pages_read = len(pages)
            if len(document) > max_pages:
                result.status = "document_truncated"
    except (RuntimeError, ValueError, fitz.FileDataError):
        result.status = "pdf_unreadable"
        return result
    if not any(len(text.strip()) >= 30 for _, text in pages):
        result.status = "ocr_required"
        return result
    if not text_covers_model("\n".join(text for _, text in pages), model, template, power_w):
        result.status = "model_scope_unverified"
        return result
    expected_voltage = (api_series_info or {}).get("maxSystemVoltage")
    actual_voltages = []
    for _, text in pages:
        actual_voltages.extend(
            float(m[1])
            for m in re.finditer(
                r"(?:max(?:imum)?\s+system\s+voltage|最大系统电压)[^\d]{0,40}(\d{3,4})\s*V",
                text,
                re.IGNORECASE,
            )
        )
    reviewed = VERIFIED_DOCUMENT_METADATA.get(result.sha256)
    if reviewed and reviewed.get("maximum_system_voltage_v"):
        actual_voltages = [float(reviewed["maximum_system_voltage_v"])]
    if expected_voltage and actual_voltages and expected_voltage not in actual_voltages:
        result.status = "document_version_conflict"
        result.evidence["_document_version"] = [
            {
                "status": "conflict",
                "source_url": source_url,
                "reason": "API与文档最大系统电压不同，不能确认同一电压/市场版本。",
                "api_voltage_v": expected_voltage,
                "document_voltages_v": actual_voltages,
                "document_sha256": result.sha256,
                "manual_review": reviewed,
            }
        ]
        return result
    for number, text in pages:
        if len(pages) > 4 and not text_covers_model(text, model, template, power_w):
            continue
        extract_page(text, number, source_url, result)
    result.page_texts = dict(pages)
    # Deduplicate repeated lexical matches, preserving genuinely different evidence.
    for key, records in result.evidence.items():
        unique = {json_key(record): record for record in records}
        result.evidence[key] = list(unique.values())
        for record in result.evidence[key]:
            if record["page"] in result.ocr_pages:
                record["source_kind"] = "datasheet_ocr"
                record["requires_manual_review"] = True
    result.values()
    return result


def json_key(record: dict) -> tuple:
    return (record["page"], record["value"], record["quote"], record.get("load_basis"))
