"""Offline recorded-fixture and API tests for CAMS European pollens via Open-Meteo Air Quality."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.attribution import AIR_QUALITY, credit_text
from app.collectors.flood_air import OpenMeteoAirQualityCollector, is_in_cams_europe
from app.models.pollens import POLLEN_API_VARIABLES, POLLEN_SPECIES
from tests.helpers import SimClock

FIXTURES = Path(__file__).parent / "fixtures" / "pollens"


def _load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_cams_europe_domain_bounds() -> None:
    assert is_in_cams_europe(30.0, -25.0) is True
    assert is_in_cams_europe(71.9, 45.0) is True
    assert is_in_cams_europe(48.85, 2.35) is True
    assert is_in_cams_europe(29.9, 0.0) is False
    assert is_in_cams_europe(72.0, 0.0) is False
    assert is_in_cams_europe(48.85, -25.1) is False
    assert is_in_cams_europe(48.85, 45.1) is False


def test_parse_pollens_malaga_recorded_fixture() -> None:
    payload = _load_fixture("malaga_2026-11-15.json")
    status = OpenMeteoAirQualityCollector.parse_response(payload)

    assert status.european_aqi is not None
    assert status.pollens is not None
    pollens = status.pollens
    assert pollens.observed_time == "2026-11-15T18:00"
    assert pollens.current is not None
    assert pollens.current.mugwort_grains_m3 == 2.5
    assert pollens.current.alder_grains_m3 == 0.0
    assert pollens.current.birch_grains_m3 == 0.0
    assert pollens.current.grass_grains_m3 == 0.0
    assert pollens.current.olive_grains_m3 == 0.0
    assert pollens.current.ragweed_grains_m3 == 0.0
    assert pollens.dominant_species == "mugwort"
    assert pollens.derived == ["daily", "dominant_species"]
    assert pollens.stale is False
    assert pollens.attribution == credit_text(*AIR_QUALITY)

    assert len(pollens.daily) == 4
    assert [day.mugwort_max_grains_m3 for day in pollens.daily] == [4.6, 2.5, 4.7, 0.5]
    assert [day.date for day in pollens.daily] == [
        "2026-11-15",
        "2026-11-16",
        "2026-11-17",
        "2026-11-18",
    ]


def test_parse_pollens_paris_off_season_preserves_zeros() -> None:
    payload = _load_fixture("paris_2026-11-15.json")
    pollens = OpenMeteoAirQualityCollector.parse_pollens(payload)

    assert pollens is not None
    assert pollens.current is not None
    for species in POLLEN_SPECIES:
        assert getattr(pollens.current, f"{species}_grains_m3") == 0.0
    assert pollens.dominant_species is None
    assert pollens.derived == ["daily"]
    assert len(pollens.daily) == 4
    assert all(day.grass_max_grains_m3 == 0.0 for day in pollens.daily)


def test_parse_pollens_outside_europe_returns_none_without_fabricating() -> None:
    payload = _load_fixture("new_york_outside_europe_2026-11-15.json")
    status = OpenMeteoAirQualityCollector.parse_response(payload)

    assert status.european_aqi is not None
    assert status.pollens is None


@pytest.mark.asyncio
async def test_collector_request_params_weight_and_stale_cache(sim_clock: SimClock) -> None:
    malaga = _load_fixture("malaga_2026-11-15.json")
    new_york = _load_fixture("new_york_outside_europe_2026-11-15.json")
    queries: list[dict[str, str]] = []
    fail_europe = False

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        queries.append(params)
        lat = float(params["latitude"])
        if lat < 38.0:
            if fail_europe:
                return httpx.Response(503)
            return httpx.Response(200, json=malaga)
        return httpx.Response(200, json=new_york)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    collector = OpenMeteoAirQualityCollector(http_client=client)
    try:
        in_eu = await collector.fetch_air_quality(36.74, -4.42)
        assert in_eu.pollens is not None
        assert in_eu.pollens.stale is False
        eu_hourly = queries[0]["hourly"].split(",")
        assert all(v in eu_hourly for v in POLLEN_API_VARIABLES.values())
        assert queries[0]["forecast_days"] == "4"
        assert collector.call_weight(queries[0]) == 1.4

        outside = await collector.fetch_air_quality(40.71, -74.01)
        assert outside.pollens is None
        outside_hourly = queries[1]["hourly"].split(",")
        assert not any(v in outside_hourly for v in POLLEN_API_VARIABLES.values())
        assert queries[1]["forecast_days"] == "4"
        assert collector.call_weight(queries[1]) == 1.0

        # After the 1-hour TTL expires and upstream returns 503, stale flag propagates to pollens.
        fail_europe = True
        sim_clock.advance(3605.0)
        stale_eu = await collector.fetch_air_quality(36.74, -4.42)
        assert stale_eu.stale is True
        assert stale_eu.pollens is not None
        assert stale_eu.pollens.stale is True
    finally:
        await client.aclose()


def test_point_and_pollens_endpoints_europe_cache_and_outside_coverage(
    world: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    malaga = _load_fixture("malaga_2026-11-15.json")
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=malaga)

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    collector = OpenMeteoAirQualityCollector(http_client=http_client)
    monkeypatch.setattr(world.aggregator, "air_quality", collector)

    # 1. Dedicated endpoint inside CAMS Europe
    resp = world.client.get("/v1/pollens", params={"lat": 36.74, "lon": -4.42})
    assert resp.status_code == 200
    body = resp.json()
    assert body["coverage"] == "cams_europe"
    assert body["status"] == "available"
    assert body["grid_lat"] == 36.7
    assert body["grid_lon"] == -4.4
    assert body["attribution"] == credit_text(*AIR_QUALITY)
    assert body["pollens"]["current"]["mugwort_grains_m3"] == 2.5
    assert body["pollens"]["dominant_species"] == "mugwort"
    assert len(body["pollens"]["daily"]) == 4
    assert calls == 1

    # 2. /v1/point in the same 0.1 degree cell reuses the cache with zero extra upstream calls
    point_resp = world.client.get("/v1/point", params={"lat": 36.72, "lon": -4.43})
    assert point_resp.status_code == 200
    point = point_resp.json()
    assert point["local_alert_level"] == "GREEN"
    assert point["air_quality"]["pollens"]["dominant_species"] == "mugwort"
    assert point["air_quality"]["pollens"]["current"]["mugwort_grains_m3"] == 2.5
    assert calls == 1

    # 3. Outside CAMS Europe: outside_coverage and zero upstream HTTP calls
    ny_resp = world.client.get("/v1/pollens", params={"lat": 40.71, "lon": -74.01})
    assert ny_resp.status_code == 200
    ny = ny_resp.json()
    assert ny["coverage"] == "outside_europe"
    assert ny["status"] == "outside_coverage"
    assert ny["pollens"] is None
    assert ny["attribution"] is None
    assert calls == 1


def test_pollens_endpoint_unavailable_when_upstream_fails_in_europe(
    world: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(failing_handler))
    collector = OpenMeteoAirQualityCollector(http_client=http_client)
    monkeypatch.setattr(world.aggregator, "air_quality", collector)

    resp = world.client.get("/v1/pollens", params={"lat": 48.85, "lon": 2.35})
    assert resp.status_code == 200
    body = resp.json()
    assert body["coverage"] == "cams_europe"
    assert body["status"] == "unavailable"
    assert body["pollens"] is None
    assert body["attribution"] is None
