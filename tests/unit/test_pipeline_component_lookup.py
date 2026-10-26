from datetime import UTC, datetime

from app.contracts import (
    ComponentProfile,
    ProcessingStatus,
    ProcessingStep,
    ProjectInfo,
)
from app.pipeline import UnderwritingPipeline
from app.providers import (
    MockOcrProvider,
    MockVisionProvider,
    ProviderError,
)
from app.providers.component import ComponentProvider
from component_test_support import CatalogComponentProvider


def project_info(
    component_model: str = (
        "PV-MODULE-580W"
    ),
) -> ProjectInfo:
    return ProjectInfo(
        project_name="组件查询测试项目",
        insured_name="示例企业",
        project_type="rooftop",
        installation_type=(
            "color_steel_roof"
        ),
        site_address="广东省示例市",
        longitude=113.25,
        latitude=23.12,
        proposed_start_date="2026-10-01",
        component_model=component_model,
    )


def component_profile() -> ComponentProfile:
    return ComponentProfile(
        component_model="PV-MODULE-580W",
        manufacturer="示例组件厂商",
        rated_power_w=580,
        hail_resistance_mm=25,
        wind_load_pa=2400,
        snow_load_pa=5400,
        source_url=(
            "https://example.com/component.pdf"
        ),
        source_name="本地可信组件目录",
        retrieved_at=datetime(
            2026,
            9,
            26,
            9,
            0,
            tzinfo=UTC,
        ),
        match_confidence=1.0,
    )


class FailingComponentProvider(
    ComponentProvider
):
    @property
    def name(self) -> str:
        return "failing-component-provider"

    def lookup(
        self,
        component_model: str,
    ) -> ComponentProfile | None:
        del component_model

        raise ProviderError(
            "sensitive upstream failure"
        )


def test_pipeline_adds_component_profile() -> None:
    pipeline = UnderwritingPipeline(
        ocr_provider=MockOcrProvider(),
        vision_provider=MockVisionProvider(),
        component_provider=(
            CatalogComponentProvider(
                profiles=[
                    component_profile()
                ]
            )
        ),
    )

    profile, trace = pipeline._run_component_lookup(project_info())

    assert profile is not None
    assert (
        profile.component_model
        == "PV-MODULE-580W"
    )

    assert (
        trace.status
        is ProcessingStatus.SUCCESS
    )
    assert trace.provider == (
        "local-component-catalog"
    )
    assert trace.step is ProcessingStep.COMPONENT_LOOKUP


def test_unknown_component_is_not_provider_failure() -> None:
    pipeline = UnderwritingPipeline(
        ocr_provider=MockOcrProvider(),
        vision_provider=MockVisionProvider(),
        component_provider=(
            CatalogComponentProvider(
                profiles=[
                    component_profile()
                ]
            )
        ),
    )

    profile, trace = pipeline._run_component_lookup(project_info("UNKNOWN-MODEL"))

    assert profile is None

    assert (
        trace.status
        is ProcessingStatus.SUCCESS
    )
    assert trace.error_code is None


def test_component_provider_failure_is_sanitized() -> None:
    pipeline = UnderwritingPipeline(
        ocr_provider=MockOcrProvider(),
        vision_provider=MockVisionProvider(),
        component_provider=(
            FailingComponentProvider()
        ),
    )

    profile, trace = pipeline._run_component_lookup(project_info())
    assert profile is None

    assert (
        trace.status
        is ProcessingStatus.FAILED
    )
    assert trace.error_code == (
        "COMPONENT_PROVIDER_FAILURE"
    )
    assert trace.error_message == (
        "component provider lookup failed"
    )
    assert (
        "sensitive upstream failure"
        not in trace.error_message
    )
