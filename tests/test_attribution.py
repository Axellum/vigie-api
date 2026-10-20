"""Credits: every block of data carries the text its source asks for, from one table.

The table is ``app/attribution.py``; ``SOURCES.md`` quotes the same texts (``test_sources_page.py`` fails
when they drift apart). The compact embedded frame has no credit field and must not get one.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.attribution import (
    CREDITS,
    HAZARD_SOURCE_CREDITS,
    SEPARATOR,
    SIMULATION_CREDIT,
    Credit,
    credit_text,
    hazard_credit,
    merge_credits,
)
from app.collectors.disasters import GDACSCollector, NOAANWSCollector
from app.collectors.flood_air import (
    OpenMeteoAirQualityCollector,
    OpenMeteoFloodCollector,
    USGSVolcanoCollector,
)
from app.collectors.marine import OpenMeteoMarineCollector
from app.collectors.seismic import EMSCCollector, USGSCollector
from app.collectors.space_weather import NOAASWPCCollector
from app.collectors.weather import OpenMeteoCollector, rainviewer_enabled_from_env
from app.main import aggregator
from app.models.schemas import HazardAlert, SimulationRequest
from app.routers.dashboard import credits_html, render_dashboard
from app.services.geodesy import deduplicate_hazards
from tests import samples
from tests.conftest import World
from tests.helpers import make_service

OPEN_METEO = CREDITS["open_meteo"].text
GDACS = CREDITS["gdacs"].text


# ============================================================================
# The table
# ============================================================================


def test_every_hazard_source_name_has_a_credit() -> None:
    assert set(HAZARD_SOURCE_CREDITS.values()) <= set(CREDITS)
    assert {"USGS", "EMSC", "GDACS", "NOAA_NWS", "USGS_VOLCANO"} == set(HAZARD_SOURCE_CREDITS)


def test_the_gdacs_credit_says_its_alerts_do_not_replace_official_warnings() -> None:
    assert "Global Disaster Alert and Coordination System, GDACS" in GDACS
    assert "automatic" in GDACS and "do not replace official warnings" in GDACS


def test_the_required_texts_of_the_providers() -> None:
    assert "Weather data by Open-Meteo.com" in OPEN_METEO and "https://open-meteo.com/" in OPEN_METEO
    assert CREDITS["emsc"].text == "Credit: EMSC-CSEM SeismicPortal, https://www.seismicportal.eu"
    assert "MET Norway" in CREDITS["met_norway"].text
    assert "U.S. Geological Survey" in CREDITS["usgs"].text
    assert "DWD" in CREDITS["dwd_icon_wave"].text
    assert "CAMS" in CREDITS["cams"].text
    for key in ("noaa_nws", "noaa_swpc"):
        assert "Not endorsed by NOAA" in CREDITS[key].text  # no implied endorsement


def test_merge_credits_keeps_order_and_drops_repeats() -> None:
    both = merge_credits(OPEN_METEO, f"{OPEN_METEO}{SEPARATOR}{CREDITS['cams'].text}", None)
    assert both == f"{OPEN_METEO}{SEPARATOR}{CREDITS['cams'].text}"
    assert merge_credits(None, "") is None


# ============================================================================
# Hazards
# ============================================================================


def _usgs() -> HazardAlert:
    data = {
        "features": [
            {
                "id": "us1",
                "properties": {"mag": 6.1, "place": "Sendai, Japan", "time": 1792334500000},
                "geometry": {"coordinates": [142.85, 38.10, 25.0]},
            }
        ]
    }
    return USGSCollector.parse_geojson(data)[0]


def _emsc() -> HazardAlert:
    data = {
        "features": [
            {
                "id": "e1",
                "properties": {"mag": 5.3, "flynn_region": "CRETE", "time": "2026-10-18T12:00:00Z"},
                "geometry": {"coordinates": [25.1, 35.2, -15.0]},
            }
        ]
    }
    return EMSCCollector.parse_geojson(data)[0]


def _gdacs() -> HazardAlert:
    data = {
        "features": [
            {
                "properties": {
                    "eventtype": "TC",
                    "eventid": "9988",
                    "alertlevel": "Red",
                    "name": "Cyclone Milton",
                    "fromdate": "2026-10-18T10:00:00Z",
                },
                "geometry": {"coordinates": [-82.5, 27.5]},
            }
        ]
    }
    return GDACSCollector.parse_geojson(data)[0]


def _nws() -> HazardAlert:
    data = {
        "features": [
            {
                "id": "urn:oid:1",
                "properties": {
                    "event": "Tornado Warning",
                    "severity": "Extreme",
                    "areaDesc": "Oklahoma County, OK",
                    "sent": "2026-10-18T13:00:00+00:00",
                },
                "geometry": {"type": "Point", "coordinates": [-97.5, 35.5]},
            }
        ]
    }
    return NOAANWSCollector.parse_alerts(data)[0]


def _volcano() -> HazardAlert:
    payload = [
        {
            "vName": "Great Sitkin",
            "vnum": "311120",
            "lat": 52.0765,
            "long": -176.1109,
            "colorCode": "ORANGE",
            "sentUtc": "2026-10-19 20:01:42",
        }
    ]
    return USGSVolcanoCollector.parse_volcanoes(payload)[0]


@pytest.mark.parametrize(
    ("parse", "key"),
    [(_usgs, "usgs"), (_emsc, "emsc"), (_gdacs, "gdacs"), (_nws, "noaa_nws"), (_volcano, "usgs_volcano")],
)
def test_each_hazard_source_is_credited_with_its_own_text(parse, key: str) -> None:  # type: ignore[no-untyped-def]
    alert = parse()
    assert alert.attribution == CREDITS[key].text
    assert alert.model_dump()["attribution"] == CREDITS[key].text  # it is in the JSON, not only a property


def test_a_merged_alert_is_credited_to_every_source_that_reports_it() -> None:
    usgs = samples.earthquake("usgs_1", source="USGS", value=6.5)
    emsc = samples.earthquake("emsc_1", source="EMSC", value=6.6, timestamp=usgs.timestamp_utc + 20)
    (merged,) = deduplicate_hazards([usgs, emsc])
    assert merged.sources == ["USGS", "EMSC"]
    assert merged.attribution == f"{CREDITS['usgs'].text}{SEPARATOR}{CREDITS['emsc'].text}"


def test_an_unknown_source_gets_no_invented_credit() -> None:
    assert hazard_credit("x_1", ["SOMEONE"]) is None
    assert hazard_credit("x_1", ["USGS", "SOMEONE"]) == CREDITS["usgs"].text


def test_a_simulated_alert_is_credited_to_no_provider(world: World) -> None:
    alert = world.aggregator.trigger_simulation(SimulationRequest()).injected_hazards[0]
    assert alert.id.startswith("sim_")
    assert alert.sources  # it still carries the (labelled) source names of the scenario
    assert alert.attribution == SIMULATION_CREDIT


def test_the_credit_survives_the_history_round_trip(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(aggregator.gdacs, "fetch", AsyncMock(return_value=[_gdacs()]))
    world.client.get("/v1/snapshot")  # collecting records the hazards
    items = world.client.get("/v1/history").json()["items"]
    assert items and items[0]["attribution"] == GDACS


# ============================================================================
# Weather, air quality, flood, marine, space weather
# ============================================================================


def test_parsers_credit_their_block() -> None:
    assert OpenMeteoCollector.parse_response(samples.open_meteo_payload())["attribution"] == OPEN_METEO
    assert samples.parsed_metno()["attribution"] == CREDITS["met_norway"].text
    assert samples.parsed_marine().attribution == credit_text("open_meteo", "dwd_icon_wave")
    flood = OpenMeteoFloodCollector.parse_response(
        {"daily": {"river_discharge": [10.0], "river_discharge_mean": [9.0], "river_discharge_max": [12.0]}}
    )
    assert flood.attribution == credit_text("open_meteo", "glofas")
    air = OpenMeteoAirQualityCollector.parse_response({"current": {"european_aqi": 31, "pm10": 14.0}})
    assert air.attribution == credit_text("open_meteo", "cams")
    kp = NOAASWPCCollector.parse_kp_json([{"time_tag": "2026-10-20T00:00:00", "Kp": 2.33}])
    assert kp.attribution == CREDITS["noaa_swpc"].text


def test_marine_parser_still_raises_when_empty_so_nothing_is_credited_without_data() -> None:
    with pytest.raises(ValueError):
        OpenMeteoMarineCollector.parse_response({"current": {}})


def test_the_snapshot_credits_each_zone_hazard_and_space_weather(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(aggregator.usgs, "fetch", AsyncMock(return_value=[_usgs()]))
    snap = world.client.get("/v1/snapshot").json()
    for zone in snap["zones"]:
        # the weather provider, and CAMS because the air quality index is part of `current`
        assert zone["attribution"] == f"{OPEN_METEO}{SEPARATOR}{CREDITS['cams'].text}"
    assert snap["hazards"][0]["attribution"] == CREDITS["usgs"].text
    assert snap["global_status"]["space_weather_attribution"] == CREDITS["noaa_swpc"].text


def test_a_zone_without_weather_has_nothing_to_credit(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.collectors.base import CollectorUnavailableError

    monkeypatch.setattr(aggregator.open_meteo, "fetch_point", AsyncMock(side_effect=CollectorUnavailableError("down")))
    monkeypatch.setattr(aggregator.air_quality, "fetch_air_quality", AsyncMock(side_effect=CollectorUnavailableError("down")))
    monkeypatch.setattr(aggregator.swpc, "fetch", AsyncMock(side_effect=CollectorUnavailableError("down")))
    snap = world.client.get("/v1/snapshot").json()
    assert all(zone["attribution"] is None for zone in snap["zones"])
    assert snap["global_status"]["space_weather_attribution"] is None


def test_a_fallback_weather_is_credited_to_met_norway(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.collectors.base import CollectorUnavailableError

    monkeypatch.setattr(aggregator.open_meteo, "fetch_point", AsyncMock(side_effect=CollectorUnavailableError("down")))
    monkeypatch.setattr(aggregator.met_norway, "fetch_point", AsyncMock(return_value=samples.parsed_metno()))
    point = world.client.get("/v1/point?lat=48.85&lon=2.35").json()
    assert point["weather_provider"] == "MET Norway (fallback)"
    assert point["weather_attribution"].startswith(CREDITS["met_norway"].text)
    assert OPEN_METEO in point["weather_attribution"]  # the air quality index (Open-Meteo) is part of `current`


def test_the_point_response_credits_each_block(world: World) -> None:
    point = world.client.get("/v1/point?lat=47.75&lon=-3.36&marine=true").json()
    assert point["weather_attribution"] == f"{OPEN_METEO}{SEPARATOR}{CREDITS['cams'].text}"
    assert point["marine"]["attribution"] == credit_text("open_meteo", "dwd_icon_wave")
    assert point["flood_risk"]["attribution"] == credit_text("open_meteo", "glofas")
    assert point["air_quality"]["attribution"] == credit_text("open_meteo", "cams")
    assert point["space_weather"]["attribution"] == CREDITS["noaa_swpc"].text
    # the rain steps are Open-Meteo's, the radar fields RainViewer's
    assert point["rain_nowcast"]["attribution"] == f"{OPEN_METEO}{SEPARATOR}{CREDITS['rainviewer'].text}"


def test_blocks_with_no_data_have_no_credit(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.collectors.base import CollectorUnavailableError

    monkeypatch.setattr(aggregator.open_meteo, "fetch_point", AsyncMock(side_effect=CollectorUnavailableError("down")))
    monkeypatch.setattr(aggregator.rainviewer, "fetch", AsyncMock(side_effect=CollectorUnavailableError("down")))
    point = world.client.get("/v1/point?lat=47.75&lon=-3.36").json()
    assert point["current"] is None and point["weather_attribution"] is None
    assert point["rain_nowcast"]["attribution"] is None  # no rain steps, no radar frame


def test_simulated_blocks_are_credited_to_no_provider(world: World, simulation_on: None) -> None:
    world.client.post("/v1/simulate", json={"scenario": "volcano_eruption_iceland"})
    point = world.client.get("/v1/point?lat=63.98&lon=-22.45").json()
    assert point["air_quality"]["attribution"] == SIMULATION_CREDIT
    world.client.post("/v1/simulate", json={"scenario": "severe_squall_marine"})
    point = world.client.get("/v1/point?lat=47.65&lon=-3.5&marine=true").json()
    assert point["marine"]["attribution"] == SIMULATION_CREDIT


# ============================================================================
# GeoJSON, CAP, WebSocket, frame
# ============================================================================


def test_geojson_features_and_collection_carry_the_credit(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(aggregator.gdacs, "fetch", AsyncMock(return_value=[_gdacs()]))
    monkeypatch.setattr(aggregator.usgs, "fetch", AsyncMock(return_value=[_usgs()]))
    geo = world.client.get("/v1/geojson/hazards").json()
    credits = {f["properties"]["attribution"] for f in geo["features"]}
    assert credits == {GDACS, CREDITS["usgs"].text}
    assert set(geo["metadata"]["attribution"].split(SEPARATOR)) == {GDACS, CREDITS["usgs"].text}


def test_an_empty_geojson_has_no_credit(world: World) -> None:
    assert world.client.get("/v1/geojson/hazards").json()["metadata"]["attribution"] is None


def test_cap_alerts_carry_the_credit_as_a_parameter_and_in_the_description(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(aggregator.gdacs, "fetch", AsyncMock(return_value=[_gdacs()]))
    info = world.client.get("/v1/cap/alerts").json()["alerts"][0]["info"][0]
    assert info["parameters"]["attribution"] == GDACS
    assert info["description"].endswith(f"Attribution: {GDACS}")
    assert "do not replace official warnings" in info["description"]


def test_the_embedded_frame_has_no_credit_field(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    """The frame contract is fixed: credits never enter it (the contract tests pin its columns)."""
    monkeypatch.setattr(aggregator.gdacs, "fetch", AsyncMock(return_value=[_gdacs()]))
    frame = world.client.get("/v1/embedded/frame?lat=47.75&lon=-3.36&marine=true").text
    assert "HAZ|" in frame
    for credit in CREDITS.values():
        assert credit.text not in frame
    assert "attribution" not in frame.lower() and "open-meteo.com" not in frame.lower()


# ============================================================================
# RainViewer can be turned off
# ============================================================================


@pytest.mark.parametrize(("value", "enabled"), [(None, True), ("", True), ("1", True), ("yes", True), ("0", False), ("false", False), ("NO", False), (" off ", False)])
def test_rainviewer_switch(monkeypatch: pytest.MonkeyPatch, value: str | None, enabled: bool) -> None:
    if value is None:
        monkeypatch.delenv("VIGIE_ENABLE_RAINVIEWER", raising=False)
    else:
        monkeypatch.setenv("VIGIE_ENABLE_RAINVIEWER", value)
    assert rainviewer_enabled_from_env() is enabled


@pytest.mark.asyncio
async def test_a_service_without_rainviewer_never_asks_for_the_radar(tmp_path: Path) -> None:
    service = make_service(tmp_path / "off.db", rainviewer_enabled=False)
    service.rainviewer.fetch = AsyncMock(side_effect=AssertionError("RainViewer must not be asked"))  # type: ignore[method-assign]
    assert service.rainviewer not in service.get_all_collectors()
    assert "rainviewer_radar" not in {p.provider_id for p in service.get_providers_health()}
    assert len(service.get_all_collectors()) == 11

    point = await service.build_point_response(lat=48.85, lon=2.35)
    assert point.rain_nowcast.radar_tile_url_template is None and point.rain_nowcast.radar_timestamp_utc is None
    service.rainviewer.fetch.assert_not_called()


def test_rainviewer_stays_on_by_default(world: World) -> None:
    assert world.aggregator.rainviewer_enabled is True
    assert len(world.aggregator.get_all_collectors()) == 12


# ============================================================================
# Dashboard
# ============================================================================


def test_dashboard_ends_with_the_credits_and_the_gdacs_notice(world: World) -> None:
    html = world.client.get("/dashboard").text
    assert "GDACS alerts are automatic and do not replace official warnings" in html
    assert "Not an official warning service" in html
    assert '<a href="https://open-meteo.com/"' in html  # Open-Meteo asks for a link
    for credit in CREDITS.values():
        assert credit.provider in html


def test_dashboard_credit_texts_are_escaped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(CREDITS, "evil", Credit("evil", "<i>P</i>", "<script>alert(1)</script> https://a.example/x?y=1&z=2", "ours"))
    html = credits_html()
    assert "<script>" not in html and "&lt;script&gt;" in html and "&lt;i&gt;P&lt;/i&gt;" in html
    assert 'href="https://a.example/x?y=1&amp;z=2"' in html
    monkeypatch.delitem(CREDITS, "evil")
    assert credits_html(include_rainviewer=False).count("<li>") == len(CREDITS) - 1
    assert "RainViewer" not in render_dashboard(include_rainviewer=False).split("<footer>")[1]


def test_openapi_description_carries_the_notice(world: World) -> None:
    description = world.client.get("/openapi.json").json()["info"]["description"]
    assert "do not replace official warnings" in description
    assert "attribution" in description
    schema = world.client.get("/openapi.json").json()["components"]["schemas"]
    assert "attribution" in schema["HazardAlert"]["properties"]
    assert "attribution" in schema["MarineConditions"]["properties"]
