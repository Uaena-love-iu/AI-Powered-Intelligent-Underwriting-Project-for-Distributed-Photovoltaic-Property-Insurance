import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Self
from urllib.parse import urlparse

from dotenv import load_dotenv


class ProviderConfigurationError(ValueError):
    """外部模型 Provider 配置不完整或不合法。"""


MODEL_ENV_BY_STAGE = {
    "classification": "PV_VLM_CLASSIFICATION_MODEL",
    "filing": "PV_VLM_FILING_MODEL",
    "ocr": "PV_VLM_OCR_MODEL",
    "installation": "PV_VLM_INSTALLATION_MODEL",
    "vision": "PV_VLM_VISION_MODEL",
    "geometry": "PV_VLM_GEOMETRY_MODEL",
    "summary": "PV_VLM_SUMMARY_MODEL",
}


def _required_environment_value(name: str) -> str:
    value = os.getenv(name, "").strip()

    if not value:
        raise ProviderConfigurationError(
            f"{name} is not configured"
        )

    return value


def _positive_float_environment_value(
    name: str,
    default: str,
) -> float:
    raw_value = os.getenv(name, default).strip()

    try:
        value = float(raw_value)
    except ValueError as exc:
        raise ProviderConfigurationError(
            f"{name} must be a number"
        ) from exc

    if value <= 0:
        raise ProviderConfigurationError(
            f"{name} must be greater than zero"
        )

    return value


def _validate_http_url(name: str, value: str) -> str:
    parsed = urlparse(value)

    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
    ):
        raise ProviderConfigurationError(
            f"{name} must be a valid HTTP(S) URL"
        )

    return value.rstrip("/")


@dataclass(frozen=True, slots=True)
class VlmSettings:
    """多模态视觉模型 Provider 的运行配置。"""

    base_url: str
    api_key: str = field(repr=False)
    model: str
    timeout_seconds: float = 120.0
    use_system_proxy: bool = False
    trust_env: bool = False
    stage_models: dict[str, str] = field(default_factory=dict, repr=False)

    def for_stage(self, stage: str) -> Self:
        """Select a model for one task, falling back to the shared model."""
        if stage not in MODEL_ENV_BY_STAGE:
            raise ValueError(f"Unknown VLM stage: {stage}")
        return replace(self, model=self.stage_models.get(stage) or self.model)

    @classmethod
    def from_environment(
        cls,
        env_file: str | Path = ".env",
    ) -> Self:
        """从进程环境和本地 .env 文件读取配置。"""

        load_dotenv(
            dotenv_path=env_file,
            override=False,
        )

        from app.config.llm_runtime import read_runtime
        runtime_models = read_runtime()["models"]

        base_url = _validate_http_url(
            "PV_VLM_BASE_URL",
            _required_environment_value(
                "PV_VLM_BASE_URL"
            ),
        )

        return cls(
            base_url=base_url,
            api_key=_required_environment_value(
                "PV_VLM_API_KEY"
            ),
            model=_required_environment_value(
                "PV_VLM_MODEL"
            ),
            stage_models={
                stage: model
                for stage, name in MODEL_ENV_BY_STAGE.items()
                if (model := runtime_models.get(stage) or os.getenv(name, "").strip())
            },
            trust_env=os.environ.get('PV_VLM_TRUST_ENV', 'false').lower() in {'true', '1', 'yes'},
            timeout_seconds=(
                _positive_float_environment_value(
                    "PV_VLM_TIMEOUT_SECONDS",
                    "120",
                )
            ),
            use_system_proxy=(
                os.getenv("PV_VLM_USE_SYSTEM_PROXY", "false").strip().lower()
                in {"1", "true", "yes", "on"}
            ),
        )


@dataclass(frozen=True, slots=True)
class BaiduMapSettings:
    """服务端调用百度地图地理编码 API 所需的配置。"""

    api_key: str = field(repr=False)
    timeout_seconds: float = 10.0

    @classmethod
    def from_environment(
        cls,
        env_file: str | Path = ".env",
    ) -> Self:
        load_dotenv(
            dotenv_path=env_file,
            override=False,
        )
        return cls(
            api_key=_required_environment_value("PV_MAP_BAIDU_AK"),
            timeout_seconds=_positive_float_environment_value(
                "PV_MAP_TIMEOUT_SECONDS",
                "10",
            ),
        )


@dataclass(frozen=True, slots=True)
class AmapJsApiSettings:
    """Browser-side Amap JS API credentials for the optional satellite layer."""

    api_key: str = field(repr=False)
    security_js_code: str = field(repr=False)

    @classmethod
    def from_environment(
        cls,
        env_file: str | Path = ".env",
    ) -> Self:
        load_dotenv(
            dotenv_path=env_file,
            override=False,
        )
        return cls(
            api_key=_required_environment_value("PV_AMAP_JS_API_KEY"),
            security_js_code=_required_environment_value(
                "PV_AMAP_JS_SECURITY_CODE"
            ),
        )


@dataclass(frozen=True, slots=True)
class SentinelHubSettings:
    """Server-side credentials and bounded query settings for Copernicus Sentinel Hub."""

    client_id: str = field(repr=False)
    client_secret: str = field(repr=False)
    timeout_seconds: float = 45.0
    lookback_days: int = 180
    max_cloud_cover_percent: float = 40.0
    radius_m: int = 1000
    use_system_proxy: bool = False

    @classmethod
    def from_environment(
        cls,
        env_file: str | Path = ".env",
    ) -> Self:
        load_dotenv(dotenv_path=env_file, override=False)
        try:
            lookback_days = int(os.getenv("PV_SENTINEL_LOOKBACK_DAYS", "180"))
            radius_m = int(os.getenv("PV_SENTINEL_RADIUS_M", "1000"))
            cloud_cover = float(os.getenv("PV_SENTINEL_MAX_CLOUD_COVER_PERCENT", "40"))
        except ValueError as exc:
            raise ProviderConfigurationError(
                "Sentinel lookback, radius, and cloud cover settings must be numbers"
            ) from exc
        timeout_seconds = _positive_float_environment_value(
            "PV_SENTINEL_TIMEOUT_SECONDS",
            "45",
        )
        if not 1 <= lookback_days <= 730:
            raise ProviderConfigurationError(
                "PV_SENTINEL_LOOKBACK_DAYS must be between 1 and 730"
            )
        if not 250 <= radius_m <= 2000:
            raise ProviderConfigurationError(
                "PV_SENTINEL_RADIUS_M must be between 250 and 2000"
            )
        if not 0 <= cloud_cover <= 100:
            raise ProviderConfigurationError(
                "PV_SENTINEL_MAX_CLOUD_COVER_PERCENT must be between 0 and 100"
            )
        return cls(
            client_id=_required_environment_value("PV_SENTINEL_HUB_CLIENT_ID"),
            client_secret=_required_environment_value(
                "PV_SENTINEL_HUB_CLIENT_SECRET"
            ),
            timeout_seconds=timeout_seconds,
            lookback_days=lookback_days,
            max_cloud_cover_percent=cloud_cover,
            radius_m=radius_m,
            use_system_proxy=(
                os.getenv("PV_SENTINEL_USE_SYSTEM_PROXY", "false").strip().lower()
                in {"1", "true", "yes", "on"}
            ),
        )
