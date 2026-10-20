"""Tests of the geodesy functions and of the multi-provider de-duplication."""

from __future__ import annotations

from app.models.schemas import HazardAlert, HazardCategory, SeverityLevel
from app.services.geodesy import (
    bearing_to_cardinal,
    deduplicate_hazards,
    estimate_tsunami_eta_minutes,
    haversine_km,
    initial_bearing_deg,
)


def test_haversine_and_bearing_accuracy() -> None:
    """Great-circle distance and cardinal bearing are accurate."""
    assert haversine_km(48.8566, 2.3522, 48.8566, 2.3522) == 0.0
    assert initial_bearing_deg(48.8566, 2.3522, 48.8566, 2.3522) == 0.0

    # Paris -> Tokyo is about 9713 km, initial bearing north-east
    dist_paris_tokyo = haversine_km(48.8566, 2.3522, 35.6762, 139.6503)
    assert 9700.0 <= dist_paris_tokyo <= 9730.0

    bearing_pt = initial_bearing_deg(48.8566, 2.3522, 35.6762, 139.6503)
    assert 30.0 <= bearing_pt <= 40.0
    assert bearing_to_cardinal(bearing_pt) == "NE"

    assert bearing_to_cardinal(initial_bearing_deg(0.0, 0.0, 10.0, 0.0)) == "N"
    assert bearing_to_cardinal(initial_bearing_deg(0.0, 0.0, 0.0, 10.0)) == "E"
    assert bearing_to_cardinal(initial_bearing_deg(10.0, 0.0, 0.0, 0.0)) == "S"
    assert bearing_to_cardinal(initial_bearing_deg(0.0, 10.0, 0.0, 0.0)) == "W"


def test_tsunami_eta_estimation() -> None:
    """The long-wave law gives about 713 km/h in 4000 m of water."""
    assert estimate_tsunami_eta_minutes(0.0) == 0
    assert 58 <= estimate_tsunami_eta_minutes(713.0, avg_depth_m=4000.0) <= 62
    assert 29 <= estimate_tsunami_eta_minutes(356.5, avg_depth_m=4000.0) <= 31


def _quake(
    hazard_id: str,
    severity: SeverityLevel,
    source: str,
    value: float,
    timestamp: int,
    lat: float,
    lon: float,
    tsunami: bool,
    depth: float | None,
) -> HazardAlert:
    return HazardAlert(
        id=hazard_id,
        category=HazardCategory.EARTHQUAKE,
        severity=severity,
        title=f"M {value}",
        source=source,
        sources=[source],
        timestamp_utc=timestamp,
        lat=lat,
        lon=lon,
        value_primary=value,
        value_unit="Mw",
        depth_km=depth,
        tsunami_warning=tsunami,
    )


def test_deduplicate_hazards_tri_providers() -> None:
    """One earthquake seen by USGS, EMSC and GDACS becomes one enriched alert."""
    base_ts = 1792334500
    eq_usgs = _quake("usgs_1", SeverityLevel.ORANGE, "USGS", 7.3, base_ts, 38.10, 142.85, True, 32.0)
    eq_emsc = _quake("emsc_1", SeverityLevel.ORANGE, "EMSC", 7.4, base_ts + 18, 38.15, 142.92, False, 30.0)
    eq_gdacs = _quake("gdacs_1", SeverityLevel.RED, "GDACS", 7.2, base_ts + 45, 38.08, 142.80, True, None)
    eq_chile = _quake("usgs_2", SeverityLevel.YELLOW, "USGS", 5.4, base_ts - 600, -23.65, -70.40, False, 45.0)

    fused = deduplicate_hazards([eq_emsc, eq_usgs, eq_gdacs, eq_chile])
    assert len(fused) == 2

    japan_alert = fused[0]
    assert japan_alert.severity == SeverityLevel.RED
    assert japan_alert.value_primary == 7.4  # best magnitude kept
    assert japan_alert.tsunami_warning is True
    assert set(japan_alert.sources) == {"USGS", "EMSC", "GDACS"}


def test_deduplicate_keeps_unknown_value_unknown() -> None:
    """Merging alerts that report no value does not invent one, and they sort after valued alerts."""
    base_ts = 1792334500
    no_value_a = HazardAlert(
        id="noaa_a",
        category=HazardCategory.TORNADO,
        severity=SeverityLevel.RED,
        title="Tornado Warning",
        source="NOAA_NWS",
        sources=["NOAA_NWS"],
        timestamp_utc=base_ts,
        lat=35.5,
        lon=-97.5,
        value_primary=None,
        value_unit="km/h",
    )
    no_value_b = no_value_a.model_copy(update={"id": "noaa_b", "timestamp_utc": base_ts + 600})
    valued = _quake("usgs_3", SeverityLevel.RED, "USGS", 7.1, base_ts, -10.0, 100.0, False, 20.0)

    fused = deduplicate_hazards([no_value_a, no_value_b, valued])
    assert len(fused) == 2
    assert fused[0].id == "usgs_3"
    assert fused[1].value_primary is None
