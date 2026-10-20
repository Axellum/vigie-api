"""Size budget of the compact ``GET /v1/snapshot``: 16 KiB, with the credit of every item.

The snapshot used to be held to 12 KiB; the credit every hazard and every zone now carries (``attribution``)
made a snapshot of 15 hazards and 5 zones about 2.5 KB larger, so the documented budget is 16 KiB. The
display reads only the embedded frame, whose own budget (8 KiB) is unchanged and tested in
``test_embedded_frame_contract.py``.

The snapshot below is built from realistic samples of the real sources, in the format each one sends
(USGS and EMSC GeoJSON, GDACS search, NOAA NWS alerts, USGS volcano list) and run through the real parsers,
so the titles, identifiers and credits have the length they have in production. The five watched zones
have weather and air quality.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.attribution import CREDITS
from app.collectors.disasters import GDACSCollector, NOAANWSCollector
from app.collectors.flood_air import USGSVolcanoCollector
from app.collectors.seismic import EMSCCollector, USGSCollector
from app.main import aggregator
from app.models.schemas import DeviceType, HazardAlert
from tests.conftest import World
from tests.helpers import SNAPSHOT_BUDGET_BYTES


def usgs_alerts() -> list[HazardAlert]:
    places = [
        ("us7000abcd", 6.1, "120 km SSW of Kimbe, Papua New Guinea", 1792334500000, 150.2, -6.9, 55.2),
        ("us7000abce", 5.4, "34 km NNE of Sendai, Japan", 1792330000000, 141.1, 38.5, 45.0),
        ("us7000abcf", 5.0, "12 km WSW of Pisco, Peru", 1792325000000, -76.3, -13.8, 31.4),
        ("us7000abcg", 4.8, "South Sandwich Islands region", 1792320000000, -26.1, -57.4, 120.0),
        ("us7000abch", 4.6, "98 km SE of Hachijo-jima, Japan", 1792315000000, 140.4, 32.9, 387.5),
    ]
    features = [
        {
            "id": event_id,
            "properties": {"mag": mag, "place": place, "time": time_ms, "tsunami": 0, "magType": "mww"},
            "geometry": {"coordinates": [lon, lat, depth]},
        }
        for event_id, mag, place, time_ms, lon, lat, depth in places
    ]
    return USGSCollector.parse_geojson({"features": features})


def emsc_alerts() -> list[HazardAlert]:
    events = [
        ("20261018_0000123", 5.3, "SOUTHERN GREECE", "2026-10-18T09:12:45.0Z", 22.4, 36.1, 10.0),
        ("20261018_0000124", 5.1, "EASTERN TURKEY", "2026-10-18T07:40:10.0Z", 40.2, 38.9, 8.0),
        ("20261018_0000125", 4.9, "NORTHERN ALGERIA", "2026-10-18T05:02:31.0Z", 3.1, 36.6, 12.0),
    ]
    features = [
        {
            "id": unid,
            "properties": {"mag": mag, "flynn_region": region, "time": when, "magtype": "mw", "unid": unid},
            "geometry": {"coordinates": [lon, lat, -depth]},
        }
        for unid, mag, region, when, lon, lat, depth in events
    ]
    return EMSCCollector.parse_geojson({"features": features})


def gdacs_alerts() -> list[HazardAlert]:
    events = [
        ("TC", "1001234", "Red", "Tropical Cyclone MILTON-26", "United States", 250.0, "km/h", -82.5, 27.5),
        ("FL", "1102345", "Orange", "Flood in Bangladesh", "Bangladesh", None, None, 90.4, 23.7),
        ("WF", "1203456", "Orange", "Wildfire in Portugal", "Portugal", 12000.0, "ha", -8.2, 39.9),
    ]
    features = []
    for ev_type, event_id, level, name, country, severity, unit, lon, lat in events:
        props: dict[str, Any] = {
            "eventtype": ev_type,
            "eventid": event_id,
            "alertlevel": level,
            "name": name,
            "country": country,
            "iscurrent": "true",
            "fromdate": "2026-10-15T10:00:00Z",
        }
        if severity is not None:
            props["severitydata"] = {"severity": severity, "severityunit": unit}
        features.append({"properties": props, "geometry": {"coordinates": [lon, lat]}})
    return GDACSCollector.parse_geojson({"features": features})


def nws_alerts() -> list[HazardAlert]:
    alerts = [
        ("urn:oid:2.49.0.1.840.0.8a1b2c3d4e5f60718293a4b5c6d7e8f901234567.001.1", "Tornado Warning", "Extreme",
         "Oklahoma County, OK; Canadian County, OK", -97.5, 35.5),
        ("urn:oid:2.49.0.1.840.0.9b2c3d4e5f60718293a4b5c6d7e8f9012345678a.001.1", "Hurricane Warning", "Extreme",
         "Pinellas, FL; Hillsborough, FL; Manatee, FL", -82.6, 27.9),
    ]
    features = [
        {
            "id": alert_id,
            "properties": {
                "id": alert_id,
                "event": event,
                "severity": severity,
                "areaDesc": area,
                "sent": "2026-10-18T13:00:00+00:00",
            },
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
        }
        for alert_id, event, severity, area, lon, lat in alerts
    ]
    return NOAANWSCollector.parse_alerts({"features": features})


def volcano_alerts() -> list[HazardAlert]:
    payload = [
        {"vName": "Great Sitkin", "vnum": "311120", "lat": 52.0765, "long": -176.1109, "alertLevel": "WATCH",
         "colorCode": "ORANGE", "sentUtc": "2026-10-19 20:01:42"},
        {"vName": "Kilauea", "vnum": "332010", "lat": 19.421, "long": -155.287, "alertLevel": "WATCH",
         "colorCode": "ORANGE", "sentUtc": "2026-10-19 18:30:00"},
    ]
    return USGSVolcanoCollector.parse_volcanoes(payload)


@pytest.fixture()
def realistic_sources(world: World, monkeypatch: pytest.MonkeyPatch) -> dict[str, list[HazardAlert]]:
    """About 15 hazards spread over the five hazard sources, served by the mocked collectors."""
    sources = {
        "usgs": usgs_alerts(),
        "emsc": emsc_alerts(),
        "gdacs": gdacs_alerts(),
        "noaa_nws": nws_alerts(),
        "volcano": volcano_alerts(),
    }
    for name, alerts in sources.items():
        monkeypatch.setattr(getattr(aggregator, name), "fetch", AsyncMock(return_value=alerts))
    return sources


def test_the_samples_are_fifteen_hazards_over_five_sources(realistic_sources: dict[str, list[HazardAlert]]) -> None:
    assert {name: len(alerts) for name, alerts in realistic_sources.items()} == {
        "usgs": 5, "emsc": 3, "gdacs": 3, "noaa_nws": 2, "volcano": 2,
    }
    assert sum(len(alerts) for alerts in realistic_sources.values()) == 15


@pytest.mark.parametrize("device", [d.value for d in DeviceType])
def test_the_compact_snapshot_stays_under_its_budget_with_every_credit(
    world: World, realistic_sources: dict[str, list[HazardAlert]], device: str
) -> None:
    response = world.client.get(f"/v1/snapshot?device_type={device}&compact=true")
    assert response.status_code == 200
    snapshot = response.json()

    assert len(snapshot["zones"]) == 5
    for zone in snapshot["zones"]:
        assert zone["current"]["aqi_eu"] is not None and zone["daily_10d"]  # weather and air quality
        assert zone["attribution"]  # the credit is on every zone...
    assert snapshot["global_status"]["space_weather_attribution"]

    expected_hazards = min(15, aggregator_limit(device))
    assert len(snapshot["hazards"]) == expected_hazards
    assert all(hazard["attribution"] for hazard in snapshot["hazards"])  # ...and on every hazard

    size = len(json.dumps(snapshot, separators=(",", ":")).encode("utf-8"))
    print(f"compact snapshot, {device}: {len(snapshot['hazards'])} hazards, {size} bytes")
    assert size < SNAPSHOT_BUDGET_BYTES, f"{device}: {size} bytes"


def test_every_source_is_credited_in_the_realistic_snapshot(world: World, realistic_sources: dict[str, list[HazardAlert]]) -> None:
    snapshot = world.client.get("/v1/snapshot?device_type=tab5&compact=true").json()
    shown = {hazard["attribution"] for hazard in snapshot["hazards"]}
    for key in ("usgs", "emsc", "gdacs", "noaa_nws", "usgs_volcano"):
        assert CREDITS[key].text in shown, key


def test_the_realistic_point_response_stays_under_the_same_budget(
    world: World, realistic_sources: dict[str, list[HazardAlert]]
) -> None:
    point = world.client.get("/v1/point?lat=47.75&lon=-3.36&marine=true&radius_km=5000&compact=true").json()
    assert len(point["nearby_hazards"]) >= 5  # the hazards within 5,000 km, and every red one
    assert all(hazard["attribution"] for hazard in point["nearby_hazards"])
    size = len(json.dumps(point, separators=(",", ":")).encode("utf-8"))
    print(f"compact point response: {len(point['nearby_hazards'])} hazards, {size} bytes")
    assert size < SNAPSHOT_BUDGET_BYTES


def aggregator_limit(device: str) -> int:
    """How many hazards a profile may carry (``DEVICE_DEFAULT_MAX_HAZARDS`` of the aggregator)."""
    from app.services.aggregator import DEVICE_DEFAULT_MAX_HAZARDS

    return DEVICE_DEFAULT_MAX_HAZARDS[DeviceType(device)]
