from dataclasses import dataclass
from time import sleep
from typing import Literal

import httpx

from app.contracts import Material


@dataclass(frozen=True, slots=True)
class MaterialInput:
    """传递给OCR和视觉Provider的材料内容。"""

    material: Material
    content: bytes
    ocr_task: Literal["auto", "watermark", "business"] = "auto"
def post_with_connect_retry(
    client: httpx.Client,
    url: str,
    **kwargs: object,
) -> httpx.Response:
    """Retry once only when the connection fails before a request is sent."""

    for attempt in range(2):
        try:
            return client.post(url, **kwargs)
        except (httpx.ConnectError, httpx.ConnectTimeout):
            if attempt == 1:
                raise
            sleep(0.25)

    raise AssertionError("unreachable")


class ProviderError(RuntimeError):
    """Provider调用失败时抛出的已脱敏异常。"""
