"""Tests for the coastal weather stations (``GET /v1/surf/stations``) and their 6-minute observations (``GET /v1/surf/spots/{id}/station``)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

from app import clock
from app.collectors.meteofrance_obs import (
    FALLBACK_KEY_ENV,
    KEY_ENV,
    PAQUET_URL,
    MeteoFranceObsCollector,
    meteofrance_obs_key_from_env,
    parse_latest,
)
from app.main import aggregator, app
from app.services import stations

KEY = "mf-obs-test-token-1234567890=="
NOW = datetime(2026, 11, 15, 11, 42, tzinfo=timezone.utc).timestamp()
HOSSEGOR = "soorts-hossegor-plage-sud-point-d-or"

SAMPLE_PAQUET: list[dict[str, Any]] = [
    {
        # Biarritz-Pays-Basque (64024001), ~24 km from Hossegor: easterly offshore wind (100 deg, 5.0 m/s = 18.0 km/h)
        "geo_id_insee": "64024001",
        "validity_time": "2026-11-15T11:36:00Z",
        "ff": 5.0,
        "fxi10": 8.0,
        "dd": 100,
        "t": 289.15,
        "u": 68,
        "pmer": 101850,
        "rr_per": 0.0,
    },
    {
        # Capbreton (40065001) closer to Hossegor, but without an anemometer (`ff` is null) -> must be skipped
        "geo_id_insee": "40065001",
        "validity_time": "2026-11-15T11:36:00Z",
        "ff": None,
        "t": 290.15,
    },
    {
        # Calm wind (ff == 0 and dd == 0) -> wind_from_deg is None; dd == 360 -> 0
        "geo_id_insee": "17300009",
        "validity_time": "2026-11-15T11:36:00Z",
        "ff": 0.0,
        "fxy": 1.5,
        "dd": 0,
        "t": 287.15,
        "u": 82,
        "pmer": 102000,
        "rr_per": 0.2,
    },
]


def collector_with(payload: Any = SAMPLE_PAQUET, status_code: int = 200) -> tuple[MeteoFranceObsCollector, list[httpx.Request]]:
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        if status_code != 200:
            return httpx.Response(status_code, text="upstream error")
        return httpx.Response(200, json=payload)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return MeteoFranceObsCollector(KEY, http_client=client), sent


def test_every_spot_has_its_nearest_coastal_station() -> None:
    answer = stations.spot_stations()
    assert answer.count == len(answer.spots) == 171
    for row in answer.spots:
        assert 0 <= row.distance_km <= 36.0
        assert row.comparable == (row.distance_km <= stations.MAX_COMPARABLE_KM)
    assert answer.within_15_km == sum(r.distance_km <= 15 for r in answer.spots)
    assert answer.within_25_km == sum(r.distance_km <= 25 for r in answer.spots)
    assert answer.within_15_km <= answer.within_25_km <= answer.count


def test_stations_list_route() -> None:
    body = TestClient(app).get("/v1/surf/stations").json()
    assert body["count"] == 171 and "POSTES_MF.csv" in body["attribution"] and "distance_km" in body["derived"]


def test_key_from_env_prefers_obs_key_and_falls_back_to_meteofrance_key() -> None:
    assert meteofrance_obs_key_from_env({}) == (None, None)
    assert meteofrance_obs_key_from_env({FALLBACK_KEY_ENV: KEY}) == (KEY, None)
    other = "mf-dedicated-obs-token-987654"
    assert meteofrance_obs_key_from_env({KEY_ENV: other, FALLBACK_KEY_ENV: KEY}) == (other, None)
    bad, problem = meteofrance_obs_key_from_env({KEY_ENV: "short"})
    assert bad is None and problem is not None and "short" not in problem


def test_parse_latest_converts_units_and_skips_stations_without_wind() -> None:
    readings = parse_latest(SAMPLE_PAQUET)
    assert set(readings) == {"64024001", "17300009"}
    biarritz = readings["64024001"]
    assert biarritz.wind_speed_kmh == 18.0
    assert biarritz.wind_gust_kmh == 28.8
    assert biarritz.wind_from_deg == 100
    assert biarritz.air_temp_c == 16.0
    assert biarritz.humidity_pct == 68
    assert biarritz.pressure_hpa == 1018.5
    assert biarritz.precip_6m_mm == 0.0

    rochelle = readings["17300009"]
    assert rochelle.wind_speed_kmh == 0.0
    assert rochelle.wind_gust_kmh == 5.4
    assert rochelle.wind_from_deg is None


def test_the_key_is_sent_in_the_apikey_header_and_never_in_the_url() -> None:
    collector, sent = collector_with()
    latest = asyncio.run(collector.fetch_latest())
    assert len(sent) == 1 and "64024001" in latest.readings
    req = sent[0]
    assert req.headers["apikey"] == KEY
    assert KEY not in str(req.url)
    assert str(req.url) == f"{PAQUET_URL}?format=json"


def test_spot_station_route_and_wind_forecast_comparison(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(clock, "_wall", lambda: NOW)
    client = TestClient(app)
    monkeypatch.setattr(aggregator, "meteofrance_obs", None)
    off = client.get(f"/v1/surf/spots/{HOSSEGOR}/station")
    assert off.status_code == 200 and off.json()["status"] == "not_configured" and off.json()["measure"] is None

    collector, _ = collector_with()
    monkeypatch.setattr(aggregator, "meteofrance_obs", collector)
    on = client.get(f"/v1/surf/spots/{HOSSEGOR}/station")
    body = on.json()
    assert on.status_code == 200 and body["status"] == "ok"
    assert body["station"]["id"] == "64024001" and body["station"]["name"] == "BIARRITZ-PAYS-BASQUE"
    assert body["measure"]["wind_speed_kmh"] == 18.0
    assert body["measure"]["wind_gust_kmh"] == 28.8
    assert body["measure"]["wind_from_deg"] == 100
    assert body["measure"]["wind_relative"] == "offshore"
    assert body["measure"]["age_minutes"] == 6
    assert body["comparison"] is None

    # Compare against the hourly wind forecast (`compare=true`)
    forecast_hour = int(datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc).timestamp())
    wind_data = {
        "times": [forecast_hour - 3600, forecast_hour, forecast_hour + 3600],
        "wind_speed_10m": [12.0, 14.0, 16.0],
        "wind_gusts_10m": [20.0, 22.0, 25.0],
        "wind_direction_10m": [95, 90, 85],
    }
    monkeypatch.setattr(aggregator.surf, "fetch_wind", AsyncMock(return_value=(wind_data, False)))
    compared = client.get(f"/v1/surf/spots/{HOSSEGOR}/station", params={"compare": "true"}).json()
    comp = compared["comparison"]
    assert comp is not None and comp["status"] == "ok" and comp["forecast_time_utc"] == forecast_hour
    assert comp["model_wind_speed_kmh"] == 14.0 and comp["speed_diff_kmh"] == 4.0
    assert comp["model_wind_gust_kmh"] == 22.0 and comp["gust_diff_kmh"] == 6.8
    assert comp["model_wind_from_deg"] == 90 and comp["direction_diff_deg"] == 10
    assert comp["model_wind_relative"] == "offshore" and comp["regime_match"] is True
    assert "comparison" in compared["derived"] and "Open-Meteo" in compared["attribution"]
    assert client.get("/v1/surf/spots/nowhere/station").status_code == 404


def test_health_lists_meteofrance_obs_as_disabled_without_key_and_provider_with_key(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(app)
    monkeypatch.setattr(aggregator, "meteofrance_obs", None)
    health = client.get("/v1/health").json()
    assert "meteofrance_obs" in [s["provider_id"] for s in health["disabled_sources"]]
    collector, _ = collector_with()
    monkeypatch.setattr(aggregator, "meteofrance_obs", collector)
    health = client.get("/v1/health").json()
    assert "meteofrance_obs" in [p["provider_id"] for p in health["providers"]]
    assert "meteofrance_obs" not in [s["provider_id"] for s in health["disabled_sources"]]
