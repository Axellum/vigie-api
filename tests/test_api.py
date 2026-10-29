"""Integration tests of the HTTP API: endpoints, display profiles, fallback and unknown values."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from app.collectors.base import CollectorUnavailableError
from app.collectors.weather import MetNorwayCollector, OpenMeteoCollector
from app.main import aggregator, scheduler, ws_hub
from app.models.schemas import CircuitState, SeverityLevel
from app.services.aggregator import AggregatorService
from app.services.embedded_frame import verify_embedded_frame_crc
from app.services.scheduler import detect_new_or_upgraded_hazards
from tests import samples
from tests.conftest import World
from tests.helpers import SNAPSHOT_BUDGET_BYTES, expired_entry

# ============================================================================
# Weather fallback
# ============================================================================


@pytest.mark.asyncio
async def test_weather_falls_back_to_met_norway_when_open_meteo_fails() -> None:
    """When Open-Meteo fails the service switches to MET Norway and opens the circuit breaker."""
    om_collector = OpenMeteoCollector()
    metno_collector = MetNorwayCollector()
    om_collector._collect = AsyncMock(side_effect=RuntimeError("Open-Meteo HTTP 503"))  # type: ignore[method-assign]
    metno_collector._collect = AsyncMock(return_value=samples.parsed_metno())  # type: ignore[method-assign]
    service = AggregatorService(open_meteo=om_collector, met_norway=metno_collector)

    for _ in range(3):
        bundle, fallback_used = await service.fetch_weather_with_fallback(48.8566, 2.3522)
        assert fallback_used is True
        assert bundle is not None
        assert "MET Norway" in bundle["provider_name"]
        assert bundle["current"].temp_c == 14.8
        assert bundle["current"].rain_1h_mm == 1.6

    assert om_collector.circuit_state == CircuitState.OPEN
    assert om_collector.consecutive_failures >= 3
    assert metno_collector.circuit_state == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_weather_uses_the_stale_cache_and_says_so() -> None:
    om_collector = OpenMeteoCollector()
    metno_collector = MetNorwayCollector()
    # cached long ago (monotonic clock), so the entries are stale whatever the machine uptime
    now_key, daily_key = om_collector.cache_keys_for(48.8566, 2.3522)
    om_collector._cache[now_key] = expired_entry(OpenMeteoCollector.parse_now(samples.open_meteo_payload()))
    om_collector._cache[daily_key] = expired_entry(OpenMeteoCollector.parse_daily(samples.open_meteo_payload()))
    om_collector._collect = AsyncMock(side_effect=RuntimeError("down"))  # type: ignore[method-assign]
    metno_collector._collect = AsyncMock(side_effect=RuntimeError("down"))  # type: ignore[method-assign]
    service = AggregatorService(open_meteo=om_collector, met_norway=metno_collector)

    bundle, fallback_used = await service.fetch_weather_with_fallback(48.8566, 2.3522)
    assert fallback_used is True
    assert bundle is not None
    assert bundle["stale"] is True
    assert "stale cache" in bundle["provider_name"]
    assert bundle["current"].temp_c == 18.2


@pytest.mark.asyncio
async def test_no_weather_at_all_means_no_bundle() -> None:
    """When Open-Meteo, MET Norway and the cache all fail there is no weather: nothing is invented."""
    om_collector = OpenMeteoCollector()
    metno_collector = MetNorwayCollector()
    om_collector._collect = AsyncMock(side_effect=RuntimeError("down"))  # type: ignore[method-assign]
    metno_collector._collect = AsyncMock(side_effect=RuntimeError("down"))  # type: ignore[method-assign]
    service = AggregatorService(open_meteo=om_collector, met_norway=metno_collector)

    bundle, _ = await service.fetch_weather_with_fallback(48.8566, 2.3522)
    assert bundle is None
    assert not hasattr(service, "_build_offline_weather_bundle")


# ============================================================================
# Endpoints
# ============================================================================


def test_health_and_providers(world: World) -> None:
    health = world.client.get("/v1/health")
    assert health.status_code == 200
    data = health.json()
    assert data["status"] == "ok"
    assert data["storage_journal_mode"] == "wal"
    assert {"waveshare-7b", "tab5"} <= set(data["supported_devices"])
    assert data["providers_summary"]["total"] == 33  # 22, four SWPC products of the aurora, five travel ministries, ECDC, and the surf forecast
    assert data["scheduler"]["running"] is False  # the scheduler is opt-in (VIGIE_SCHEDULER)

    providers = world.client.get("/v1/providers")
    assert providers.status_code == 200
    ids = {p["provider_id"] for p in providers.json()}
    assert len(providers.json()) == 33
    assert {
        "usgs_eq",
        "emsc_seismic",
        "gdacs_global",
        "usgs_volcano_gvp",
        "open_meteo_hd",
        "met_norway_fallback",
        "open_meteo_marine",
        "open_meteo_flood",
        "open_meteo_aqi",
        "meteoalarm_atom",
        "meteoalarm_cap",
        "travel_gb",
        "travel_us",
        "travel_ca",
        "travel_au",
        "travel_nl",
    } <= ids


def test_point_endpoint_marine_gps(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    quake = samples.earthquake()
    emsc_quake = samples.earthquake("emsc_test_dup", source="EMSC", value=6.6, lat=35.18, lon=25.24, tsunami=False)
    monkeypatch.setattr(aggregator.usgs, "fetch", AsyncMock(return_value=[quake]))
    monkeypatch.setattr(aggregator.emsc, "fetch", AsyncMock(return_value=[emsc_quake]))

    resp = world.client.get(
        "/v1/point?lat=47.75&lon=-3.36&marine=true&radius_km=3500&sog_knots=8.2&cog_deg=215&device_type=marine"
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["coordinates"]["lat"] == 47.75 and payload["coordinates"]["sog_knots"] == 8.2
    assert payload["utc_offset_s"] == 7200
    assert payload["current"]["temp_c"] == 18.2
    assert payload["current"]["aqi_eu"] == 31  # from the air quality provider
    assert [d["date"] for d in payload["daily"]][:2] == ["2026-10-18", "2026-10-19"]
    assert payload["rain_nowcast"]["mm_per_step"] == [0.0, 0.2, 0.5, 0.1]
    assert payload["rain_nowcast"]["rain_expected_60m"] is True
    assert payload["marine"]["wave_height_m"] == 4.2

    h0 = payload["nearby_hazards"][0]
    assert h0["source"] == "USGS+EMSC"
    assert h0["value_primary"] == 6.6
    assert h0["tsunami_eta_minutes"] > 0
    assert h0["bearing_cardinal"] in {"N", "NE", "E", "SE", "S", "SW", "W", "NW"}


def test_point_endpoint_reports_unknown_as_null(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    """With every optional provider down, the point response says "unknown" and invents nothing."""
    monkeypatch.setattr(aggregator.open_meteo, "fetch_point", AsyncMock(side_effect=CollectorUnavailableError("down")))
    for collector, method in (
        (aggregator.air_quality, "fetch_air_quality"),
        (aggregator.flood, "fetch_flood_risk"),
        (aggregator.marine, "fetch_marine"),
        (aggregator.rainviewer, "fetch"),
        (aggregator.swpc, "fetch"),
    ):
        monkeypatch.setattr(collector, method, AsyncMock(side_effect=CollectorUnavailableError("down")))

    payload = world.client.get("/v1/point?lat=47.75&lon=-3.36&marine=true").json()
    assert payload["current"] is None
    assert payload["weather_provider"] is None
    assert payload["fallback_used"] is False
    assert payload["daily"] == []
    assert payload["utc_offset_s"] is None
    assert payload["rain_nowcast"]["mm_per_step"] is None
    assert payload["rain_nowcast"]["rain_expected_60m"] is None
    assert payload["rain_nowcast"]["radar_tile_url_template"] is None
    assert payload["rain_nowcast"]["radar_timestamp_utc"] is None
    assert payload["marine"] is None
    assert payload["flood_risk"] is None
    assert payload["air_quality"] is None
    assert payload["space_weather"] == {
        "solar_kp": None,
        "solar_storm_level": None,
        "gps_degraded": None,
        "hf_radio_blackout_risk": None,
        "attribution": None,  # nothing to credit when nothing is known
    }


def test_snapshot_zones_carry_air_quality_and_real_days(world: World) -> None:
    snap = world.client.get("/v1/snapshot?device_type=tab5&compact=true").json()
    assert len(snap["zones"]) == 5
    zone = snap["zones"][0]
    assert zone["current"]["aqi_eu"] == 31
    assert zone["current"]["uv_index"] == 1.7
    assert zone["weather_source"] == "Open-Meteo"
    assert zone["utc_offset_s"] == 7200  # the provider's offset wins over the configured one
    assert zone["daily_10d"][0]["date"] == "2026-10-18"
    assert zone["daily_10d"][0]["rain_probability_pct"] == 80
    assert snap["global_status"]["solar_kp"] == 2.3
    assert snap["global_status"]["zones_without_weather"] == 0


def test_snapshot_zone_without_weather_is_empty_not_invented(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(aggregator.open_meteo, "fetch_point", AsyncMock(side_effect=CollectorUnavailableError("down")))
    monkeypatch.setattr(aggregator.air_quality, "fetch_air_quality", AsyncMock(side_effect=CollectorUnavailableError("down")))

    snap = world.client.get("/v1/snapshot").json()
    assert snap["global_status"]["zones_without_weather"] == 5
    assert snap["global_status"]["weather_fallback_active"] is False
    for zone in snap["zones"]:
        assert zone["current"] is None
        assert zone["rain_next_60m"] is None
        assert zone["daily_10d"] == []
        assert zone["weather_source"] is None
        assert isinstance(zone["utc_offset_s"], int)  # the configured offset, known by construction


def test_hazards_unknown_when_no_source_answers(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    """If every hazard feed is down the status is "unknown", not an all-clear GREEN."""
    for collector in (aggregator.usgs, aggregator.emsc, aggregator.gdacs, aggregator.noaa_nws, aggregator.volcano, aggregator.nhc, aggregator.tsunami):
        monkeypatch.setattr(collector, "fetch", AsyncMock(side_effect=CollectorUnavailableError("down")))

    status = world.client.get("/v1/snapshot").json()["global_status"]
    assert status["max_severity"] is None
    assert status["active_red_count"] is None
    assert status["hazard_sources_ok"] == 0 and status["hazard_sources_total"] == 7


def test_hazards_green_when_sources_answer_with_nothing(world: World) -> None:
    status = world.client.get("/v1/snapshot").json()["global_status"]
    assert status["max_severity"] == "GREEN"
    assert status["active_red_count"] == 0
    assert status["hazard_sources_ok"] == 7


def test_simulation_snapshot_size_and_standards(world: World, simulation_on: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(aggregator.usgs, "fetch", AsyncMock(return_value=[samples.earthquake()]))

    sim = world.client.post("/v1/simulate", json={"scenario": "mega_tsunami_japan"})
    assert sim.status_code == 200
    assert sim.json()["simulation_active"] is True
    assert sim.json()["injected_hazards_count"] == 2
    assert all("SIMULATION" in h["title"] for h in sim.json()["injected_hazards"])

    for dev in ("waveshare-7b", "tab5"):
        snap = world.client.get(f"/v1/snapshot?device_type={dev}&compact=true")
        assert snap.status_code == 200
        size = len(json.dumps(snap.json(), separators=(",", ":")).encode("utf-8"))
        assert size < SNAPSHOT_BUDGET_BYTES, f"payload too large for {dev}: {size} bytes"
        assert snap.json()["simulation_active"] is True
        assert snap.json()["global_status"]["max_severity"] == "RED"

    geo = world.client.get("/v1/geojson/hazards").json()
    assert geo["type"] == "FeatureCollection" and len(geo["bbox"]) == 4
    assert geo["features"][0]["geometry"]["type"] == "Point"

    cap = world.client.get("/v1/cap/alerts").json()
    assert cap["protocol"] == "OASIS CAP 1.2"
    assert cap["alert_count"] >= 3
    alert = cap["alerts"][0]
    assert alert["status"] == "Exercise"  # simulation active
    assert alert["sender"] == "vigie-api"
    info = alert["info"][0]
    assert info["language"] == "en-US"
    assert info["web"] is None
    assert info["area"][0]["circle"].endswith(" 0")  # the affected radius is unknown: a point

    cleared = world.client.post("/v1/simulate", json={"scenario": "clear"})
    assert cleared.json()["simulation_active"] is False


def test_cap_sender_is_configurable(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VIGIE_CAP_SENDER", "alerts@example.org")
    monkeypatch.setattr(aggregator.usgs, "fetch", AsyncMock(return_value=[samples.earthquake()]))
    cap = world.client.get("/v1/cap/alerts").json()
    assert cap["alerts"][0]["sender"] == "alerts@example.org"
    assert cap["alerts"][0]["status"] == "Actual"


def test_cap_does_not_invent_a_value(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    no_value = samples.earthquake("noval", value=None)
    monkeypatch.setattr(aggregator.usgs, "fetch", AsyncMock(return_value=[no_value]))
    info = world.client.get("/v1/cap/alerts").json()["alerts"][0]["info"][0]
    assert "Value:" not in info["description"]
    assert info["parameters"]["value_primary"] is None


def test_zones_crud_and_history(world: World, simulation_on: None) -> None:
    assert len(world.client.get("/v1/zones").json()) == 5

    created = world.client.post(
        "/v1/zones",
        json={
            "id": "z_lille",
            "name": "Lille, FR",
            "lat": 50.6292,
            "lon": 3.0573,
            "utc_offset_s": 3600,
            "alert_radius_km": 350.0,
            "is_marine": False,
        },
    )
    assert created.status_code == 200 and created.json()["id"] == "z_lille"
    assert len(world.client.get("/v1/zones").json()) == 6
    assert world.client.delete("/v1/zones/z_lille").status_code == 200
    assert world.client.delete("/v1/zones/z_lille").status_code == 404
    assert len(world.client.get("/v1/zones").json()) == 5

    world.client.post("/v1/simulate", json={"scenario": "volcano_eruption_iceland"})
    world.client.get("/v1/snapshot")  # recording happens while collecting
    history = world.client.get("/v1/history?category=VOLCANO&min_severity=RED").json()
    assert history["count"] >= 1 and history["items"][0]["category"] == "VOLCANO"


def test_zone_without_utc_offset_is_rejected(world: World) -> None:
    """A zone must carry its UTC offset: there is no silent default of zero."""
    resp = world.client.post("/v1/zones", json={"name": "Nowhere", "lat": 1.0, "lon": 2.0})
    assert resp.status_code == 422


def test_websocket_stream(world: World) -> None:
    with world.client.websocket_connect("/v1/stream") as ws:
        init_msg = ws.receive_json()
        assert init_msg["event"] == "initial_snapshot"
        assert "global_status" in init_msg
        ws.send_text("ping")
        assert ws.receive_json()["event"] == "pong"


# ============================================================================
# Scheduler and dashboard
# ============================================================================


def test_detect_new_or_upgraded_hazards() -> None:
    yellow = samples.earthquake("eq_1", severity=SeverityLevel.YELLOW, value=5.2, tsunami=False)
    assert len(detect_new_or_upgraded_hazards({}, [yellow])) == 1  # new
    assert detect_new_or_upgraded_hazards({"eq_1": yellow}, [yellow]) == []  # unchanged

    worse = yellow.model_copy(update={"severity": SeverityLevel.RED, "value_primary": 7.1, "tsunami_warning": True})
    assert detect_new_or_upgraded_hazards({"eq_1": yellow}, [worse])[0].severity == SeverityLevel.RED

    # an unknown value is never "higher" than a known one
    unknown = yellow.model_copy(update={"value_primary": None})
    assert detect_new_or_upgraded_hazards({"eq_1": yellow}, [unknown]) == []


def test_scheduler_endpoints_and_websocket_push(world: World, simulation_on: None, monkeypatch: pytest.MonkeyPatch) -> None:
    scheduler._known_hazards.clear()
    scheduler.cycles_count = 0
    broadcast_mock = AsyncMock()
    monkeypatch.setattr(ws_hub, "broadcast", broadcast_mock)

    assert world.client.get("/v1/scheduler/status").json()["cycles_count"] == 0

    world.client.post("/v1/simulate", json={"scenario": "cat5_hurricane_caribbean"})
    broadcast_mock.reset_mock()

    first = world.client.post("/v1/scheduler/trigger").json()
    assert first["status"] == "ok" and first["cycles_count"] == 1
    assert first["new_or_upgraded_count"] == 1
    assert first["max_severity"] == "RED"
    assert broadcast_mock.await_count == 1

    pushed = broadcast_mock.await_args.args[0]
    assert pushed["event"] == "alert_push"
    assert verify_embedded_frame_crc(pushed["embedded_frame"]) is True

    broadcast_mock.reset_mock()
    second = world.client.post("/v1/scheduler/trigger").json()
    assert second["new_or_upgraded_count"] == 0
    assert broadcast_mock.await_count == 0


def test_dashboard_html(world: World) -> None:
    resp = world.client.get("/dashboard")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    html = resp.text
    assert "Vigie API" in html
    assert "mega_tsunami_japan" in html
    assert "/v1/embedded/frame" in html
    assert "waveshare-7b" in html and "tab5" in html
    assert "DEFCON" not in html
    assert "n/a" in html  # unknown values are displayed as n/a
