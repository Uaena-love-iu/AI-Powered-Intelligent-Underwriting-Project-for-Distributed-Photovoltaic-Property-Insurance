import os

import pytest
from app.settings import (
    MODEL_ENV_BY_STAGE,
    ProviderConfigurationError,
    VlmSettings,
)

ENVIRONMENT_NAMES = (
    "PV_VLM_BASE_URL",
    "PV_VLM_API_KEY",
    "PV_VLM_MODEL",
    "PV_VLM_TIMEOUT_SECONDS",
    "PV_VLM_USE_SYSTEM_PROXY",
    *MODEL_ENV_BY_STAGE.values(),
)


def test_stage_models_override_shared_model_without_changing_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PV_VLM_BASE_URL", "https://api.example.com/v1")
    monkeypatch.setenv("PV_VLM_API_KEY", "secret-test-key")
    monkeypatch.setenv("PV_VLM_MODEL", "fallback-model")
    monkeypatch.setenv("PV_VLM_CLASSIFICATION_MODEL", "flash-model")
    monkeypatch.setenv("PV_VLM_INSTALLATION_MODEL", "max-model")
    settings = VlmSettings.from_environment(env_file="missing.env")

    assert settings.for_stage("classification").model == "flash-model"
    assert settings.for_stage("installation").model == "max-model"
    assert settings.for_stage("geometry").model == "fallback-model"
    assert settings.for_stage("installation").api_key == settings.api_key
    assert settings.model == "fallback-model"
    with pytest.raises(ValueError, match="Unknown VLM stage"):
        settings.for_stage("unknown")


def test_api_dependencies_use_stage_models(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api.ocr_routes import get_ocr_provider
    from app.api.routes import get_vision_provider
    from app.contracts import Material, MaterialCategory
    from app.providers import MaterialInput

    monkeypatch.setenv("PV_VLM_BASE_URL", "https://api.example.com/v1")
    monkeypatch.setenv("PV_VLM_API_KEY", "secret-test-key")
    monkeypatch.setenv("PV_VLM_MODEL", "fallback-model")
    for stage, model in {
        "FILING": "filing-max", "OCR": "ordinary-flash",
        "INSTALLATION": "installation-max", "VISION": "single-max",
        "GEOMETRY": "geometry-flash",
    }.items():
        monkeypatch.setenv(f"PV_VLM_{stage}_MODEL", model)

    vision = get_vision_provider()
    ocr = get_ocr_provider()
    filing = MaterialInput(material=Material(
        material_id="filing", category=MaterialCategory.FILING_CERTIFICATE,
        file_name="filing.png", media_type="image/png",
        quality_status="usable", quality_issues=[], parse_status="success",
    ), content=b"photo")
    nameplate = MaterialInput(material=Material(
        material_id="nameplate", category=MaterialCategory.COMPONENT_NAMEPLATE,
        file_name="nameplate.png", media_type="image/png",
        quality_status="usable", quality_issues=[], parse_status="success",
    ), content=b"photo")

    assert vision.model_name == "single-max"
    assert vision.installation_model_name == "installation-max"
    assert vision.geometry_model_name == "geometry-flash"
    assert ocr.model_name_for_materials([filing]) == "filing-max"
    assert ocr.model_name_for_materials([nameplate]) == "ordinary-flash"


@pytest.fixture(autouse=True)
def clean_provider_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    for name in ENVIRONMENT_NAMES:
        monkeypatch.delenv(name, raising=False)
    # A user's saved model choices take precedence over env defaults. Keep
    # environment-only tests isolated from that persistent runtime file.
    monkeypatch.setenv("PV_LLM_RUNTIME_CONFIG_PATH", str(tmp_path / "llm-runtime.json"))

    yield

    for name in ENVIRONMENT_NAMES:
        os.environ.pop(name, None)


def test_reads_settings_from_process_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "PV_VLM_BASE_URL",
        "https://api.example.com/v1/",
    )
    monkeypatch.setenv(
        "PV_VLM_API_KEY",
        "secret-test-key",
    )
    monkeypatch.setenv(
        "PV_VLM_MODEL",
        "example-vision-model",
    )
    monkeypatch.setenv(
        "PV_VLM_TIMEOUT_SECONDS",
        "45.5",
    )

    settings = VlmSettings.from_environment(
        env_file="missing.env"
    )

    assert settings.base_url == (
        "https://api.example.com/v1"
    )
    assert settings.api_key == "secret-test-key"
    assert settings.model == "example-vision-model"
    assert settings.timeout_seconds == 45.5
    assert "secret-test-key" not in repr(settings)


