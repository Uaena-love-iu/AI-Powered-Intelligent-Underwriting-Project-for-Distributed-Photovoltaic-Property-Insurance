from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .common import ContractModel
from .enums import InstallationType


class MapImageryReview(ContractModel):
    """人工查看地图服务影像后记录的外部参考信息。"""

    review_id: str = Field(min_length=1)
    provider: Literal["baidu_maps", "amap_maps"] = "baidu_maps"
    address_source: Literal[
        "project_info",
        "material_ocr",
        "human_corrected",
    ]
    triggering_material_ids: list[str] = Field(min_length=1)
    query_address: str = Field(min_length=1, max_length=128)
    matched_address: str = Field(min_length=1)
    geocoding_confidence: int | None = Field(default=None, ge=0, le=100)
    address_comprehension: int | None = Field(default=None, ge=0, le=100)
    precise_match: bool | None = None
    match_level: str | None = None
    longitude_bd09: float | None = Field(default=None, ge=-180.0, le=180.0)
    latitude_bd09: float | None = Field(default=None, ge=-90.0, le=90.0)
    longitude_gcj02: float | None = Field(default=None, ge=-180.0, le=180.0)
    latitude_gcj02: float | None = Field(default=None, ge=-90.0, le=90.0)
    longitude_wgs84: float | None = Field(default=None, ge=-180.0, le=180.0)
    latitude_wgs84: float | None = Field(default=None, ge=-90.0, le=90.0)
    provider_map_url: str = Field(min_length=1)
    queried_at: datetime
    reviewed_at: datetime
    reviewer_name: str = Field(min_length=1, max_length=100)
    site_match_confirmed: bool
    photovoltaic_visibility: Literal[
        "visible",
        "not_visible",
        "unclear",
    ]
    observed_installation_type: InstallationType = InstallationType.UNKNOWN
    imagery_capture_date: str | None = Field(
        default=None,
        pattern=r"^\d{4}(-\d{2})?$",
    )
    observations: str = Field(min_length=1, max_length=2000)
    sentinel_context: "SentinelContextRecord | None" = None
    decision_effect: Literal["manual_context_only"] = "manual_context_only"

    @model_validator(mode="after")
    def validate_map_coordinates(self) -> "MapImageryReview":
        if (self.longitude_bd09 is None) != (self.latitude_bd09 is None):
            raise ValueError("BD-09 map coordinates must be provided together")
        if (self.longitude_gcj02 is None) != (self.latitude_gcj02 is None):
            raise ValueError("GCJ-02 map coordinates must be provided together")
        if (self.longitude_wgs84 is None) != (self.latitude_wgs84 is None):
            raise ValueError("WGS84 weather coordinates must be provided together")
        if self.provider == "amap_maps" and self.longitude_gcj02 is None:
            raise ValueError("Amap reviews require GCJ-02 map coordinates")
        if self.provider == "baidu_maps" and self.longitude_bd09 is None:
            raise ValueError("Baidu reviews require BD-09 map coordinates")
        return self


class SentinelEnvironmentObservation(ContractModel):
    """Non-binding visual category suggested from a Sentinel-2 context image."""

    category: Literal[
        "cultivated_fields",
        "tree_cover",
        "water",
        "built_up",
        "bare_or_mountain",
        "other",
        "uncertain",
    ]
    presence: Literal["present", "possible", "not_observed"]

    @field_validator("presence", mode="before")
    @classmethod
    def normalize_uncertain_presence(cls, value: object) -> object:
        """Keep model uncertainty as a possible observation, never a confirmed one."""
        return "possible" if value == "uncertain" else value

    confidence: float = Field(ge=0, le=1)
    evidence: str = Field(min_length=1, max_length=500)


class SentinelEnvironmentAnalysis(ContractModel):
    """Structured model output, retained only as a human-review suggestion."""

    summary: str = Field(min_length=1, max_length=1200)
    observations: list[SentinelEnvironmentObservation] = Field(max_length=12)


class SentinelContextRecord(ContractModel):
    provider: Literal["copernicus_sentinel_hub"] = "copernicus_sentinel_hub"
    collection: Literal["sentinel-2-l2a"] = "sentinel-2-l2a"
    scene_id: str = Field(min_length=1, max_length=300)
    acquired_at: datetime
    tile_cloud_cover_percent: float | None = Field(default=None, ge=0, le=100)
    tile_cloud_cover_note: str
    masked_pixel_fraction: float = Field(ge=0, le=1)
    center_longitude: float = Field(ge=-180, le=180)
    center_latitude: float = Field(ge=-90, le=90)
    bbox_wgs84: tuple[float, float, float, float]
    radius_m: int = Field(ge=250, le=2000)
    pixel_size_m: float = Field(gt=0, le=20)
    image_width: int = Field(ge=1, le=400)
    image_height: int = Field(ge=1, le=400)
    attribution: str = Field(min_length=1)
    quality_warning: str | None = None
    analysis_warning: str | None = None
    analysis: SentinelEnvironmentAnalysis | None = None


MapImageryReview.model_rebuild()
