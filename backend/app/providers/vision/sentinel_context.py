"""Opt-in, non-binding VLM descriptions of Sentinel-2 surroundings."""

from __future__ import annotations

import base64

import httpx
from pydantic import ValidationError

from app.contracts import SentinelEnvironmentAnalysis
from app.settings import VlmSettings

from ..common import ProviderError, post_with_connect_retry

SYSTEM_PROMPT = """You describe broad land-cover context in a Copernicus Sentinel-2 L2A image.
Return only a JSON object with keys summary and observations. Each observation must
have category, presence, confidence, and evidence. Allowed categories are
cultivated_fields, tree_cover, water, built_up, bare_or_mountain, other, uncertain.
Allowed presence values are present, possible, not_observed. Confidence is 0 to 1.
Use only patterns visible in this image. At 10 m pixel size, do not claim parcel
boundaries, the exact insured installation, photovoltaic panels, livestock or
aquaculture operations, ownership, structure condition, or current site status.
When clouds, shadows, mixed land cover, or scale prevent a reliable description,
use category=uncertain and presence=possible. Never use uncertain as a presence
value. Do not make an underwriting decision or recommend rejection.
"""


class SentinelContextAnalyzer:
    def __init__(
        self,
        settings: VlmSettings,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._transport = transport

    def analyze(self, png: bytes) -> SentinelEnvironmentAnalysis:
        encoded = base64.b64encode(png).decode("ascii")
        payload = {
            "model": self._settings.model,
            "temperature": 0,
            "enable_thinking": False,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "Describe broad visible land-cover patterns and list "
                                "uncertainty explicitly. This is auxiliary context "
                                "for a human reviewer only."
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{encoded}"},
                        },
                    ],
                },
            ],
        }
        try:
            with httpx.Client(
                timeout=httpx.Timeout(
                    self._settings.timeout_seconds,
                    connect=min(self._settings.timeout_seconds, 45.0),
                ),
                trust_env=self._settings.use_system_proxy,
                transport=self._transport,
            ) as client:
                response = post_with_connect_retry(
                    client,
                    f"{self._settings.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self._settings.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                response.raise_for_status()
                content = response.json()["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise ProviderError("VLM returned a non-text Sentinel context result")
            return SentinelEnvironmentAnalysis.model_validate_json(content)
        except ProviderError:
            raise
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError, ValidationError) as exc:
            raise ProviderError("VLM Sentinel context analysis failed validation or request") from exc