def test_default_vlm_timeout_allows_slow_multimodal_responses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "PV_VLM_BASE_URL",
        "https://api.example.com/v1",
    )
    monkeypatch.setenv("PV_VLM_API_KEY", "secret")
    monkeypatch.setenv("PV_VLM_MODEL", "example-model")

    settings = VlmSettings.from_environment(
        env_file="missing.env"
    )

    assert settings.timeout_seconds == 120.0


def test_reads_settings_from_dotenv_file(
    tmp_path,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        """PV_VLM_BASE_URL=https://api.example.com/v1
PV_VLM_API_KEY=dotenv-secret
PV_VLM_MODEL=dotenv-vision-model
PV_VLM_TIMEOUT_SECONDS=30
""",
        encoding="utf-8",
    )

    settings = VlmSettings.from_environment(
        env_file=env_file
    )

    assert settings.base_url == (
        "https://api.example.com/v1"
    )
    assert settings.api_key == "dotenv-secret"
    assert settings.model == "dotenv-vision-model"
    assert settings.timeout_seconds == 30.0


def test_process_environment_takes_priority_over_dotenv(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        """PV_VLM_BASE_URL=https://file.example.com/v1
PV_VLM_API_KEY=file-secret
PV_VLM_MODEL=file-model
""",
        encoding="utf-8",
    )

    monkeypatch.setenv(
        "PV_VLM_BASE_URL",
        "https://process.example.com/v1",
    )
    monkeypatch.setenv(
        "PV_VLM_API_KEY",
        "process-secret",
    )
    monkeypatch.setenv(
        "PV_VLM_MODEL",
        "process-model",
    )

    settings = VlmSettings.from_environment(
        env_file=env_file
    )

    assert settings.base_url == (
        "https://process.example.com/v1"
    )
    assert settings.api_key == "process-secret"
    assert settings.model == "process-model"


def test_rejects_missing_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "PV_VLM_BASE_URL",
        "https://api.example.com/v1",
    )
    monkeypatch.setenv(
        "PV_VLM_MODEL",
        "example-model",
    )

    with pytest.raises(
        ProviderConfigurationError,
        match="PV_VLM_API_KEY is not configured",
    ):
        VlmSettings.from_environment(
            env_file="missing.env"
        )


@pytest.mark.parametrize(
    "value",
    ["invalid", "0", "-1"],
)
def test_rejects_invalid_timeout(
    value: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "PV_VLM_BASE_URL",
        "https://api.example.com/v1",
    )
    monkeypatch.setenv(
        "PV_VLM_API_KEY",
        "secret",
    )
    monkeypatch.setenv(
        "PV_VLM_MODEL",
        "example-model",
    )
    monkeypatch.setenv(
        "PV_VLM_TIMEOUT_SECONDS",
        value,
    )

    with pytest.raises(ProviderConfigurationError):
        VlmSettings.from_environment(
            env_file="missing.env"
        )


def test_rejects_invalid_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "PV_VLM_BASE_URL",
        "not-a-url",
    )
    monkeypatch.setenv(
        "PV_VLM_API_KEY",
        "secret",
    )
    monkeypatch.setenv(
        "PV_VLM_MODEL",
        "example-model",
    )

    with pytest.raises(
        ProviderConfigurationError,
        match=(
            r"PV_VLM_BASE_URL must be "
            r"a valid HTTP\(S\) URL"
        ),
    ):
        VlmSettings.from_environment(
            env_file="missing.env"
        )


def test_vlm_system_proxy_setting_defaults_to_direct_and_can_be_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PV_VLM_BASE_URL", "https://api.example.com/v1")
    monkeypatch.setenv("PV_VLM_API_KEY", "secret")
    monkeypatch.setenv("PV_VLM_MODEL", "example-model")

    settings = VlmSettings.from_environment(env_file="missing.env")
    assert settings.use_system_proxy is False

    monkeypatch.setenv("PV_VLM_USE_SYSTEM_PROXY", "true")
    settings = VlmSettings.from_environment(env_file="missing.env")
    assert settings.use_system_proxy is True
