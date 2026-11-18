"""The radar mosaic of Météo-France over France, relayed from a vigie-meteo gateway: the list of images, the tiles, the routes, the point.

The gateway's list of images is the answer read on 2026-11-18 (``tests/fixtures/meteofrance_radar``); a tile is any PNG, the gateway's
own drawing is tested in its repository. No test touches the network.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Optional

import httpx
import pytest

from app.attribution import credit_text
from app.collectors import meteofrance_radar
from app.collectors.meteofrance_radar import (
    PNG_SIGNATURE,
    TILE_PATH,
    TRANSPARENT_PNG,
    MeteoFranceRadarCollector,
    parse_frames,
    tile_touches,
)
from app.collectors.weather import RainViewerCollector
from tests.conftest import World
from tests.helpers import Provider, SimClock, make_service

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "meteofrance_radar" / "frames-2026-11-18.json"
FIRST, LAST = 1794990900, 1794991200  # the two images of the fixture (08:35 and 08:40 UTC)
FAKE_PNG = PNG_SIGNATURE + b"tile"
PARIS_TILE = (8, 129, 88)  # z, x, y of Paris
VANNES = (47.66, -2.76)
OSLO = (59.91, 10.75)


def frames_payload() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def gateway(tile_status: int = 200, payload: Optional[dict[str, Any]] = None, frames_status: int = 200) -> Provider:
    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/radar/frames":
            return httpx.Response(frames_status, json=payload if payload is not None else frames_payload())
        assert request.url.path.startswith("/v1/radar/tiles/")
        return httpx.Response(tile_status, content=FAKE_PNG, headers={"content-type": "image/png"})

    return Provider(answer=answer)


def collector(provider: Provider) -> MeteoFranceRadarCollector:
    return MeteoFranceRadarCollector("http://gateway.test", http_client=provider.client())


# ------------------------------------------------------------------------------------------------ list of images


def test_the_recorded_answer_is_read() -> None:
    frames = parse_frames(frames_payload())
    assert frames.epochs == (FIRST, LAST)
    assert frames.bounds == (-9.97, 37.45, 17.57, 53.67) and (frames.min_zoom, frames.max_zoom) == (3, 10)
    assert [c.min_mm_h for c in frames.legend] == [0.1, 0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0]
    assert frames.legend[0].color == "#a6cee3"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.update(source_model="open-meteo"),
        lambda p: p.update(frames=[]),
        lambda p: p.update(frames=[{"epoch": "x"}]),
        lambda p: p.update(frames=[{"time": "x"}]),
        lambda p: p.update(bounds=[1, 2, 3]),
        lambda p: p.update(bounds=[10, 40, -10, 50]),
        lambda p: p.update(min_zoom=11, max_zoom=3),
        lambda p: p.update(min_zoom="3"),
        lambda p: p.pop("min_zoom"),
        lambda p: p.pop("max_zoom"),
        lambda p: p.update(max_zoom=None),
        lambda p: p.update(min_zoom=True),
        lambda p: p.update(legend=[{"min_mm_h": 1, "color": "red", "alpha": 1}]),
        lambda p: p.update(legend=["x"]),
    ],
)
def test_an_answer_that_is_not_the_radar_of_meteo_france_is_refused(mutate: Any) -> None:
    payload = frames_payload()
    mutate(payload)
    with pytest.raises(ValueError):
        parse_frames(payload)
    for bad in (None, [], "x"):
        with pytest.raises(ValueError):
            parse_frames(bad)


def test_a_missing_legend_is_not_an_error() -> None:
    payload = frames_payload()
    del payload["legend"]
    assert parse_frames(payload).legend == ()


def test_a_tile_far_from_the_radar_is_known_to_be_transparent() -> None:
    frames = parse_frames(frames_payload())
    assert tile_touches(frames, *PARIS_TILE)
    assert tile_touches(frames, 3, 4, 2)  # the world tile where France is
    assert not tile_touches(frames, 8, 0, 0)  # the Pacific
    assert not tile_touches(frames, 6, 10, 20)  # Southern Africa
    assert not tile_touches(frames, 8, 128, 20)  # far north of the radars' area


async def test_the_list_is_read_once_in_two_minutes_and_a_failing_gateway_gives_none(sim_clock: SimClock) -> None:
    provider = gateway()
    radar = collector(provider)
    first, second = await asyncio.gather(radar.frames(), radar.frames())
    assert first is not None and first == second and provider.count == 1
    assert (await collector(gateway(frames_status=503)).frames()) is None
    assert (await collector(gateway(payload={"source_model": "x"})).frames()) is None


# ------------------------------------------------------------------------------------------------ tiles


async def test_a_tile_is_asked_once_then_served_from_memory(sim_clock: SimClock) -> None:
    provider = gateway()
    radar = collector(provider)
    assert await radar.tile(LAST, *PARIS_TILE) == FAKE_PNG
    assert await radar.tile(LAST, *PARIS_TILE) == FAKE_PNG
    assert provider.count == 1 and provider.requests[0].url.path == f"/v1/radar/tiles/{LAST}/8/129/88.png"


async def test_a_crowd_asking_for_the_same_new_tile_makes_one_request(sim_clock: SimClock) -> None:
    provider = gateway()
    radar = collector(provider)
    results = await asyncio.gather(*(radar.tile(LAST, *PARIS_TILE) for _ in range(20)))
    assert set(results) == {FAKE_PNG} and provider.count == 1


async def test_the_memory_of_tiles_is_bounded(sim_clock: SimClock, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(meteofrance_radar, "TILE_CACHE_ENTRIES", 2)
    provider = gateway()
    radar = collector(provider)
    for x in (1, 2, 3):
        await radar.tile(LAST, 8, x, 88)
    await radar.tile(LAST, 8, 1, 88)  # evicted: asked again
    assert provider.count == 4 and len(radar._tiles) == 2


async def test_an_unknown_image_is_none_and_a_broken_answer_raises(sim_clock: SimClock) -> None:
    assert await collector(gateway(tile_status=404)).tile(LAST, *PARIS_TILE) is None
    with pytest.raises(httpx.HTTPStatusError):
        await collector(gateway(tile_status=500)).tile(LAST, *PARIS_TILE)
    html = Provider(answer=lambda r: httpx.Response(200, content=b"<html>not a tile</html>"))
    with pytest.raises(ValueError):
        await collector(html).tile(LAST, *PARIS_TILE)
    huge = Provider(answer=lambda r: httpx.Response(200, content=PNG_SIGNATURE + b"x" * 300_000))
    with pytest.raises(ValueError):
        await collector(huge).tile(LAST, *PARIS_TILE)


def test_the_transparent_tile_is_a_png() -> None:
    assert TRANSPARENT_PNG.startswith(PNG_SIGNATURE) and TRANSPARENT_PNG.endswith(b"IEND\xaeB`\x82")


# ------------------------------------------------------------------------------------------------ routes


def test_the_list_route(world: World, sim_clock: SimClock) -> None:
    world.aggregator.meteofrance_radar = collector(gateway())
    r = world.client.get("/v1/radar/france")
    assert r.status_code == 200 and r.headers["Cache-Control"] == "public, max-age=60"
    body = r.json()
    assert body["status"] == "ok" and body["latest_utc"] == LAST and [f["epoch"] for f in body["frames"]] == [FIRST, LAST]
    assert body["frames"][1]["time"] == "2026-11-18T08:40:00Z"
    assert body["tile_url_template"] == TILE_PATH == "/v1/radar/france/tiles/{epoch}/{z}/{x}/{y}.png"
    assert body["bounds"] == [-9.97, 37.45, 17.57, 53.67] and (body["min_zoom"], body["max_zoom"]) == (3, 10)
    assert len(body["legend"]) == 9 and body["attribution"] == credit_text("meteofrance_radar")
    assert "Météo-France" in body["attribution"] and "Licence Ouverte 2.0" in body["licence"]

    world.aggregator.meteofrance_radar = collector(gateway(frames_status=503))
    assert world.client.get("/v1/radar/france").json()["status"] == "unavailable"
    world.aggregator.meteofrance_radar = None
    assert world.client.get("/v1/radar/france").json()["status"] == "not_configured"


def test_the_tile_route(world: World, sim_clock: SimClock) -> None:
    provider = gateway()
    world.aggregator.meteofrance_radar = collector(provider)
    z, x, y = PARIS_TILE
    r = world.client.get(f"/v1/radar/france/tiles/{LAST}/{z}/{x}/{y}.png")
    assert r.status_code == 200 and r.content == FAKE_PNG and r.headers["content-type"] == "image/png"
    assert r.headers["Cache-Control"] == "public, max-age=86400, immutable"

    def tile_requests() -> list[httpx.Request]:
        return [req for req in provider.requests if req.url.path.startswith("/v1/radar/tiles/")]

    assert len(tile_requests()) == 1

    # an image the gateway does not list, a zoom out of its range, a tile out of the map: refused without asking the gateway for a tile
    for path in (
        f"/v1/radar/france/tiles/{LAST + 300}/{z}/{x}/{y}.png",
        f"/v1/radar/france/tiles/{LAST}/11/0/0.png",
        f"/v1/radar/france/tiles/{LAST}/2/0/0.png",
        f"/v1/radar/france/tiles/{LAST}/8/256/0.png",
        f"/v1/radar/france/tiles/{LAST}/8/0/256.png",
    ):
        assert world.client.get(path).status_code == 404, path
    assert world.client.get(f"/v1/radar/france/tiles/0/{z}/{x}/{y}.png").status_code == 422
    assert world.client.get(f"/v1/radar/france/tiles/{LAST}/{z}/-1/{y}.png").status_code == 422
    assert world.client.get(f"/v1/radar/france/tiles/{LAST}/{z}/{x}/{y}.jpg").status_code == 404
    assert len(tile_requests()) == 1

    far = world.client.get(f"/v1/radar/france/tiles/{LAST}/8/0/0.png")  # the Pacific: transparent, no gateway request
    assert far.status_code == 200 and far.content == TRANSPARENT_PNG and far.headers["Cache-Control"].endswith("immutable")
    assert len(tile_requests()) == 1


def test_the_tile_route_when_the_gateway_fails_or_is_absent(world: World, sim_clock: SimClock) -> None:
    z, x, y = PARIS_TILE
    world.aggregator.meteofrance_radar = None
    assert world.client.get(f"/v1/radar/france/tiles/{LAST}/{z}/{x}/{y}.png").status_code == 404
    world.aggregator.meteofrance_radar = collector(gateway(tile_status=502))
    assert world.client.get(f"/v1/radar/france/tiles/{LAST}/{z}/{x}/{y}.png").status_code == 503
    world.aggregator.meteofrance_radar = collector(gateway(frames_status=503))
    assert world.client.get(f"/v1/radar/france/tiles/{LAST}/{z}/{x}/{y}.png").status_code == 503
    world.aggregator.meteofrance_radar = collector(gateway(tile_status=404))
    assert world.client.get(f"/v1/radar/france/tiles/{LAST}/{z}/{x}/{y}.png").status_code == 404


# ------------------------------------------------------------------------------------------------ the point and the sources


def _service(tmp_path: Path, radar: Optional[MeteoFranceRadarCollector], rainviewer: Optional[RainViewerCollector] = None) -> Any:
    extra: dict[str, Any] = {}
    if radar is not None:
        extra["meteofrance_radar"] = radar
    if rainviewer is not None:
        extra["rainviewer"] = rainviewer
    return make_service(tmp_path / "radar.db", **extra)


async def test_a_point_in_france_points_to_the_newest_image(sim_clock: SimClock, tmp_path: Path) -> None:
    service = _service(tmp_path, collector(gateway()))
    point = await service.build_point_response(*VANNES)
    rain = point.rain_nowcast
    assert rain.radar_timestamp_utc == LAST
    assert rain.radar_tile_url_template == f"/v1/radar/france/tiles/{LAST}/{{z}}/{{x}}/{{y}}.png"
    assert rain.attribution is not None and credit_text("meteofrance_radar") in rain.attribution


async def test_a_point_outside_france_never_asks_the_radar(sim_clock: SimClock, tmp_path: Path) -> None:
    provider = gateway()
    service = _service(tmp_path, collector(provider))
    point = await service.build_point_response(*OSLO)
    assert point.rain_nowcast.radar_tile_url_template is None and point.rain_nowcast.radar_timestamp_utc is None
    assert provider.count == 0


async def test_a_radar_gateway_that_fails_leaves_the_radar_fields_unknown(sim_clock: SimClock, tmp_path: Path) -> None:
    service = _service(tmp_path, collector(gateway(frames_status=503)))
    point = await service.build_point_response(*VANNES)
    assert point.rain_nowcast.radar_tile_url_template is None and point.rain_nowcast.radar_timestamp_utc is None


async def test_in_france_the_radar_of_meteo_france_replaces_rainviewers(sim_clock: SimClock, tmp_path: Path) -> None:
    rainviewer = Provider(
        {
            "host": "https://tilecache.rainviewer.com",
            "radar": {"past": [{"time": 1792334400, "path": "/v2/radar/1792334400"}]},
        }
    )
    service = _service(tmp_path, collector(gateway()), RainViewerCollector(http_client=rainviewer.client()))
    france = await service.build_point_response(*VANNES)
    assert france.rain_nowcast.radar_timestamp_utc == LAST and "rainviewer" not in (france.rain_nowcast.radar_tile_url_template or "")
    assert france.rain_nowcast.attribution is not None and "RainViewer" not in france.rain_nowcast.attribution
    abroad = await service.build_point_response(*OSLO)
    assert abroad.rain_nowcast.radar_timestamp_utc == 1792334400
    assert abroad.rain_nowcast.attribution is not None and "RainViewer" in abroad.rain_nowcast.attribution


def test_the_source_is_listed_as_off_without_a_gateway(tmp_path: Path) -> None:
    service = _service(tmp_path, None)
    off = {item["provider_id"]: item["reason"] for item in service.disabled_sources()}
    assert "VIGIE_METEO_GATEWAY_URL" in off["meteofrance_radar"]
    assert "meteofrance_radar" not in {c.provider_id for c in service.get_all_collectors()}

    on = _service(tmp_path, collector(gateway()))
    assert "meteofrance_radar" in {c.provider_id for c in on.get_all_collectors()}
    assert "meteofrance_radar" not in {item["provider_id"] for item in on.disabled_sources()}
