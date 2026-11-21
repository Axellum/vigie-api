"""Tests for the wind of every Météo-France ground station of a box (``GET /v1/stations/wind``)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app import clock
from app.collectors.meteofrance_obs import MeteoFranceObsCollector, parse_latest
from app.main import aggregator, app
from app.services import station_wind

KEY = "mf-obs-test-token-1234567890=="
NOW = datetime(2026, 11, 21, 22, 30, tzinfo=timezone.utc).timestamp()

PAQUET: list[dict[str, Any]] = [
    # Ouessant-Stiff: strong south-easterly, named in the bundled list
    {"geo_id_insee": "29155005", "lat": 48.473333, "lon": -5.057, "validity_time": "2026-11-21T22:24:00Z", "ff": 16.0, "raf10": 20.2, "dd": 130},
    # Biarritz: weaker, inside the same box
    {"geo_id_insee": "64024001", "lat": 43.468, "lon": -1.531, "validity_time": "2026-11-21T22:24:00Z", "ff": 5.0, "raf10": 8.0, "dd": 100},
    # Calm: no direction
    {"geo_id_insee": "17300009", "lat": 46.15, "lon": -1.15, "validity_time": "2026-11-21T22:24:00Z", "ff": 0.0, "dd": 0},
    # Too old (more than an hour)
    {"geo_id_insee": "33281001", "lat": 44.83, "lon": -0.69, "validity_time": "2026-11-21T20:00:00Z", "ff": 9.0, "dd": 250},
    # No position in the package: never placed on a map
    {"geo_id_insee": "40065001", "validity_time": "2026-11-21T22:24:00Z", "ff": 3.0, "dd": 90},
    # Guadeloupe: outside a box of France, and a code the bundled list does not hold
    {"geo_id_insee": "97199999", "lat": 16.26, "lon": -61.52, "validity_time": "2026-11-21T22:24:00Z", "ff": 7.0, "dd": 80},
]


def collector_with(payload: Any = PAQUET) -> MeteoFranceObsCollector:
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)))
    return MeteoFranceObsCollector(KEY, http_client=client)


def test_parse_latest_keeps_the_position_of_a_list_and_of_geojson() -> None:
    listed = parse_latest(PAQUET)
    assert (listed["29155005"].lat, listed["29155005"].lon) == (48.47333, -5.057)
    assert listed["40065001"].lat is None and listed["40065001"].lon is None
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [-1.531, 43.468]},
                "properties": {"geo_id_insee": "64024001", "validity_time": "2026-11-21T22:24:00Z", "ff": 5.0},
            }
        ],
    }
    assert (parse_latest(geojson)["64024001"].lat, parse_latest(geojson)["64024001"].lon) == (43.468, -1.531)
    bad = parse_latest([{"geo_id_insee": "1", "lat": 123.0, "lon": 2.0, "validity_time": "2026-11-21T22:24:00Z", "ff": 1.0}])
    assert bad["00000001"].lat is None


def test_every_station_with_a_6_minute_wind_has_a_name_file_row() -> None:
    known = station_wind.names()
    assert len(known) > 2000
    assert known["29155005"] == ("OUESSANT-STIFF", 64)


def test_station_wind_route(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(clock, "_wall", lambda: NOW)
    client = TestClient(app)

    monkeypatch.setattr(aggregator, "meteofrance_obs", None)
    off = client.get("/v1/stations/wind", params={"bbox": "-6,41,10,52"}).json()
    assert off["status"] == "not_configured" and off["count"] == 0 and off["stations"] == []

    monkeypatch.setattr(aggregator, "meteofrance_obs", collector_with())
    on = client.get("/v1/stations/wind", params={"bbox": "-6,41,10,52"})
    assert on.status_code == 200 and on.headers["cache-control"] == "public, max-age=180"
    body = on.json()
    assert body["status"] == "ok" and body["bbox"] == [-6.0, 41.0, 10.0, 52.0]
    assert [s["id"] for s in body["stations"]] == ["29155005", "64024001", "17300009"]  # strongest first, no old, no unplaced
    first = body["stations"][0]
    assert first["name"] == "OUESSANT-STIFF" and first["alt_m"] == 64
    assert first["wind_speed_kmh"] == 57.6 and first["wind_gust_kmh"] == 72.7 and first["wind_from_deg"] == 130
    assert first["age_minutes"] == 6 and body["measured_utc"] == first["measured_utc"]
    assert body["stations"][2]["wind_from_deg"] is None
    assert body["count"] == 3 and body["truncated"] is False
    assert "PaquetObs" in body["attribution"] and "POSTES_MF.csv" in body["attribution"]

    top = client.get("/v1/stations/wind", params={"bbox": "-6,41,10,52", "limit": 1}).json()
    assert [s["id"] for s in top["stations"]] == ["29155005"] and top["truncated"] is True

    antilles = client.get("/v1/stations/wind", params={"bbox": "-62,15.8,-60.8,16.6"}).json()
    assert [s["id"] for s in antilles["stations"]] == ["97199999"] and antilles["stations"][0]["name"] is None

    assert client.get("/v1/stations/wind", params={"bbox": "1,2,3"}).status_code == 422
    assert client.get("/v1/stations/wind").status_code == 422
