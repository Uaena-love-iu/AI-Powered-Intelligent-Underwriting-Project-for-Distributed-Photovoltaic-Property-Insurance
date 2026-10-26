"""Present saved location evidence without overstating geocoder precision."""

from math import asin, cos, radians, sin, sqrt

from .assessment import LocationAssessment, LocationEvidence


def distinct_location_evidence(location: LocationAssessment) -> list[LocationEvidence]:
    """Keep one source file per distinct coordinate or address in a report."""
    distinct = []
    seen = set()
    for item in location.evidence:
        if item.longitude is not None and item.latitude is not None:
            key = ("point", round(item.longitude, 6), round(item.latitude, 6),
                   item.coordinate_system or "未标明")
        elif item.address:
            key = ("address", "".join(item.address.casefold().split()))
        else:
            key = ("material", item.material_id or item.source)
        if key not in seen:
            seen.add(key)
            distinct.append(item)
    return distinct


def coarse_candidate_comparison(location: LocationAssessment) -> str | None:
    """Describe a map candidate's separation as a clue, never a site match."""
    evidence = distinct_location_evidence(location)
    addresses = [item for item in evidence if item.address and item.geocode_level in
                 {"乡镇", "街道", "区县"} and len(item.geocode_candidates) == 1]
    photos = [item for item in evidence if item.source == "现场照片水印"
              and item.longitude is not None and item.latitude is not None]
    if len(addresses) != 1 or len(photos) != 1:
        return None
    address, photo = addresses[0], photos[0]
    candidate = address.geocode_candidates[0]
    left_lat, right_lat = radians(candidate.latitude), radians(photo.latitude)
    dlat = right_lat - left_lat
    dlon = radians(photo.longitude - candidate.longitude)
    distance_m = 2 * 6371000 * asin(sqrt(
        sin(dlat / 2) ** 2 + cos(left_lat) * cos(right_lat) * sin(dlon / 2) ** 2
    ))
    if location.status == "distance_screened":
        return (
            f"备案地址“{address.address}”的地图候选点与现场照片水印坐标按原始数值估算相距约 {distance_m:.0f} 米；"
            "按本轮 500 米位置距离初筛口径，此项不触发人工复核。"
            "该距离不是电站实际位置的精确距离，气象查询坐标仍为候选参考。"
        )
    coordinate_note = (
        "；照片坐标系未标明，原始坐标差也不能直接套用 500 米或 2 公里阈值"
        if photo.coordinate_system in {None, "未标明"} else ""
    )
    return (
        f"备案地址“{address.address}”只定位到{address.geocode_level}级地图候选点，"
        f"该候选点与现场照片水印坐标按原始数值估算相距约 {distance_m:.0f} 米。"
        "这是区域线索，不是备案电站与现场之间的准确距离；候选点并非电站实际位置"
        f"{coordinate_note}。仍需核对项目级准确地址或坐标，以及照片是否属于该电站。"
    )
