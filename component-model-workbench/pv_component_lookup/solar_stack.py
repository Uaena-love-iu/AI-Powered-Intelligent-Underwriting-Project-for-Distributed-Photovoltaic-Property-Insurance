"""Read-only Solar Stack adapter with exact identity and datasheet missing-field fill."""

import hashlib
import ipaddress
import json
import re
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx

from .contracts import ComponentProfile, ProviderError, numeric_missing_fields
from .datasheet import extract_datasheet
from .naming import ComponentModelIndex, model_key

API_BASE = "https://partner-api.solar-stack.com"
API_FIELDS = {
    "maxLoadFrontPa": "front_static_load_pa",
    "maxLoadRearPa": "back_static_load_pa",
}
UI_FIELDS = ("wind_load_pa", "snow_load_pa")
_LOCK = threading.Lock()
_LAST_CALL = 0.0


def _safe_url(value: str) -> bool:
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
    ):
        return False
    if parsed.hostname.lower() in {"localhost", "metadata.google.internal"}:
        return False
    try:
        return ipaddress.ip_address(parsed.hostname).is_global
    except ValueError:
        return True


class SolarStackComponentProvider:
    def __init__(
        self,
        api_key: str,
        *,
        index: ComponentModelIndex | None = None,
        cache_dir: str | Path | None = None,
        transport=None,
        page_ocr=None,
        semantic_extractor=None,
        timeout: float = 30.0,
        cache_ttl_seconds: float = 86400,
        max_datasheets: int = 3,
    ) -> None:
        if not api_key:
            raise ValueError("Solar Stack key is required")
        self._key = api_key
        self.index = index
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.transport = transport
        self.page_ocr = page_ocr
        self.semantic_extractor = semantic_extractor
        self.timeout = timeout
        self.ttl = cache_ttl_seconds
        self.max_datasheets = max_datasheets
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    @property
    def name(self) -> str:
        return "solar-stack-with-datasheet-fallback"

    def _get(self, path: str, params: dict | None = None) -> dict:
        if not re.fullmatch(r"/v1/panels(?:/[A-Za-z0-9_-]+)?", path):
            raise ValueError("unsupported API path")
        signature = json.dumps([path, params or {}], sort_keys=True)
        filename = hashlib.sha256(signature.encode()).hexdigest() + ".json"
        cached = self.cache_dir / filename if self.cache_dir else None
        if cached and cached.exists() and time.time() - cached.stat().st_mtime < self.ttl:
            try:
                return json.loads(cached.read_text("utf-8"))
            except (OSError, ValueError):
                pass
        if self.transport is None:
            global _LAST_CALL
            with _LOCK:
                delay = max(0, 0.55 - (time.monotonic() - _LAST_CALL))
                if delay:
                    time.sleep(delay)
                _LAST_CALL = time.monotonic()
        try:
            with httpx.Client(
                timeout=self.timeout,
                transport=self.transport,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                response = client.get(
                    API_BASE + path,
                    params=params,
                    headers={"Authorization": "Bearer " + self._key, "Accept": "application/json"},
                )
                if response.status_code == 429:
                    raise ProviderError(
                        "Solar Stack rate/quota limit; retry after "
                        + str(response.headers.get("Retry-After", "unspecified"))
                    )
                if response.status_code == 404:
                    return {"data": None}
                response.raise_for_status()
                if len(response.content) > 8_000_000:
                    raise ValueError("API response too large")
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderError("Solar Stack request failed") from exc
        if cached:
            cached.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return payload

    def _search(self, params: dict, max_pages: int = 10) -> tuple[list[dict], bool]:
        rows = []
        for page in range(1, max_pages + 1):
            payload = self._get("/v1/panels", {**params, "perPage": 100, "page": page})
            rows.extend(payload.get("data") or [])
            pages = payload.get("meta", {}).get("pages", 1)
            if page >= pages:
                return rows, True
        return rows, False

    def _download(self, url: str) -> bytes:
        if not _safe_url(url):
            raise ProviderError("datasheet URL is not supported")
        digest = hashlib.sha256(url.encode()).hexdigest()
        cached = self.cache_dir / (digest + ".pdf") if self.cache_dir else None
        if cached and cached.exists() and time.time() - cached.stat().st_mtime < self.ttl:
            return cached.read_bytes()
        try:
            with httpx.Client(
                timeout=self.timeout,
                transport=self.transport,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                # No API credentials are ever sent to the datasheet host.
                for _ in range(4):
                    if not _safe_url(url):
                        raise ProviderError("datasheet redirect is unsafe")
                    with client.stream("GET", url) as response:
                        if response.is_redirect:
                            url = str(response.url.join(response.headers["Location"]))
                            continue
                        response.raise_for_status()
                        chunks = []
                        size = 0
                        for chunk in response.iter_bytes():
                            size += len(chunk)
                            if size > 20_000_000:
                                raise ProviderError("datasheet exceeds size limit")
                            chunks.append(chunk)
                        content = b"".join(chunks)
                        if not content.lstrip().startswith(b"%PDF-"):
                            raise ProviderError("datasheet response is not a PDF")
                        if cached:
                            cached.write_bytes(content)
                        return content
                raise ProviderError("too many datasheet redirects")
        except httpx.HTTPError as exc:
            raise ProviderError("datasheet download failed") from exc

    def _family_profile(self, model: str, families: list[dict]) -> ComponentProfile | None:
        if not families:
            return None
        brands = {x["manufacturer"] for x in families}
        notes = []
        for candidate in families[:8]:
            notes.append(
                candidate["manufacturer"]
                + "；系列 "
                + ", ".join(candidate["series_names"])
                + "；本次收录功率 "
                + ", ".join(map(str, candidate["observed_power_bins_w"]))
                + " W"
            )
        notes.append("仅找到保留变体的系列，输入完整型号尚未确认；未借用邻近功率参数。")
        return ComponentProfile(
            component_model=model,
            manufacturer=next(iter(brands)) if len(brands) == 1 else None,
            source_url=None,
            source_name="Solar Stack系列候选",
            retrieved_at=datetime.now(UTC),
            match_confidence=0,
            model_match_status="series_only" if len(brands) == 1 else "ambiguous",
            lookup_notes=notes,
        )

    def lookup(self, component_model: str) -> ComponentProfile | None:
        exact = self.index.exact(component_model) if self.index else []
        if len(exact) > 1:
            return ComponentProfile(
                component_model=component_model,
                source_url=None,
                source_name="Solar Stack同名候选",
                retrieved_at=datetime.now(UTC),
                match_confidence=0,
                model_match_status="ambiguous",
                lookup_notes=["完整型号对应多个厂家，需补充厂商身份。"],
            )
        if not exact:
            found, complete = self._search({"search": component_model})
            if not complete:
                raise ProviderError("Solar Stack candidate retrieval incomplete")
            exact = [x for x in found if model_key(x["name"]) == model_key(component_model)]
        if len(exact) > 1:
            return ComponentProfile(
                component_model=component_model,
                source_name="Solar Stack同名候选",
                retrieved_at=datetime.now(UTC),
                match_confidence=0,
                model_match_status="ambiguous",
                lookup_notes=["API返回多个完整型号候选，需核验厂商身份；未借用系列参数。"],
            )
        if len(exact) != 1:
            families = self.index.families(component_model) if self.index else []
            # Query actual API series names; don't retain an invalid input power filter.
            for candidate in families[:4]:
                bins = set(candidate["observed_power_bins_w"])
                for series_name in candidate["series_names"][:3]:
                    current, complete = self._search(
                        {"manufacturer": candidate["manufacturer"], "series": series_name}
                    )
                    bins.update(x["pmax"] for x in current)
                    if not complete:
                        raise ProviderError("Solar Stack family retrieval incomplete")
                candidate["observed_power_bins_w"] = sorted(bins)
            return self._family_profile(component_model, families)
        row = exact[0]
        detail = self._get("/v1/panels/" + row["id"]).get("data")
        if not detail:
            return None
        if (
            model_key(detail["name"]) != model_key(row["name"])
            or detail["manufacturer"] != row["manufacturer"]
        ):
            raise ProviderError("Solar Stack detail identity mismatch")
        series = detail["series"]
        source = series.get("sourceUrl") or detail.get("url")
        values = {"rated_power_w": detail.get("pmax")}
        evidence = {}
        sources = {"rated_power_w": detail.get("url") or source}
        notes = ["数据来源：Solar Stack https://www.solar-stack.com/；原资料按字段列出。"]
        for api_field, key in API_FIELDS.items():
            value = series.get(api_field)
            if isinstance(value, (float, int)) and not isinstance(value, bool) and value > 0:
                values[key] = float(value)
                sources[key] = source
                evidence[key] = [
                    {
                        "status": "found",
                        "value": value,
                        "unit": "mm"
                        if key == "hail_resistance_mm"
                        else "m/s"
                        if key == "hail_impact_velocity_m_s"
                        else "Pa",
                        "raw_label": api_field,
                        "source_url": source,
                        "source_kind": "api",
                        "load_basis": "unspecified",
                        "load_basis_verified": False,
                        "api_series": series["name"],
                    }
                ]
        template = None
        if self.index:
            families = self.index.families(detail["name"], detail["manufacturer"])
            family = next((x for x in families if series["name"] in x["series_names"]), None)
            if family:
                template = family["template"]
        urls = []
        if source and urlparse(source).path.lower().endswith(".pdf"):
            urls.append(source)
        urls.extend(x["url"] for x in detail.get("datasheets", []) if x.get("type") == "datasheet")
        urls = list(dict.fromkeys(urls))
        missing = numeric_missing_fields(values, evidence)
        seen_documents = set()
        conflicts = set()
        for url in urls[: self.max_datasheets]:
            if not missing:
                break
            try:
                pdf = self._download(url)
                document_hash = hashlib.sha256(pdf).hexdigest()
                if document_hash in seen_documents:
                    continue
                seen_documents.add(document_hash)
                extracted = extract_datasheet(
                    pdf,
                    source_url=url,
                    model=detail["name"],
                    template=template,
                    power_w=detail.get("pmax"),
                    page_ocr=self.page_ocr,
                    api_series_info=series,
                )
                if self.semantic_extractor and extracted.status not in {
                    "pdf_unreadable",
                    "encrypted_pdf",
                    "ocr_required",
                    "model_scope_unverified",
                    "document_version_conflict",
                }:
                    extracted = self.semantic_extractor(
                        pdf, url, detail["name"], template, detail.get("pmax"), extracted
                    )
                if extracted.status not in {"text_extracted", "document_truncated"}:
                    notes.append("规格书 " + extracted.status + "：" + url)
                    for key, records in extracted.evidence.items():
                        evidence.setdefault(key, []).extend(records)
                    continue
                parsed = extracted.values()
                for key, records in extracted.evidence.items():
                    evidence.setdefault(key, []).extend(records)
                    if any(record.get("status") == "conflict" for record in records):
                        conflicts.add(key)
                        values.pop(key, None)
                        notes.append(key + "在资料中存在条件或数值冲突，保留待核验。")
                for key, value in parsed.items():
                    if key in conflicts:
                        continue
                    if values.get(key) is None:
                        values[key] = value
                        sources[key] = url
                    elif abs(values[key] - value) > max(0.01, values[key] * 0.001):
                        notes.append(key + "存在API与资料数值/安装条件差异，未取最大值。")
                        values[key] = None
                        conflicts.add(key)
                        for record in evidence[key]:
                            record["status"] = "conflict"
                for key in missing:
                    if key not in extracted.evidence:
                        evidence.setdefault(key, []).append(
                            {
                                "status": "observed_not_found"
                                if extracted.status == "text_extracted"
                                else "unknown",
                                "source_kind": "datasheet",
                                "source_url": url,
                                "pages_read": extracted.pages_read,
                                "reason": "已检索可读资料，未定位该语义字段；不是该产品能力为零。"
                                if extracted.status == "text_extracted"
                                else "文档读取不完整，不能确认字段不存在。",
                            }
                        )
                missing = numeric_missing_fields(values, evidence)
            except ProviderError:
                notes.append("规格书读取未完成（下载/OCR/语义解析）：" + url)
        for key in missing:
            evidence.setdefault(key, []).append(
                {"status": "unknown", "reason": "未取得可适用的明确字段值"}
            )
        if values.get("front_static_load_pa") and values.get("snow_load_pa") is None:
            notes.append("已取得正面机械载荷，按机械映射值展示抗雪压；风雪对应及测试/设计口径单列待核实。")
        if values.get("back_static_load_pa") and values.get("wind_load_pa") is None:
            notes.append("已取得背面机械载荷，按机械映射值展示抗风吸力；未自动等同设计风荷载。")
        return ComponentProfile(
            component_model=detail["name"],
            manufacturer=detail["manufacturer"],
            source_url=source,
            source_name="Solar Stack / 对应datasheet",
            retrieved_at=datetime.now(UTC),
            match_confidence=1.0,
            model_match_status="exact",
            parameter_sources={k: v for k, v in sources.items() if v},
            parameter_evidence=evidence,
            lookup_notes=notes,
            market_version=", ".join(series.get("markets", [])),
            source_document_type="API元数据及对应规格书（按字段核验）",
            **values,
        )
