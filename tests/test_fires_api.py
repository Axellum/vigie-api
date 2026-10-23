"""``GET /v1/fires``, ``GET /v1/fires/grid``, the ``fires_nearby`` block of ``GET /v1/point`` and the health of the source.

The detections are the recorded ones (``tests/fixtures/firms``, the static files of FIRMS) read through a simulated FIRMS; every figure is compared
with an independent computation over the CSV rows (``tests/firms_samples.py``). An instance that turned the source off says so.

The application's own collectors are shared by every test: nothing here leaves one of them failing (``_shared_collectors_untouched``).
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
from app.main import aggregator
from app.models.fires import FiresCollection, FiresGrid
from app.models.schemas import PointResponse
from app.services.aggregator import AggregatorService
from app.services.fires import FireService
from tests.conftest import World
from tests.firms_samples import (
    KEY,
    N20,
    N21,
    NOW,
    PUBLISHED,
    FirmsServer,
    acq_of,
    at,
    oracle_grid,
    oracle_ids,
    oracle_near,
    rows,
    static_rows,
)
from tests.helpers import Provider, SimClock, make_service

SINCE_24H = int(NOW - 24 * 3600) // 300 * 300  # the start of the default window at the time of the tests
TURNED_OFF = "NASA FIRMS is turned off on this instance (VIGIE_ENABLE_FIRMS=0)"
WORLD_BOX = "-180,-90,180,90"


@pytest.fixture(autouse=True)
def _shared_collectors_untouched() -> Iterator[None]:
    """The collectors of the application are shared by every test of the suite: put back what a test here may have changed in them.

    A test that reaches a route with a collector that is not mocked (the network is refused in the tests) leaves it with failures and, after
    three, an open circuit; the tests that come after would then find a source that "did not answer" for a reason that is not theirs.
    """
    saved = [
        (collector, collector.consecutive_failures, collector.circuit_state, collector.opened_at_monotonic, collector.last_error, collector.last_success_utc)
        for collector in aggregator.get_all_collectors()
    ]
    yield
    for collector, failures, circuit, opened_at, last_error, last_success in saved:
        collector.consecutive_failures, collector.circuit_state, collector.opened_at_monotonic = failures, circuit, opened_at
        collector.last_error, collector.last_success_utc = last_error, last_success


@dataclass
class Fires:
    client: TestClient
    server: FirmsServer
    collector: FIRMSCollector
    world: World


@pytest.fixture()
def fires(world: World, sim_clock: SimClock, monkeypatch: pytest.MonkeyPatch) -> Iterator[Fires]:
    """The application reading FIRMS (the files of 24 hours): the recorded detections are already read, so no request starts a refresh."""
    sim_clock.EPOCH = NOW
    server = FirmsServer(latency_ticks=0)
    collector = FIRMSCollector(http_client=server.client(), window_hours=24)
    monkeypatch.setattr(aggregator, "firms", collector)
    monkeypatch.setattr(aggregator, "fires", FireService(collector, None))
    asyncio.run(collector.fetch())
    yield Fires(client=world.client, server=server, collector=collector, world=world)


@pytest.fixture()
def turned_off(world: World, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(aggregator, "firms", None)
    monkeypatch.setattr(aggregator, "fires", FireService(None, TURNED_OFF))
    return world.client


# ------------------------------------------------------------------------------------------------ the hotspots


def test_the_hotspots_of_a_box_are_those_of_the_recording(fires: Fires) -> None:
    response = fires.client.get("/v1/fires", params={"bbox": "-20,-35,50,15", "limit": 5000})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=60"
    body = response.json()
    FiresCollection.model_validate(body)  # the document describes the answer
    expected = oracle_ids("-20,-35,50,15", SINCE_24H)
    assert body["metadata"]["count"] == len(body["features"]) == body["metadata"]["total_matching"] == len(expected)
    assert body["metadata"]["truncated"] is False
    for feature in body["features"]:
        lon, lat = feature["geometry"]["coordinates"]
        assert -20 <= lon <= 50 and -35 <= lat <= 15
        assert feature["id"] == f"{feature['properties']['satellite']}_{lat:.5f}_{lon:.5f}_{feature['properties']['acq_utc']}"
    powers = [f["properties"]["frp_mw"] for f in body["features"]]
    assert powers == sorted(powers, reverse=True)  # the strongest first


def test_a_feature_is_the_detection_of_the_file_with_its_fields(fires: Fires) -> None:
    body = fires.client.get("/v1/fires", params={"bbox": "116.2,-32,116.3,-31.9"}).json()  # near Perth, the last line of the recording of NOAA-21
    feature = next(f for f in body["features"] if f["geometry"]["coordinates"] == [116.26122, -31.95362])
    assert feature["type"] == "Feature" and feature["id"] == "N21_-31.95362_116.26122_" + str(int(at("2026-10-20", "16:20")))
    assert feature["properties"] == {
        "acq_utc": int(at("2026-10-20", "16:20")),
        "satellite": "N21",
        "instrument": None,  # the static files have no such column
        "confidence": "nominal",
        "frp_mw": 1.46,
        "bright_ti4_k": 297.04,
        "bright_ti5_k": 282.08,
        "scan_km": 0.61,
        "track_km": 0.71,
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
    assert metadata["generated_utc"] == int(NOW) and metadata["data_read_utc"] == int(NOW) and metadata["stale"] is False
    assert metadata["since_utc"] == int(NOW - 12 * 3600) // 300 * 300 and metadata["since_utc"] % 300 == 0
    assert metadata["sources"] == [
        {"source": N20, "read_utc": int(NOW), "ok": True, "via": "static", "published_utc": int(PUBLISHED[N20])},
        {"source": N21, "read_utc": int(NOW), "ok": True, "via": "static", "published_utc": int(PUBLISHED[N21])},
    ]
    assert metadata["skipped_rows"] == 0


def test_a_product_that_has_never_been_read_is_listed_with_no_time(world: World, sim_clock: SimClock, monkeypatch: pytest.MonkeyPatch) -> None:
    """The first reading of a real instance (2026-10-20): NOAA-20 timed out, NOAA-21 answered. The answer must not look like a complete one."""
    sim_clock.EPOCH = NOW
    server = FirmsServer(latency_ticks=0)
    server.failing = {N20}
    collector = FIRMSCollector(http_client=server.client(), window_hours=24)
    monkeypatch.setattr(aggregator, "firms", collector)
    monkeypatch.setattr(aggregator, "fires", FireService(collector, None))
    asyncio.run(collector.fetch())
    for path in ("/v1/fires?bbox=-180,-90,180,90", "/v1/fires/grid"):
        metadata = world.client.get(path).json()["metadata"]
        assert metadata["stale"] is True
        assert metadata["sources"] == [
            {"source": N20, "read_utc": None, "ok": False, "via": None, "published_utc": None},
            {"source": N21, "read_utc": int(NOW), "ok": True, "via": "static", "published_utc": int(PUBLISHED[N21])},
        ]


def test_the_hotspots_beyond_the_limit_are_not_sent_and_the_answer_says_so(fires: Fires) -> None:
    body = fires.client.get("/v1/fires", params={"bbox": WORLD_BOX, "limit": 10}).json()
    assert len(body["features"]) == 10 and body["metadata"]["total_matching"] == len(oracle_ids(WORLD_BOX, SINCE_24H)) and body["metadata"]["truncated"] is True
    strongest = sorted((r["frp"] for r in rows() if r["acq"] >= SINCE_24H and not math.isnan(r["frp"])), reverse=True)[:10]
    assert [f["properties"]["frp_mw"] for f in body["features"]] == pytest.approx(strongest, abs=0.01)


def test_the_filters_of_confidence_power_and_window(fires: Fires) -> None:
    def count(**params: Any) -> int:
        return fires.client.get("/v1/fires", params={"bbox": "-180,-90,180,90", "limit": 5000, **params}).json()["metadata"]["total_matching"]

    assert count() == len(oracle_ids(WORLD_BOX, SINCE_24H)) < len(rows())  # the files hold more than the 24 hours of the window
    assert count(min_confidence="nominal") == len(oracle_ids(WORLD_BOX, SINCE_24H, min_conf=2))
    assert count(min_confidence="high") == len(oracle_ids(WORLD_BOX, SINCE_24H, min_conf=3))
    assert count(min_frp=10) == len(oracle_ids(WORLD_BOX, SINCE_24H, min_frp=10.0))
    since = int(NOW - 6 * 3600) // 300 * 300
    assert count(hours=6) == len(oracle_ids(WORLD_BOX, since))
    assert 0 < count(hours=6) < count()


def test_the_window_moves_with_the_clock(fires: Fires, sim_clock: SimClock) -> None:
    sim_clock.advance(20 * 3600)  # 14:12 the next day: only the detections of the last 24 hours remain
    count = fires.client.get("/v1/fires", params={"bbox": "-180,-90,180,90", "limit": 5000}).json()["metadata"]["total_matching"]
    since = int(NOW + 20 * 3600 - 24 * 3600) // 300 * 300
    assert count == len(oracle_ids(WORLD_BOX, since)) < len(oracle_ids(WORLD_BOX, SINCE_24H))


def test_a_box_across_the_antimeridian(fires: Fires) -> None:
    body = fires.client.get("/v1/fires", params={"bbox": "170,-25,-170,-10", "limit": 5000}).json()
    longitudes = [f["geometry"]["coordinates"][0] for f in body["features"]]
    assert len(longitudes) == len(oracle_ids("170,-25,-170,-10", SINCE_24H)) > 0
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
        {"bbox": "0,0,10,10", "hours": 49},  # more than any instance reads
        {"bbox": "0,0,10,10", "hours": 36},  # more than this one reads (the files of 24 hours)
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
    expected = oracle_grid(cell, since=SINCE_24H)
    metadata = body["metadata"]
    assert metadata["cell_deg"] == cell and metadata["total_cells"] == len(expected) == metadata["count"] and metadata["truncated"] is False
    assert metadata["total_detections"] == len(oracle_ids(WORLD_BOX, SINCE_24H)) == sum(count for count, _, _ in expected.values())
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
    assert body["metadata"]["total_detections"] == len(oracle_ids("-20,-35,50,15", SINCE_24H))
    assert all(-21 <= f["geometry"]["coordinates"][0] <= 51 and -36 <= f["geometry"]["coordinates"][1] <= 16 for f in body["features"])


def test_the_grid_keeps_the_busiest_cells_when_there_are_too_many(fires: Fires) -> None:
    body = fires.client.get("/v1/fires/grid", params={"cell_deg": 0.25, "limit": 5}).json()
    counts = [f["properties"]["count"] for f in body["features"]]
    assert len(counts) == 5 and counts == sorted(counts, reverse=True)
    assert body["metadata"]["truncated"] is True and body["metadata"]["total_cells"] > 5


def test_the_grid_has_the_same_filters_as_the_hotspots(fires: Fires) -> None:
    body = fires.client.get("/v1/fires/grid", params={"cell_deg": 1, "min_confidence": "high", "min_frp": 5, "hours": 12}).json()
    since = int(NOW - 12 * 3600) // 300 * 300
    assert body["metadata"]["total_detections"] == len(oracle_ids(WORLD_BOX, since, 3, 5.0))


@pytest.mark.parametrize("params", [{"cell_deg": 0.1}, {"cell_deg": 3}, {"cell_deg": 0}, {"cell_deg": 20}, {"bbox": "0,0,1"}, {"limit": 0}, {"limit": 20001}, {"hours": 100}])
def test_a_grid_request_that_is_not_valid_is_refused(fires: Fires, params: dict[str, Any]) -> None:
    assert fires.client.get("/v1/fires/grid", params=params).status_code == 422


# ------------------------------------------------------------------------------------------------ the summary of /v1/point


def test_the_point_carries_the_summary_of_the_fires_nearby(fires: Fires) -> None:
    response = fires.client.get("/v1/point", params={"lat": -16.6, "lon": 179.0, "fire_radius_km": 300})
    assert response.status_code == 200
    nearby = response.json()["fires_nearby"]
    PointResponse.model_validate(response.json())
    count, nearest, strongest, latest = oracle_near(-16.6, 179.0, 300.0, int(NOW) - 86400)
    assert count > 0
    assert nearby["radius_km"] == 300 and nearby["count_24h"] == count and nearby["latest_utc"] == latest
    assert nearby["nearest_km"] == pytest.approx(nearest, abs=0.06) and nearby["max_frp_mw"] == pytest.approx(strongest, abs=0.01)
    assert nearby["data_read_utc"] == int(NOW) and nearby["stale"] is False and nearby["sources"] == [N20, N21]
    assert nearby["derived"] == ["count_24h", "nearest_km", "max_frp_mw", "latest_utc"]
    assert nearby["attribution"] == CREDITS["nasa_firms"].text and nearby["disclaimer"] == FIRMS_DISCLAIMER


def test_the_default_radius_is_fifty_kilometres(fires: Fires) -> None:
    nearby = fires.client.get("/v1/point", params={"lat": -16.75, "lon": 178.73}).json()["fires_nearby"]
    assert nearby["radius_km"] == 50
    assert nearby["count_24h"] == oracle_near(-16.75, 178.73, 50.0, int(NOW) - 86400)[0] > 5


def test_nothing_nearby_is_zero_with_unknown_distance_and_power(fires: Fires) -> None:
    nearby = fires.client.get("/v1/point", params={"lat": -60.0, "lon": -120.0}).json()["fires_nearby"]
    assert nearby["count_24h"] == 0 and nearby["nearest_km"] is None and nearby["max_frp_mw"] is None and nearby["latest_utc"] is None


def test_the_fires_never_raise_the_alert_level_of_the_point(fires: Fires, monkeypatch: pytest.MonkeyPatch) -> None:
    params = {"lat": -16.6, "lon": 179.0, "radius_km": 600, "fire_radius_km": 500}
    with_fires = fires.client.get("/v1/point", params=params).json()
    assert with_fires["fires_nearby"]["count_24h"] == oracle_near(-16.6, 179.0, 500.0, int(NOW) - 86400)[0] > 20  # a lot of fire around
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
    assert nearby["stale"] is True and nearby["data_read_utc"] == int(NOW) and nearby["count_24h"] > 0


# ------------------------------------------------------------------------------------------------ an instance that turned the source off


def test_when_the_source_is_turned_off_the_routes_say_so(turned_off: TestClient) -> None:
    for path in ("/v1/fires?bbox=0,0,10,10", "/v1/fires/grid"):
        response = turned_off.get(path)
        assert response.status_code == 404 and response.json() == {"detail": TURNED_OFF}


def test_when_the_source_is_turned_off_the_point_has_no_fire_block_and_nothing_else_changes(turned_off: TestClient) -> None:
    response = turned_off.get("/v1/point", params={"lat": 48.85, "lon": 2.35})
    assert response.status_code == 200 and response.json()["fires_nearby"] is None


def test_when_the_source_is_turned_off_the_health_lists_it_as_disabled_and_not_as_a_provider(turned_off: TestClient) -> None:
    health = turned_off.get("/v1/health").json()
    assert health["disabled_sources"] == [{"provider_id": "nasa_firms", "name": "NASA FIRMS (active fires)", "domain": "fires", "reason": TURNED_OFF}]
    assert "nasa_firms" not in {p["provider_id"] for p in health["providers"]}
    assert "nasa_firms" not in {p["provider_id"] for p in turned_off.get("/v1/providers").json()}
    assert health["providers_summary"]["total"] == len(health["providers"])


def test_the_provider_is_listed_with_its_calls_and_nothing_is_disabled(fires: Fires) -> None:
    health = fires.client.get("/v1/health").json()
    assert health["disabled_sources"] == []
    provider = next(p for p in fires.client.get("/v1/providers").json() if p["provider_id"] == "nasa_firms")
    assert provider["healthy"] is True and provider["calls_24h"] == 2 and provider["weight_24h"] == 0 and provider["last_error"] is None  # two files, no transaction
    assert provider["quota"]["group"] == "nasa-firms" and provider["quota"]["limits"] == [{"window_s": 600, "limit": 5000.0, "budget": 4500.0, "used": 0.0}]


def test_a_service_built_from_the_environment_reads_the_files_with_no_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VIGIE_ENABLE_FIRMS", "0")
    off = make_service(tmp_path / "off.db")
    assert off.firms is None and off.fires.enabled is False and "VIGIE_ENABLE_FIRMS=0" in off.disabled_sources()[0]["reason"]
    assert "nasa_firms" not in {c.provider_id for c in off.get_all_collectors()}

    monkeypatch.delenv("VIGIE_ENABLE_FIRMS")  # on by default, and no key needed
    monkeypatch.delenv("VIGIE_FIRMS_MAP_KEY", raising=False)
    on = make_service(tmp_path / "on.db")
    assert isinstance(on.firms, FIRMSCollector) and on.fires.enabled and on.disabled_sources() == [] and on.firms in on.get_all_collectors()
    assert on.firms.sources == (N20, N21) and on.firms.ttl_seconds == 3600 and on.firms.window_hours == 48 and on.firms.secrets == ()

    monkeypatch.setenv("VIGIE_FIRMS_MAP_KEY", KEY)
    monkeypatch.setenv("VIGIE_FIRMS_SOURCES", N21)
    monkeypatch.setenv("VIGIE_FIRMS_WINDOW_HOURS", "24")
    keyed = make_service(tmp_path / "keyed.db")
    assert keyed.firms is not None and keyed.firms.sources == (N21,) and keyed.firms.window_hours == 24 and keyed.firms.secrets == (KEY,)

    monkeypatch.setenv("VIGIE_FIRMS_MAP_KEY", "not a key")
    bad = make_service(tmp_path / "bad.db")  # a key that is not one is not used, and the source is not turned off for it
    assert bad.firms is not None and bad.firms.secrets == () and bad.disabled_sources() == []


def test_more_hours_than_the_files_of_this_instance_are_refused_with_the_reason(fires: Fires) -> None:
    for path in ("/v1/fires", "/v1/fires/grid"):
        response = fires.client.get(path, params={"bbox": "0,0,10,10", "hours": 36})
        assert response.status_code == 422 and response.json() == {"detail": "hours must be at most 24 on this instance (it reads the files of the last 24 hours; VIGIE_FIRMS_WINDOW_HOURS)"}


def test_an_instance_that_reads_the_files_of_48_hours_answers_up_to_48_hours(world: World, sim_clock: SimClock, monkeypatch: pytest.MonkeyPatch) -> None:
    sim_clock.EPOCH = NOW
    collector = FIRMSCollector(http_client=FirmsServer(latency_ticks=0).client(), sources=(N21,), window_hours=48)
    monkeypatch.setattr(aggregator, "firms", collector)
    monkeypatch.setattr(aggregator, "fires", FireService(collector, None))
    asyncio.run(collector.fetch())
    body = world.client.get("/v1/fires", params={"bbox": WORLD_BOX, "hours": 48, "limit": 5000}).json()
    since = int(NOW - 48 * 3600) // 300 * 300
    assert body["metadata"]["window_hours"] == 48 and body["metadata"]["since_utc"] == since
    days = {str(feature["properties"]["acq_utc"] // 86400) for feature in body["features"]}
    expected = [row for row in static_rows(N21, 48) if acq_of(row) >= since]  # the file starts at 00:00 of the 18th: 68 hours back, the window 48
    assert len(days) == 3 and body["metadata"]["total_matching"] == len(expected) < 241


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
def failing_instance(world: World, sim_clock: SimClock, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """The application with a FIRMS collector (and a key) whose every read, of the file and of the area API, fails with an error that holds the URL.

    ``world`` mocks the other providers: without it the routes below reach collectors that are not mocked, which fail (the network is refused) and stay
    failed for the tests that come after this one.
    """
    sim_clock.EPOCH = NOW

    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/data/"):  # the file of FIRMS (no key in its address) is down...
            raise httpx.ConnectError("connection refused", request=request)
        raise httpx.ConnectError(f"All connection attempts failed for {request.url}", request=request)  # ...and so is the area API, whose URL holds the key

    collector = FIRMSCollector(KEY, http_client=Provider(answer=answer, latency_ticks=0).client(), window_hours=24)
    monkeypatch.setattr(aggregator, "firms", collector)
    monkeypatch.setattr(aggregator, "fires", FireService(collector, None))
    yield world.client


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
