"""``GET /v1/fires``, ``GET /v1/fires/grid``, the ``fires_nearby`` block of ``GET /v1/point`` and the health of the source.

The detections are the recorded ones (``tests/fixtures/firms``) read through a simulated FIRMS; every figure is compared with an independent
computation over the CSV rows (``tests/firms_samples.py``). An instance without a key has the source off, and says so.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.attribution import CREDITS, FIRMS_DISCLAIMER
from app.collectors.firms import FIRMSCollector
from app.main import aggregator, app
from app.models.fires import FiresCollection, FiresGrid
from app.models.schemas import PointResponse
from app.services.aggregator import AggregatorService
from app.services.fires import FireService
from tests.conftest import World
from tests.firms_samples import (
    EVENING,
    KEY,
    MIDNIGHT,
    N20,
    N21,
    FirmsServer,
    oracle_grid,
    oracle_ids,
    oracle_near,
    rows,
)
from tests.helpers import Provider, SimClock, make_service


@dataclass
class Fires:
    client: TestClient
    server: FirmsServer
    collector: FIRMSCollector
    world: World


@pytest.fixture()
def fires(world: World, sim_clock: SimClock, monkeypatch: pytest.MonkeyPatch) -> Iterator[Fires]:
    """The application with a FIRMS key: the recorded detections are already read, so no request starts a refresh."""
    sim_clock.EPOCH = EVENING
    server = FirmsServer(latency_ticks=0)
    collector = FIRMSCollector(KEY, http_client=server.client())
    monkeypatch.setattr(aggregator, "firms", collector)
    monkeypatch.setattr(aggregator, "fires", FireService(collector, None))
    asyncio.run(collector.fetch())
    yield Fires(client=world.client, server=server, collector=collector, world=world)


@pytest.fixture()
def no_key(world: World, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(aggregator, "firms", None)
    monkeypatch.setattr(aggregator, "fires", FireService(None, "VIGIE_FIRMS_MAP_KEY is not set"))
    return world.client


# ------------------------------------------------------------------------------------------------ the hotspots


def test_the_hotspots_of_a_box_are_those_of_the_recording(fires: Fires) -> None:
    response = fires.client.get("/v1/fires", params={"bbox": "-20,-35,50,15", "limit": 5000})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=60"
    body = response.json()
    FiresCollection.model_validate(body)  # the document describes the answer
    expected = oracle_ids("-20,-35,50,15")
    assert body["metadata"]["count"] == len(body["features"]) == body["metadata"]["total_matching"] == len(expected)
    assert body["metadata"]["truncated"] is False
    for feature in body["features"]:
        lon, lat = feature["geometry"]["coordinates"]
        assert -20 <= lon <= 50 and -35 <= lat <= 15
        assert feature["id"] == f"{feature['properties']['satellite']}_{lat:.5f}_{lon:.5f}_{feature['properties']['acq_utc']}"
    powers = [f["properties"]["frp_mw"] for f in body["features"]]
    assert powers == sorted(powers, reverse=True)  # the strongest first


def test_a_feature_is_the_detection_of_the_file_with_its_fields(fires: Fires) -> None:
    body = fires.client.get("/v1/fires", params={"bbox": "33.07,28.85,33.08,28.86"}).json()
    feature = next(f for f in body["features"] if f["properties"]["satellite"] == "N20")
    assert feature["type"] == "Feature" and feature["geometry"] == {"type": "Point", "coordinates": [33.07164, 28.85126]}
    assert feature["properties"] == {
        "acq_utc": MIDNIGHT + 60,
        "satellite": "N20",
        "instrument": "VIIRS",
        "confidence": "nominal",
        "frp_mw": 1.42,
        "bright_ti4_k": 314.28,
        "bright_ti5_k": 294.59,
        "scan_km": 0.41,
        "track_km": 0.45,
        "daynight": "night",
        "version": "2.0NRT",
    }


def test_every_answer_carries_the_credit_and_the_disclaimer_of_nasa(fires: Fires) -> None:
    for path in ("/v1/fires?bbox=-20,-35,50,15", "/v1/fires/grid?cell_deg=5", "/v1/fires?bbox=0,0,1,1", "/v1/fires/grid?bbox=0,0,1,1"):
        metadata = fires.client.get(path).json()["metadata"]
        assert metadata["attribution"] == CREDITS["nasa_firms"].text and metadata["disclaimer"] == FIRMS_DISCLAIMER
    assert "We acknowledge the use of data and/or imagery from NASA's Fire Information for Resource Management System (FIRMS)" in CREDITS["nasa_firms"].text


def test_the_metadata_says_what_was_asked_and_what_was_read(fires: Fires) -> None:
    metadata = fires.client.get("/v1/fires", params={"bbox": "-6,35,10,45", "hours": 12, "min_confidence": "nominal", "min_frp": 2, "limit": 7}).json()["metadata"]
    assert metadata["bbox"] == [-6, 35, 10, 45] and metadata["window_hours"] == 12 and metadata["min_confidence"] == "nominal"
    assert metadata["min_frp_mw"] == 2 and metadata["limit"] == 7
    assert metadata["generated_utc"] == int(EVENING) and metadata["data_read_utc"] == int(EVENING) and metadata["stale"] is False
    assert metadata["since_utc"] == int(EVENING - 12 * 3600) // 300 * 300 and metadata["since_utc"] % 300 == 0
    assert metadata["sources"] == [{"source": N20, "read_utc": int(EVENING), "ok": True}, {"source": N21, "read_utc": int(EVENING), "ok": True}]
    assert metadata["skipped_rows"] == 0


def test_a_product_that_has_never_been_read_is_listed_with_no_time(world: World, sim_clock: SimClock, monkeypatch: pytest.MonkeyPatch) -> None:
    """The first reading of a real instance (2026-10-20): NOAA-20 timed out, NOAA-21 answered. The answer must not look like a complete one."""
    sim_clock.EPOCH = EVENING
    server = FirmsServer(latency_ticks=0)
    server.failing = {N20}
    collector = FIRMSCollector(KEY, http_client=server.client())
    monkeypatch.setattr(aggregator, "firms", collector)
    monkeypatch.setattr(aggregator, "fires", FireService(collector, None))
    asyncio.run(collector.fetch())
    for path in ("/v1/fires?bbox=-180,-90,180,90", "/v1/fires/grid"):
        metadata = world.client.get(path).json()["metadata"]
        assert metadata["stale"] is True
        assert metadata["sources"] == [{"source": N20, "read_utc": None, "ok": False}, {"source": N21, "read_utc": int(EVENING), "ok": True}]
    assert KEY not in world.client.get("/v1/providers").text


def test_the_hotspots_beyond_the_limit_are_not_sent_and_the_answer_says_so(fires: Fires) -> None:
    body = fires.client.get("/v1/fires", params={"bbox": "-180,-90,180,90", "limit": 10}).json()
    assert len(body["features"]) == 10 and body["metadata"]["total_matching"] == 258 + 218 and body["metadata"]["truncated"] is True
    strongest = sorted((r["frp"] for r in rows() if not math.isnan(r["frp"])), reverse=True)[:10]
    assert [f["properties"]["frp_mw"] for f in body["features"]] == pytest.approx(strongest, abs=0.01)


def test_the_filters_of_confidence_power_and_window(fires: Fires) -> None:
    def count(**params: Any) -> int:
        return fires.client.get("/v1/fires", params={"bbox": "-180,-90,180,90", "limit": 5000, **params}).json()["metadata"]["total_matching"]

    assert count() == 258 + 218
    assert count(min_confidence="nominal") == len(oracle_ids("-180,-90,180,90", min_conf=2))
    assert count(min_confidence="high") == len(oracle_ids("-180,-90,180,90", min_conf=3))
    assert count(min_frp=10) == len(oracle_ids("-180,-90,180,90", min_frp=10.0))
    since = int(EVENING - 6 * 3600) // 300 * 300
    assert count(hours=6) == len(oracle_ids("-180,-90,180,90", since))
    assert 0 < count(hours=6) < count()


def test_the_window_moves_with_the_clock(fires: Fires, sim_clock: SimClock) -> None:
    sim_clock.advance(20 * 3600)  # 14:12 the next day: only the detections of the last 24 hours remain
    count = fires.client.get("/v1/fires", params={"bbox": "-180,-90,180,90", "limit": 5000}).json()["metadata"]["total_matching"]
    since = int(EVENING + 20 * 3600 - 24 * 3600) // 300 * 300
    assert count == len(oracle_ids("-180,-90,180,90", since)) < 258 + 218


def test_a_box_across_the_antimeridian(fires: Fires) -> None:
    body = fires.client.get("/v1/fires", params={"bbox": "170,-25,-170,-10", "limit": 5000}).json()
    longitudes = [f["geometry"]["coordinates"][0] for f in body["features"]]
    assert len(longitudes) == len(oracle_ids("170,-25,-170,-10")) > 0
    assert any(x > 170 for x in longitudes) and any(x < -170 for x in longitudes) and not any(-170 <= x <= 170 for x in longitudes)
    assert body["metadata"]["bbox"] == [170, -25, -170, -10]


@pytest.mark.parametrize(
    "params",
    [
        {},  # no box
        {"bbox": ""},
        {"bbox": "1,2,3"},
        {"bbox": "a,b,c,d"},
        {"bbox": "0,10,10,10"},
        {"bbox": "0,10,10,5"},
        {"bbox": "0,-91,10,0"},
        {"bbox": "0,0,10,91"},
        {"bbox": "0,0,541,5"},
        {"bbox": "0,0,10,10", "hours": 0},
        {"bbox": "0,0,10,10", "hours": 49},
        {"bbox": "0,0,10,10", "limit": 0},
        {"bbox": "0,0,10,10", "limit": 5001},
        {"bbox": "0,0,10,10", "min_confidence": "certain"},
        {"bbox": "0,0,10,10", "min_frp": -1},
    ],
)
def test_a_request_that_is_not_valid_is_refused(fires: Fires, params: dict[str, Any]) -> None:
    response = fires.client.get("/v1/fires", params=params)
    assert response.status_code == 422, params
    assert KEY not in response.text


# ------------------------------------------------------------------------------------------------ the grid


@pytest.mark.parametrize("cell", [0.25, 0.5, 1, 2, 5, 10])
def test_the_grid_counts_the_detections_of_each_cell(fires: Fires, cell: float) -> None:
    response = fires.client.get("/v1/fires/grid", params={"cell_deg": cell, "limit": 20000})
    assert response.status_code == 200
    body = response.json()
    FiresGrid.model_validate(body)
    expected = oracle_grid(cell)
    metadata = body["metadata"]
    assert metadata["cell_deg"] == cell and metadata["total_cells"] == len(expected) == metadata["count"] and metadata["truncated"] is False
    assert metadata["total_detections"] == 258 + 218 == sum(count for count, _, _ in expected.values())
    for feature in body["features"]:
        lon, lat = feature["geometry"]["coordinates"]
        key = (math.floor((lat + 90) / cell), math.floor((lon + 180) / cell))
        count, power, latest = expected[key]
        assert feature["properties"] == {"count": count, "max_frp_mw": pytest.approx(power, abs=0.01), "latest_utc": latest}


def test_the_grid_defaults_to_the_globe_in_one_degree_cells(fires: Fires) -> None:
    metadata = fires.client.get("/v1/fires/grid").json()["metadata"]
    assert metadata["bbox"] == [-180, -90, 180, 90] and metadata["cell_deg"] == 1 and metadata["limit"] == 5000 and metadata["window_hours"] == 24


def test_the_grid_of_a_box_counts_only_its_detections(fires: Fires) -> None:
    body = fires.client.get("/v1/fires/grid", params={"bbox": "-20,-35,50,15", "cell_deg": 2}).json()
    assert body["metadata"]["total_detections"] == len(oracle_ids("-20,-35,50,15"))
    assert all(-21 <= f["geometry"]["coordinates"][0] <= 51 and -36 <= f["geometry"]["coordinates"][1] <= 16 for f in body["features"])


def test_the_grid_keeps_the_busiest_cells_when_there_are_too_many(fires: Fires) -> None:
    body = fires.client.get("/v1/fires/grid", params={"cell_deg": 0.25, "limit": 5}).json()
    counts = [f["properties"]["count"] for f in body["features"]]
    assert len(counts) == 5 and counts == sorted(counts, reverse=True)
    assert body["metadata"]["truncated"] is True and body["metadata"]["total_cells"] > 5


def test_the_grid_has_the_same_filters_as_the_hotspots(fires: Fires) -> None:
    body = fires.client.get("/v1/fires/grid", params={"cell_deg": 1, "min_confidence": "high", "min_frp": 5, "hours": 12}).json()
    since = int(EVENING - 12 * 3600) // 300 * 300
    assert body["metadata"]["total_detections"] == len(oracle_ids("-180,-90,180,90", since, 3, 5.0))


@pytest.mark.parametrize("params", [{"cell_deg": 0.1}, {"cell_deg": 3}, {"cell_deg": 0}, {"cell_deg": 20}, {"bbox": "0,0,1"}, {"limit": 0}, {"limit": 20001}, {"hours": 100}])
def test_a_grid_request_that_is_not_valid_is_refused(fires: Fires, params: dict[str, Any]) -> None:
    assert fires.client.get("/v1/fires/grid", params=params).status_code == 422


# ------------------------------------------------------------------------------------------------ the summary of /v1/point


def test_the_point_carries_the_summary_of_the_fires_nearby(fires: Fires) -> None:
    response = fires.client.get("/v1/point", params={"lat": -16.6, "lon": 179.0, "fire_radius_km": 300})
    assert response.status_code == 200
    nearby = response.json()["fires_nearby"]
    PointResponse.model_validate(response.json())
    count, nearest, strongest, latest = oracle_near(-16.6, 179.0, 300.0, int(EVENING) - 86400)
    assert count > 0
    assert nearby["radius_km"] == 300 and nearby["count_24h"] == count and nearby["latest_utc"] == latest
    assert nearby["nearest_km"] == pytest.approx(nearest, abs=0.06) and nearby["max_frp_mw"] == pytest.approx(strongest, abs=0.01)
    assert nearby["data_read_utc"] == int(EVENING) and nearby["stale"] is False and nearby["sources"] == [N20, N21]
    assert nearby["derived"] == ["count_24h", "nearest_km", "max_frp_mw", "latest_utc"]
    assert nearby["attribution"] == CREDITS["nasa_firms"].text and nearby["disclaimer"] == FIRMS_DISCLAIMER


def test_the_default_radius_is_fifty_kilometres(fires: Fires) -> None:
    nearby = fires.client.get("/v1/point", params={"lat": 38.7, "lon": -9.1}).json()["fires_nearby"]
    assert nearby["radius_km"] == 50
    assert nearby["count_24h"] == oracle_near(38.7, -9.1, 50.0, int(EVENING) - 86400)[0]


def test_nothing_nearby_is_zero_with_unknown_distance_and_power(fires: Fires) -> None:
    nearby = fires.client.get("/v1/point", params={"lat": -60.0, "lon": -120.0}).json()["fires_nearby"]
    assert nearby["count_24h"] == 0 and nearby["nearest_km"] is None and nearby["max_frp_mw"] is None and nearby["latest_utc"] is None


def test_the_fires_never_raise_the_alert_level_of_the_point(fires: Fires, monkeypatch: pytest.MonkeyPatch) -> None:
    params = {"lat": -16.6, "lon": 179.0, "radius_km": 600, "fire_radius_km": 500}
    with_fires = fires.client.get("/v1/point", params=params).json()
    assert with_fires["fires_nearby"]["count_24h"] == oracle_near(-16.6, 179.0, 500.0, int(EVENING) - 86400)[0] > 20  # a lot of fire around
    monkeypatch.setattr(aggregator, "fires", FireService(None, "off"))
    without = fires.client.get("/v1/point", params=params).json()
    assert without["fires_nearby"] is None
    assert with_fires["local_alert_level"] == without["local_alert_level"]
    with_fires["fires_nearby"] = None
    assert with_fires == without  # the block is the only difference


@pytest.mark.parametrize("radius", [0, -5, 501, 10_000])
def test_the_radius_of_the_summary_is_bounded(fires: Fires, radius: float) -> None:
    assert fires.client.get("/v1/point", params={"lat": -16.6, "lon": 179.0, "fire_radius_km": radius}).status_code == 422


def test_the_summary_says_when_the_last_read_failed(fires: Fires, sim_clock: SimClock) -> None:
    sim_clock.advance(fires.collector.ttl_seconds + 1)
    fires.server.failing = {N20, N21}

    async def refresh_behind_the_reading() -> None:
        await fires.world.aggregator.fires.nearby(-16.6, 179.0)  # starts the refresh, which fails
        await fires.world.aggregator.fires.drain()

    asyncio.run(refresh_behind_the_reading())
    nearby = fires.client.get("/v1/point", params={"lat": -16.6, "lon": 179.0}).json()["fires_nearby"]
    assert nearby["stale"] is True and nearby["data_read_utc"] == int(EVENING) and nearby["count_24h"] > 0


# ------------------------------------------------------------------------------------------------ an instance without a key


def test_without_a_key_the_routes_say_the_source_is_off(no_key: TestClient) -> None:
    for path in ("/v1/fires?bbox=0,0,10,10", "/v1/fires/grid"):
        response = no_key.get(path)
        assert response.status_code == 404 and response.json() == {"detail": "VIGIE_FIRMS_MAP_KEY is not set"}


def test_without_a_key_the_point_has_no_fire_block_and_nothing_else_changes(no_key: TestClient) -> None:
    response = no_key.get("/v1/point", params={"lat": 48.85, "lon": 2.35})
    assert response.status_code == 200 and response.json()["fires_nearby"] is None


def test_without_a_key_the_health_lists_the_source_as_disabled_and_not_as_a_provider(no_key: TestClient) -> None:
    health = no_key.get("/v1/health").json()
    assert health["disabled_sources"] == [{"provider_id": "nasa_firms", "name": "NASA FIRMS (active fires)", "domain": "fires", "reason": "VIGIE_FIRMS_MAP_KEY is not set"}]
    assert "nasa_firms" not in {p["provider_id"] for p in health["providers"]}
    assert "nasa_firms" not in {p["provider_id"] for p in no_key.get("/v1/providers").json()}
    assert health["providers_summary"]["total"] == len(health["providers"])


def test_with_a_key_the_provider_is_listed_with_its_quota_and_nothing_is_disabled(fires: Fires) -> None:
    health = fires.client.get("/v1/health").json()
    assert health["disabled_sources"] == []
    provider = next(p for p in fires.client.get("/v1/providers").json() if p["provider_id"] == "nasa_firms")
    assert provider["healthy"] is True and provider["calls_24h"] == 4 and provider["weight_24h"] == 144 and provider["last_error"] is None
    assert provider["quota"]["group"] == "nasa-firms" and provider["quota"]["limits"] == [{"window_s": 600, "limit": 5000.0, "budget": 4500.0, "used": 144.0}]


def test_a_service_built_from_the_environment_reads_the_key_from_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VIGIE_FIRMS_MAP_KEY", raising=False)
    off = make_service(tmp_path / "off.db")
    assert off.firms is None and off.fires.enabled is False and off.disabled_sources()[0]["reason"] == "VIGIE_FIRMS_MAP_KEY is not set"
    assert "nasa_firms" not in {c.provider_id for c in off.get_all_collectors()}

    monkeypatch.setenv("VIGIE_FIRMS_MAP_KEY", KEY)
    monkeypatch.setenv("VIGIE_FIRMS_SOURCES", N21)
    monkeypatch.setenv("VIGIE_FIRMS_REFRESH_MINUTES", "60")
    on = make_service(tmp_path / "on.db")
    assert isinstance(on.firms, FIRMSCollector) and on.fires.enabled and on.disabled_sources() == []
    assert on.firms.sources == (N21,) and on.firms.ttl_seconds == 3600 and on.firms in on.get_all_collectors()

    monkeypatch.setenv("VIGIE_FIRMS_MAP_KEY", "not a key")
    bad = make_service(tmp_path / "bad.db")
    assert bad.firms is None and "not a FIRMS key" in bad.disabled_sources()[0]["reason"] and "not a key" not in bad.disabled_sources()[0]["reason"]


def test_the_routes_are_in_the_openapi_document(world: World) -> None:
    document = world.client.get("/openapi.json").json()
    assert {"/v1/fires", "/v1/fires/grid"} <= set(document["paths"])
    fires_route = document["paths"]["/v1/fires"]["get"]
    assert fires_route["tags"] == ["Fires"] and {"200", "404", "422", "503"} <= set(fires_route["responses"])
    assert "fire_radius_km" in {p["name"] for p in document["paths"]["/v1/point"]["get"]["parameters"]}
    assert "fires_nearby" in document["components"]["schemas"]["PointResponse"]["properties"]
    assert isinstance(world.aggregator, AggregatorService)


# ------------------------------------------------------------------------------------------------ the key never leaves


@pytest.fixture()
def failing_instance(sim_clock: SimClock, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """The application with a FIRMS collector whose every read fails with an error that holds the URL."""
    sim_clock.EPOCH = EVENING

    def answer(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"All connection attempts failed for {request.url}", request=request)

    collector = FIRMSCollector(KEY, http_client=Provider(answer=answer, latency_ticks=0).client())
    monkeypatch.setattr(aggregator, "firms", collector)
    monkeypatch.setattr(aggregator, "fires", FireService(collector, None))
    yield TestClient(app)


def test_no_route_gives_the_key_when_firms_cannot_be_read(failing_instance: TestClient) -> None:
    bodies = []
    for path in (
        "/v1/fires?bbox=-10,35,5,45",
        "/v1/fires/grid",
        "/v1/providers",
        "/v1/health",
        "/v1/point?lat=40&lon=-3",
    ):
        response = failing_instance.get(path)
        bodies.append(response.text)
        assert KEY not in response.text and KEY not in str(response.headers), path
    assert failing_instance.get("/v1/fires?bbox=-10,35,5,45").status_code == 503
    providers = {p["provider_id"]: p for p in failing_instance.get("/v1/providers").json()}
    assert providers["nasa_firms"]["last_error"] is not None and "REDACTED" in providers["nasa_firms"]["last_error"]
    assert not any(KEY in body for body in bodies)


def test_the_openapi_document_and_the_dashboard_never_hold_a_key(failing_instance: TestClient) -> None:
    for path in ("/openapi.json", "/dashboard", "/v1/scheduler/status"):
        assert KEY not in failing_instance.get(path).text
