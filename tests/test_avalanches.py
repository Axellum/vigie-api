"""Offline tests for SLF and Avalanche.report Euregio CAAML v6 avalanche collectors and endpoints."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main as app_main
from app.collectors.avalanches import (
    EUREGIO_CAAML_URL,
    SLF_CAAML_URL,
    EuregioAvalancheCollector,
    SLFAvalancheCollector,
    get_euregio_index,
    is_bulletin_active,
    parse_euregio_json,
    parse_slf_geojson,
)
from app.models.schemas import SeverityLevel
from tests.conftest import World
from tests.helpers import SimClock, make_service

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "avalanches"

SLF_VALID_NOW = datetime(2026, 11, 15, 18, 30, 0, tzinfo=timezone.utc)
EUREGIO_WINTER_NOW = datetime(2026, 3, 15, 10, 0, 0, tzinfo=timezone.utc)
EUREGIO_OFFSEASON_NOW = datetime(2026, 11, 15, 18, 30, 0, tzinfo=timezone.utc)

# Representative coordinates
ZERMATT = (46.0207, 7.7491)       # CH active feature in SLF fixture
VADUZ = (47.1410, 9.5209)         # LI (CH-3311) in SLF fixture
ZURICH = (47.3769, 8.5417)        # CH outside alpine unscheduled polygons on 2026-11-15
INNSBRUCK = (47.2692, 11.4041)    # AT-07-14 / AT-07-14-04
BOLZANO = (46.4983, 11.3548)      # IT-32-BZ-08
TRENTO = (46.0748, 11.1217)       # IT-32-TN-03
PARIS = (48.8566, 2.3522)         # Outside avalanche coverage
LYON = (45.7640, 4.8357)          # FR outside every verified coverage (Chamonix is now a Météo-France BRA massif)
VIENNA = (48.2082, 16.3738)       # AT outside Euregio Tyrol


def _load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_parse_slf_geojson_real_fixture() -> None:
    payload = _load_fixture("slf_caaml_en.geojson")
    feed = parse_slf_geojson(payload, SLF_VALID_NOW)
    assert len(feed.features) == 5

    zermatt_matches = feed.match_point(*ZERMATT)
    assert zermatt_matches, "Zermatt should match an active SLF feature"
    b = zermatt_matches[0]
    assert b.source == "slf_avalanche"
    assert b.unscheduled is True
    assert b.is_expired is False
    assert b.max_danger_level == 2
    assert b.max_danger_label == "moderate"
    assert b.danger_ratings[0].subdivision == "neutral"
    assert any(p.problem_type == "wind_slab" for p in b.avalanche_problems)
    # HTML tags stripped cleanly while preserving wording
    assert b.snowpack_comment and "<h1>" not in b.snowpack_comment and "Snow cover" in b.snowpack_comment
    assert b.tendency and b.tendency[0].comment and "<p>" not in b.tendency[0].comment
    assert "www.slf.ch" in b.attribution

    vaduz_matches = feed.match_point(*VADUZ)
    assert vaduz_matches and any(r.region_id == "CH-3311" for r in vaduz_matches[0].regions)

    zurich_matches = feed.match_point(*ZURICH)
    assert zurich_matches == []


def test_euregio_index_and_winter_caaml_parsing() -> None:
    idx = get_euregio_index()
    assert [r.region_id for r in idx.matches(*INNSBRUCK)] == ["AT-07-14-04", "AT-07-14"]
    assert any(r.region_id.startswith("IT-32-BZ") for r in idx.matches(*BOLZANO))
    assert any(r.region_id.startswith("IT-32-TN") for r in idx.matches(*TRENTO))
    assert idx.matches(*ZERMATT) == []
    assert idx.matches(*VIENNA) == []

    winter_feed = parse_euregio_json(_load_fixture("euregio_winter_caaml_en.json"), EUREGIO_WINTER_NOW, idx)
    assert len(winter_feed.bulletins) == 11
    innsbruck_ids = [r.region_id for r in idx.matches(*INNSBRUCK)]
    matched = winter_feed.match_regions(innsbruck_ids)
    assert len(matched) == 1
    b = matched[0]
    assert b.source == "euregio_avalanche"
    assert b.is_expired is False
    assert b.max_danger_level is not None and b.max_danger_level >= 1
    assert is_bulletin_active(b, EUREGIO_WINTER_NOW)

    # Same latest snapshot queried in off-season (Nov 15 vs May 3 expiry) is marked expired
    offseason_feed = parse_euregio_json(_load_fixture("euregio_caaml_en.json"), EUREGIO_OFFSEASON_NOW, idx)
    assert offseason_feed.bulletins
    assert all(item.is_expired for item in offseason_feed.bulletins)


@pytest.mark.asyncio
async def test_coverage_gating_makes_zero_upstream_calls_outside_territory(tmp_path: Path) -> None:
    calls: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url)
        if request.url.host == "aws.slf.ch":
            return httpx.Response(200, content=(FIXTURES / "slf_caaml_en.geojson").read_bytes())
        if request.url.host == "static.avalanche.report":
            return httpx.Response(200, content=(FIXTURES / "euregio_winter_caaml_en.json").read_bytes())
        return httpx.Response(404)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    slf = SLFAvalancheCollector(http_client=http)
    eur = EuregioAvalancheCollector(http_client=http)
    service = make_service(tmp_path / "cov.db", slf_avalanche=slf, euregio_avalanche=eur)

    for lat, lon in (PARIS, LYON, VIENNA):
        res = await service.get_avalanches(lat=lat, lon=lon)
        assert res.status == "outside_coverage"
        assert res.covered_by is None
        assert res.bulletins == []
        assert await service.get_avalanche_safe(lat, lon) is None

    assert calls == []

    # Asking Zermatt touches SLF once, not Euregio
    zermatt = await service.get_avalanches(lat=ZERMATT[0], lon=ZERMATT[1])
    assert zermatt.status in {"active", "confirmed_none"}
    assert [url.host for url in calls] == ["aws.slf.ch"]

    # Asking Innsbruck touches Euregio once, not SLF again
    innsbruck = await service.get_avalanches(lat=INNSBRUCK[0], lon=INNSBRUCK[1])
    assert innsbruck.covered_by == "euregio_avalanche"
    assert [url.host for url in calls] == ["aws.slf.ch", "static.avalanche.report"]
    await http.aclose()


@pytest.mark.asyncio
async def test_etag_304_coalescing_and_stale_fallback(sim_clock: SimClock) -> None:
    sim_clock.now = SLF_VALID_NOW.timestamp() - SimClock.EPOCH
    slf_bytes = (FIXTURES / "slf_caaml_en.geojson").read_bytes()
    etag = '"slf-test-etag"'
    mode = "ok"
    requests_seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == SLF_CAAML_URL
        requests_seen.append(request)
        if mode == "error":
            return httpx.Response(502, text="bad gateway")
        if request.headers.get("If-None-Match") == etag:
            return httpx.Response(304, headers={"ETag": etag})
        return httpx.Response(200, content=slf_bytes, headers={"ETag": etag})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    collector = SLFAvalancheCollector(http_client=client)

    # Concurrent cold fetch coalesces into a single HTTP request
    f1, f2 = await asyncio.gather(collector.fetch_feed(), collector.fetch_feed())
    assert len(requests_seen) == 1
    assert f1.stale is False and f2.stale is False

    # Within TTL (30 min): zero additional HTTP calls
    sim_clock.advance(600)
    await collector.fetch_feed()
    assert len(requests_seen) == 1

    # Past TTL (1800s): sends If-None-Match and honours 304
    sim_clock.advance(1300)
    f3 = await collector.fetch_feed()
    assert len(requests_seen) == 2
    assert requests_seen[1].headers.get("If-None-Match") == etag
    assert f3.stale is False
    assert len(f3.value.features) == 5

    # Next expiry on 502 falls back to stale cache without raising
    sim_clock.advance(1900)
    mode = "error"
    f4 = await collector.fetch_feed(allow_stale_on_error=True)
    assert f4.stale is True
    assert len(f4.value.features) == 5
    await client.aclose()


@pytest.mark.asyncio
async def test_euregio_offseason_and_include_expired(tmp_path: Path, sim_clock: SimClock) -> None:
    sim_clock.now = EUREGIO_OFFSEASON_NOW.timestamp() - SimClock.EPOCH
    eur_bytes = (FIXTURES / "euregio_caaml_en.json").read_bytes()
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, content=eur_bytes)
            if str(r.url) == EUREGIO_CAAML_URL
            else httpx.Response(404)
        )
    )
    eur = EuregioAvalancheCollector(http_client=client)
    slf = SLFAvalancheCollector(http_client=client)
    service = make_service(tmp_path / "offseason.db", slf_avalanche=slf, euregio_avalanche=eur)

    res = await service.get_avalanches(lat=INNSBRUCK[0], lon=INNSBRUCK[1])
    assert res.status == "confirmed_none"
    assert res.max_danger_level is None
    assert res.bulletins == []
    assert "expired" in res.note.lower()

    res_exp = await service.get_avalanches(lat=INNSBRUCK[0], lon=INNSBRUCK[1], include_expired=True)
    assert res_exp.status == "confirmed_none"
    assert res_exp.max_danger_level is None  # Expired bulletins never promote a current danger level
    assert len(res_exp.bulletins) == 1
    assert res_exp.bulletins[0].is_expired is True
    await client.aclose()


def test_http_avalanches_route_and_point_non_escalation(
    world: World, sim_clock: SimClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.collectors.base import Fetched

    # Play SLF active fixture at SLF_VALID_NOW
    sim_clock.now = SLF_VALID_NOW.timestamp() - SimClock.EPOCH
    slf_feed = parse_slf_geojson(_load_fixture("slf_caaml_en.geojson"), SLF_VALID_NOW)
    winter_feed = parse_euregio_json(
        _load_fixture("euregio_winter_caaml_en.json"),
        EUREGIO_WINTER_NOW,
        world.aggregator.euregio_avalanche.region_index,
    )
    monkeypatch.setattr(world.aggregator.slf_avalanche, "fetch_feed", AsyncMock(return_value=Fetched(slf_feed)))
    monkeypatch.setattr(world.aggregator.euregio_avalanche, "fetch_feed", AsyncMock(return_value=Fetched(winter_feed)))

    # Outside coverage -> 200 outside_coverage
    r_out = world.client.get(f"/v1/avalanches?lat={PARIS[0]}&lon={PARIS[1]}")
    assert r_out.status_code == 200
    assert r_out.json()["status"] == "outside_coverage"

    # Validation 422 on invalid coordinates
    assert world.client.get("/v1/avalanches?lat=120&lon=7").status_code == 422

    # Active SLF (Zermatt + Vaduz LI)
    r_ch = world.client.get(f"/v1/avalanches?lat={ZERMATT[0]}&lon={ZERMATT[1]}")
    assert r_ch.status_code == 200
    d_ch = r_ch.json()
    assert d_ch["status"] == "active"
    assert d_ch["covered_by"] == "slf_avalanche"
    assert d_ch["max_danger_level"] == 2
    assert "www.slf.ch" in d_ch["attribution"]

    r_li = world.client.get(f"/v1/avalanches?lat={VADUZ[0]}&lon={VADUZ[1]}")
    assert r_li.status_code == 200
    assert r_li.json()["region_id"] == "CH-3311"
    assert r_li.json()["region_name"] == "Liechtenstein"

    # Active SLF country but outside polygons (Zurich)
    r_zh = world.client.get(f"/v1/avalanches?lat={ZURICH[0]}&lon={ZURICH[1]}")
    assert r_zh.status_code == 200
    assert r_zh.json()["status"] == "confirmed_none"
    assert r_zh.json()["covered_by"] == "slf_avalanche"

    # Switch simulated clock to winter for Euregio active bulletin on Innsbruck
    sim_clock.now = EUREGIO_WINTER_NOW.timestamp() - SimClock.EPOCH
    r_at = world.client.get(f"/v1/avalanches?lat={INNSBRUCK[0]}&lon={INNSBRUCK[1]}")
    assert r_at.status_code == 200
    assert r_at.json()["status"] == "active"
    assert r_at.json()["covered_by"] == "euregio_avalanche"
    assert r_at.json()["max_danger_level"] == 2
    assert "avalanche.report" in r_at.json()["attribution"]

    # PointResponse embeds compact avalanche block without raising local_alert_level
    pt_at = world.client.get(f"/v1/point?lat={INNSBRUCK[0]}&lon={INNSBRUCK[1]}")
    assert pt_at.status_code == 200
    pt_json = pt_at.json()
    assert pt_json["local_alert_level"] == SeverityLevel.GREEN.value
    assert pt_json["avalanche"]["status"] == "active"
    assert pt_json["avalanche"]["max_danger_level"] == 2
    assert pt_json["avalanche"]["covered_by"] == "euregio_avalanche"

    # PointResponse outside avalanche coverage keeps avalanche = null
    pt_paris = world.client.get(f"/v1/point?lat={PARIS[0]}&lon={PARIS[1]}")
    assert pt_paris.status_code == 200
    assert pt_paris.json()["avalanche"] is None

    # Upstream failure without cache -> 503 on GET /v1/avalanches
    monkeypatch.setattr(app_main.aggregator.slf_avalanche, "fetch_feed", AsyncMock(side_effect=RuntimeError("boom")))
    r_503 = world.client.get(f"/v1/avalanches?lat={ZERMATT[0]}&lon={ZERMATT[1]}")
    assert r_503.status_code == 503
    assert isinstance(TestClient(app_main.app), TestClient)
