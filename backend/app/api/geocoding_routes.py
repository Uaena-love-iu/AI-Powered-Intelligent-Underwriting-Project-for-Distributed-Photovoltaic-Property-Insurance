"""Address lookup for manual confirmation of a project's location."""

import os
from dataclasses import asdict
from typing import Annotated

from dotenv import load_dotenv
from fastapi import APIRouter, Depends, HTTPException, Query, status
from starlette.concurrency import run_in_threadpool

from app.providers.common import ProviderError
from app.providers.geocoding import AmapGeocodingProvider

from .component_dependencies import PROJECT_ROOT

router = APIRouter(prefix="/api/v1/locations", tags=["locations"])


def get_geocoding_provider() -> AmapGeocodingProvider:
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    key = os.getenv("PV_AMAP_WEB_SERVICE_KEY", "").strip()
    if not key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Address lookup is not configured",
        )
    use_system_proxy = os.getenv("PV_AMAP_USE_SYSTEM_PROXY", "false").strip().lower() in {
        "1", "true", "yes", "on"
    }
    return AmapGeocodingProvider(key, trust_env=use_system_proxy)


@router.get("/resolve")
async def resolve_address(
    address: Annotated[str, Query(min_length=4, max_length=200)],
    provider: Annotated[AmapGeocodingProvider, Depends(get_geocoding_provider)],
) -> dict[str, object]:
    """Return candidates only; no coordinate is adopted until the user chooses it."""
    try:
        candidates = await run_in_threadpool(provider.lookup, address)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    except ProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Address lookup failed",
        ) from exc
    return {
        "candidates": [asdict(candidate) for candidate in candidates],
        "source": "Amap Web Service geocoding",
        "coordinate_system": "WGS84 approximate",
        "note": "请核对候选地址和定位级别。坐标为高德坐标近似反算值，仅供天气查询，不是测绘结果。",
    }
