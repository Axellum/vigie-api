"""Global observed satellite precipitation (``GET /v1/rain/observed``): NASA GIBS GPM IMERG collector, allowlist, circuit breaker and routes.

Uses the recorded 380-byte GIBS ``DescribeDomains`` XML (``tests/fixtures/nasa_gibs_imerg/``) and synthetic PNG tiles;
nothing here touches the network.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from app.attribution import CREDITS
from app.collectors import nasa_gibs_imerg as imerg
from app.collectors.budget import CallBudget, Limit, Quota
from app.collectors.meteofrance_radar import TRANSPARENT_PNG
from app.commercial import commercial_status
from app.main import aggregator
from app.models.schemas import CircuitState
from app.services.aggregator import AggregatorService
from tests.conftest import World
from tests.helpers import Provider, SimClock

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "nasa_gibs_imerg"
DOMAINS_XML = (FIXTURES / "domains-2026-11-22.xml").read_bytes()


# ============================================================================
# XML parsing
# ============================================================================


def test_parse_recorded_domains_xml_returns_last_12_frames_oldest_first() -> None:
    parsed = imerg.parse_domains_xml(DOMAINS_XML)
    assert len(parsed.frames) == 12
    assert parsed.frames[0].stamp == "202611220700"
    assert parsed.frames[0].time_at == "2026-11-22T07:00:00Z"
    assert parsed.frames[-1].stamp == "202611221230"
    assert parsed.frames[-1].time_at == "2026-11-22T12:30:00Z"
    assert parsed.latest == parsed.frames[-1]
    assert set(parsed.by_stamp) == {f.stamp for f in parsed.frames}
    # Consecutive frames are 30 minutes (1800 s) apart.
    deltas = [b.time_utc - a.time_utc for a, b in zip(parsed.frames, parsed.frames[1:], strict=False)]
    assert deltas == [1800] * 11


def test_parse_domains_xml_handles_bare_date_start_and_multiple_periods() -> None:
    bare_date_xml = (
        b"<Domains xmlns:ows='http://www.opengis.net/ows/1.1'>"
        b"<DimensionDomain><ows:Identifier>time</ows:Identifier>"
        b"<Domain>2026-11-22/2026-11-22T02:00:00Z/PT30M</Domain></DimensionDomain></Domains>"
    )
    parsed = imerg.parse_domains_xml(bare_date_xml)
    assert [f.stamp for f in parsed.frames] == [
        "202611220000",
        "202611220030",
        "202611220100",
        "202611220130",
        "202611220200",
    ]

    multi_period_xml = (
        b"<Domains xmlns:ows='http://www.opengis.net/ows/1.1'>"
        b"<DimensionDomain><ows:Identifier>time</ows:Identifier>"
        b"<Domain>2026-11-22T08:00:00Z/2026-11-22T09:00:00Z/PT30M,2026-11-22T11:30:00Z</Domain>"
        b"</DimensionDomain></Domains>"
    )
    parsed_multi = imerg.parse_domains_xml(multi_period_xml)
    assert [f.stamp for f in parsed_multi.frames] == [
        "202611220800",
        "202611220830",
        "202611220900",
        "202611221130",
    ]


@pytest.mark.parametrize(
    "bad_xml",
    [
        pytest.param(b"", id="empty"),
        pytest.param(b"<Domains><not-closed>", id="malformed"),
        pytest.param(b"<Domains xmlns:ows='http://www.opengis.net/ows/1.1'></Domains>", id="no-domain"),
        pytest.param(
            b"<Domains xmlns:ows='http://www.opengis.net/ows/1.1'>"
            b"<DimensionDomain><ows:Identifier>time</ows:Identifier>"
            b"<Domain>2026-11-22T08:00:00Z/2026-11-22T09:00:00Z/PT0M</Domain></DimensionDomain></Domains>",
            id="zero-step",
        ),
        pytest.param(
            b"<Domains xmlns:ows='http://www.opengis.net/ows/1.1'>"
            b"<DimensionDomain><ows:Identifier>time</ows:Identifier>"
            b"<Domain>2026-11-22T08:00:00Z/2026-11-22T09:00:00Z/P1D</Domain></DimensionDomain></Domains>",
            id="unsupported-step",
        ),
        pytest.param(b"x" * (imerg.MAX_DOMAINS_BYTES + 1), id="oversized"),
    ],
)
def test_parse_domains_xml_rejects_invalid_or_empty_inputs(bad_xml: bytes) -> None:
    with pytest.raises(ValueError):
        imerg.parse_domains_xml(bad_xml)


# ============================================================================
# Environment switch, attribution & commercial status
# ============================================================================


def test_env_switch_defaults_on_and_disables_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(imerg.SWITCH_ENV, raising=False)
    assert imerg.observed_rain_enabled() is True
    for raw in ("1", "true", "yes", "on"):
        monkeypatch.setenv(imerg.SWITCH_ENV, raw)
        assert imerg.observed_rain_enabled() is True
    for raw in ("0", "false", "no", "off", " FALSE "):
        monkeypatch.setenv(imerg.SWITCH_ENV, raw)
        assert imerg.observed_rain_enabled() is False


def test_attribution_and_commercial_status_are_allowed() -> None:
    credit = CREDITS["nasa_gibs_imerg"]
    assert "Global Imagery Browse Services (GIBS)" in credit.text
    assert credit.wording == "provider"
    assert commercial_status("nasa_gibs_imerg") == "allowed"


# ============================================================================
# Collector: caching, WMTS y/x mapping, LRU, coalescing, circuit breaker, budget
# ============================================================================


def _gibs_provider(domains_status: int = 200, tile_status: int = 200, tile_body: bytes = TRANSPARENT_PNG) -> Provider:
    def answer(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith(".xml"):
            if domains_status != 200:
                return httpx.Response(domains_status)
            return httpx.Response(200, content=DOMAINS_XML, headers={"Content-Type": "application/xml"})
        if path.endswith(".png"):
            if tile_status != 200:
                return httpx.Response(tile_status)
            return httpx.Response(200, content=tile_body, headers={"Content-Type": "image/png"})
        return httpx.Response(404)

    return Provider(answer=answer)


@pytest.mark.asyncio
async def test_collector_maps_xyz_to_wmts_zyx_coalesces_and_evicts_lru(
    sim_clock: SimClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(imerg, "TILE_CACHE_ENTRIES", 2)
    provider = _gibs_provider()
    collector = imerg.NasaGibsImergCollector(http_client=provider.client())

    fetched = await collector.frames()
    assert fetched is not None and fetched.stale is False
    frame = fetched.value.latest  # 2026-11-22T12:30:00Z

    # Concurrent callers for the same tile (z=4, x=8, y=5) trigger ONE upstream call
    # and the upstream WMTS URL must place TileRow (y=5) BEFORE TileCol (x=8).
    results = await asyncio.gather(*(collector.tile(frame, z=4, x=8, y=5) for _ in range(10)))
    assert all(body == TRANSPARENT_PNG for body in results)
    tile_requests = [r for r in provider.requests if r.url.path.endswith(".png")]
    assert len(tile_requests) == 1
    assert tile_requests[0].url.path.endswith("/2026-11-22T12:30:00Z/GoogleMapsCompatible_Level6/4/5/8.png")

    # Fetch 2 more distinct tiles -> cache limit 2 evicts (z=4, x=8, y=5).
    await collector.tile(frame, z=4, x=9, y=5)
    await collector.tile(frame, z=4, x=10, y=5)
    assert len(collector._tiles) == 2
    assert (frame.stamp, 4, 8, 5) not in collector._tiles

    # Requesting the evicted tile fetches upstream again.
    await collector.tile(frame, z=4, x=8, y=5)
    assert len([r for r in provider.requests if r.url.path.endswith(".png")]) == 4


@pytest.mark.asyncio
async def test_collector_rejects_corrupt_or_oversized_tile_and_handles_404() -> None:
    bad_provider = _gibs_provider(tile_body=b"<html>not a png</html>")
    collector = imerg.NasaGibsImergCollector(http_client=bad_provider.client())
    fetched = await collector.frames()
    assert fetched is not None
    with pytest.raises(ValueError, match="valid PNG"):
        await collector.tile(fetched.value.latest, z=0, x=0, y=0)

    missing_provider = _gibs_provider(tile_status=404)
    missing_collector = imerg.NasaGibsImergCollector(http_client=missing_provider.client())
    fetched2 = await missing_collector.frames()
    assert fetched2 is not None
    assert await missing_collector.tile(fetched2.value.latest, z=0, x=0, y=0) is None


@pytest.mark.asyncio
async def test_collector_serves_stale_on_error_and_trips_circuit_breaker(sim_clock: SimClock) -> None:
    fail = False

    def answer(request: httpx.Request) -> httpx.Response:
        if fail:
            return httpx.Response(503)
        return httpx.Response(200, content=DOMAINS_XML, headers={"Content-Type": "application/xml"})

    provider = Provider(answer=answer)
    collector = imerg.NasaGibsImergCollector(http_client=provider.client())

    fresh = await collector.frames()
    assert fresh is not None and fresh.stale is False
    assert provider.count == 1

    # Within 600s TTL: served from cache without calling upstream.
    sim_clock.advance(300.0)
    cached = await collector.frames()
    assert cached is not None and cached.stale is False and provider.count == 1

    # After TTL expires and upstream fails: serves stale cached frames.
    fail = True
    sim_clock.advance(400.0)
    stale = await collector.frames()
    assert stale is not None and stale.stale is True
    assert stale.value.latest.stamp == "202611221230"

    # Two more failures open the circuit breaker (threshold=3).
    sim_clock.advance(1.0)
    await collector.frames()
    sim_clock.advance(1.0)
    await collector.frames()
    assert collector.circuit_state == CircuitState.OPEN


@pytest.mark.asyncio
async def test_collector_enforces_call_budget(sim_clock: SimClock) -> None:
    provider = _gibs_provider()
    collector = imerg.NasaGibsImergCollector(http_client=provider.client())
    collector.budget = CallBudget(Quota("test-gibs", (Limit(60, 1),), "https://example.test"), fraction=1.0)

    fetched = await collector.frames()
    assert fetched is not None and provider.count == 1

    # Budget (1 call/min) is now exhausted: a tile fetch must be refused without touching upstream.
    with pytest.raises(Exception, match="budget"):
        await collector.tile(fetched.value.latest, z=0, x=0, y=0)
    assert provider.count == 1


# ============================================================================
# Aggregator wiring & FastAPI routes
# ============================================================================


def test_aggregator_wires_observed_rain_by_default_and_disables_with_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(imerg.SWITCH_ENV, raising=False)
    service_on = AggregatorService(db_path=tmp_path / "on.db")
    assert service_on.nasa_gibs_imerg is not None
    assert service_on.nasa_gibs_imerg in service_on.get_all_collectors()
    assert "nasa_gibs_imerg" not in {item["provider_id"] for item in service_on.disabled_sources()}

    monkeypatch.setenv(imerg.SWITCH_ENV, "0")
    service_off = AggregatorService(db_path=tmp_path / "off.db")
    assert service_off.nasa_gibs_imerg is None
    assert "nasa_gibs_imerg" not in {c.provider_id for c in service_off.get_all_collectors()}
    off = {item["provider_id"]: item["reason"] for item in service_off.disabled_sources()}
    assert "nasa_gibs_imerg" in off and "VIGIE_OBSERVED_RAIN=0" in off["nasa_gibs_imerg"]


def test_routes_return_ok_schema_and_enforce_strict_stamp_allowlist(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = _gibs_provider()
    collector = imerg.NasaGibsImergCollector(http_client=provider.client())
    monkeypatch.setattr(aggregator, "nasa_gibs_imerg", collector)

    res = world.client.get("/v1/rain/observed")
    assert res.status_code == 200
    assert res.headers["cache-control"] == "public, max-age=300"
    body = res.json()
    assert body["status"] == "ok"
    assert body["source_model"] == "nasa-gibs-imerg-30min"
    assert body["projection"] == "epsg3857"
    assert body["bounds"] == [-180.0, -85.051129, 180.0, 85.051129]
    assert body["min_zoom"] == 0 and body["max_zoom"] == 6
    assert body["interval_minutes"] == 30
    assert body["resolution_km"] == 10
    assert body["typical_latency_minutes"] == 300
    assert body["latest_at"] == "2026-11-22T12:30:00Z"
    assert body["stale"] is False
    assert body["tile_url_template"] == "/v1/rain/observed/tiles/{stamp}/{z}/{x}/{y}.png"
    assert len(body["frames"]) == 12
    assert body["frames"][-1]["stamp"] == "202611221230"
    assert len(body["legend"]) == 9 and body["legend"][0]["min_mm_h"] == 0.1
    assert len(body["snow_legend"]) == 9 and body["snow_legend"][0]["min_mm_h"] == 0.1
    assert body["attribution"] == CREDITS["nasa_gibs_imerg"].text
    assert "not a ground weather radar" in body["note"].lower()
    assert "not for safety" in body["note"].lower()

    # Valid allowlisted stamp & tile coordinates -> 200 PNG, immutable cache.
    tile = world.client.get("/v1/rain/observed/tiles/202611221230/2/2/1.png")
    assert tile.status_code == 200
    assert tile.headers["content-type"] == "image/png"
    assert tile.headers["cache-control"] == "public, max-age=86400, immutable"
    assert tile.content == TRANSPARENT_PNG

    tile_calls_before = len([r for r in provider.requests if r.url.path.endswith(".png")])

    # Stamp NOT in active allowlist -> 404 without hitting NASA GIBS!
    assert world.client.get("/v1/rain/observed/tiles/202001010000/0/0/0.png").status_code == 404
    # Malformed stamp -> 404 without hitting NASA GIBS!
    assert world.client.get("/v1/rain/observed/tiles/not-a-stamp/0/0/0.png").status_code == 404
    # Zoom above max_zoom (6) or out-of-range x/y -> 404 without hitting NASA GIBS!
    assert world.client.get("/v1/rain/observed/tiles/202611221230/7/0/0.png").status_code == 404
    assert world.client.get("/v1/rain/observed/tiles/202611221230/2/4/0.png").status_code == 404
    assert world.client.get("/v1/rain/observed/tiles/202611221230/2/0/4.png").status_code == 404

    assert len([r for r in provider.requests if r.url.path.endswith(".png")]) == tile_calls_before


def test_routes_when_disabled_or_unavailable(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    # Disabled (VIGIE_OBSERVED_RAIN=0)
    monkeypatch.setattr(aggregator, "nasa_gibs_imerg", None)
    disabled = world.client.get("/v1/rain/observed")
    assert disabled.status_code == 200
    body = disabled.json()
    assert body["status"] == "disabled"
    assert body["frames"] == []
    assert body["tile_url_template"] is None
    assert body["bounds"] is None
    assert body["legend"] == []
    assert world.client.get("/v1/rain/observed/tiles/202611221230/0/0/0.png").status_code == 404

    # Unavailable (GIBS returns 503 with nothing cached)
    down = imerg.NasaGibsImergCollector(http_client=_gibs_provider(domains_status=503).client())
    monkeypatch.setattr(aggregator, "nasa_gibs_imerg", down)
    unavail = world.client.get("/v1/rain/observed")
    assert unavail.status_code == 200
    assert unavail.json()["status"] == "unavailable"
    assert unavail.json()["frames"] == []
    assert world.client.get("/v1/rain/observed/tiles/202611221230/0/0/0.png").status_code == 503

    # Domains OK, but tile upstream fails with 502 -> 503
    bad_tile = imerg.NasaGibsImergCollector(http_client=_gibs_provider(tile_status=502).client())
    monkeypatch.setattr(aggregator, "nasa_gibs_imerg", bad_tile)
    assert world.client.get("/v1/rain/observed/tiles/202611221230/0/0/0.png").status_code == 503

    # Domains OK, but tile upstream returns 404 -> 404
    missing_tile = imerg.NasaGibsImergCollector(http_client=_gibs_provider(tile_status=404).client())
    monkeypatch.setattr(aggregator, "nasa_gibs_imerg", missing_tile)
    assert world.client.get("/v1/rain/observed/tiles/202611221230/0/0/0.png").status_code == 404
