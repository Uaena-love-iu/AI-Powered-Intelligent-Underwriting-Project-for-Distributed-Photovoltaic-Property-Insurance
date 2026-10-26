from app.contracts import UnderwritingCase  # noqa: F401 - initialize contract imports first
from app.location.assessment import LocationAssessment, LocationCandidate, LocationEvidence
from app.location.reporting import coarse_candidate_comparison, distinct_location_evidence


def test_repeated_photo_coordinate_is_one_source_and_coarse_distance_is_not_site_match():
    photo = LocationEvidence(
        source="现场照片水印", material_id="photo-one", longitude=105.91146,
        latitude=36.97829, coordinate_system="未标明",
    )
    location = LocationAssessment(status="uncertain", evidence=[
        photo,
        photo.model_copy(update={"material_id": "photo-two"}),
        LocationEvidence(
            source="备案/并网材料", material_id="filing", address="吴忠市同心县豫海镇",
            geocode_level="乡镇", geocode_candidates=[LocationCandidate(
                address="宁夏回族自治区吴忠市同心县豫海镇", level="乡镇",
                longitude=105.912878, latitude=36.980018,
            )],
        ),
    ])
    assert [item.material_id for item in distinct_location_evidence(location)] == [
        "photo-one", "filing",
    ]
    summary = coarse_candidate_comparison(location)
    assert "约 230 米" in summary
    assert "不是备案电站与现场之间的准确距离" in summary
    assert "不能直接套用 500 米或 2 公里阈值" in summary
    screened = location.model_copy(update={"status": "distance_screened"})
    screened_summary = coarse_candidate_comparison(screened)
    assert "约 230 米" in screened_summary
    assert "不触发人工复核" in screened_summary
