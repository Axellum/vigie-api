"""The radar mosaic of Météo-France over France, relayed from a vigie-meteo gateway: the list of images, the tiles, the routes, the point.

The gateway's list of images is the answer read on 2026-11-18 (``tests/fixtures/meteofrance_radar``); a tile is any PNG, the gateway's
own drawing is tested in its repository. No test touches the network.
"""

from __future__ import annotations

import asyncio
import json
import math
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


# ------------------------------------------------------------------------------------------------ the overseas areas (ADR 0037)

OVERSEAS_POINTS = {
    "antilles": (14.65, -61.0),  # Martinique
    "reunion": (-21.1, 55.5),  # La Réunion
    "new-caledonia": (-22.27, 166.45),  # Nouméa
}


def overseas_payload(area: str) -> dict[str, Any]:
    """What the gateway answers for an overseas area: the recorded answer of France with the box of the area's grid (read in the
    files of ``DPRadar`` on 2026-11-21) and the area's own tile path."""
    payload = frames_payload()
    payload["area"] = area
    payload["bounds"] = list(meteofrance_radar.AREA_BOXES[area])
    payload["tile_path"] = f"/v1/radar/{area}/tiles/{{epoch}}/{{z}}/{{x}}/{{y}}.png"
    return payload


def area_gateway(tile_status: int = 200) -> Provider:
    """A gateway that has an image for the three overseas areas and for France, and refuses any other address."""

    def answer(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v1/radar/frames":
            return httpx.Response(200, json=frames_payload())
        for area in meteofrance_radar.AREA_BOXES:
            if path == f"/v1/radar/{area}/frames":
                return httpx.Response(200, json=overseas_payload(area))
            if path.startswith(f"/v1/radar/{area}/tiles/"):
                return httpx.Response(tile_status, content=FAKE_PNG, headers={"content-type": "image/png"})
        if path.startswith("/v1/radar/tiles/"):
            return httpx.Response(tile_status, content=FAKE_PNG, headers={"content-type": "image/png"})
        raise AssertionError(f"unexpected request to the gateway: {path}")

    return Provider(answer=answer)


def test_the_overseas_boxes_are_the_grids_of_the_files() -> None:
    assert meteofrance_radar.AREAS == ("france", "antilles", "reunion", "new-caledonia")
    for name, point in OVERSEAS_POINTS.items():
        assert meteofrance_radar.overseas_area_of(*point) == name
    # La Réunion's mosaic reaches Mauritius; neither Mayotte, Guyane, Tahiti, Saint-Pierre nor Paris has a radar of its own
    assert meteofrance_radar.overseas_area_of(-20.3, 57.55) == "reunion"
    for name, point in {"Mayotte": (-12.8, 45.2), "Cayenne": (4.94, -52.33), "Tahiti": (-17.55, -149.57), "Saint-Pierre": (46.78, -56.18), "Paris": (48.85, 2.35)}.items():
        assert meteofrance_radar.overseas_area_of(*point) is None, name
    assert meteofrance_radar.tile_path("antilles") == "/v1/radar/antilles/tiles/{epoch}/{z}/{x}/{y}.png"
    assert meteofrance_radar.tile_path() == TILE_PATH


async def test_each_area_asks_its_own_list_and_its_own_tiles(sim_clock: SimClock) -> None:
    provider = area_gateway()
    radar = collector(provider)
    france = await radar.frames()
    antilles = await radar.frames("antilles")
    assert france is not None and antilles is not None and france.bounds != antilles.bounds
    assert antilles.bounds == meteofrance_radar.AREA_BOXES["antilles"]
    assert await radar.frames("antilles") is antilles and provider.count == 2  # the list of an area is kept two minutes, per area
    assert await radar.frames("atlantis") is None and provider.count == 2  # an unknown area costs nothing
    assert await radar.tile(LAST, 9, 150, 230, "antilles") == FAKE_PNG
    assert await radar.tile(LAST, 9, 150, 230) == FAKE_PNG  # the same tile address of France is another tile
    assert [r.url.path for r in provider.requests[-2:]] == [f"/v1/radar/antilles/tiles/{LAST}/9/150/230.png", f"/v1/radar/tiles/{LAST}/9/150/230.png"]


@pytest.mark.parametrize("area", list(OVERSEAS_POINTS))
def test_the_overseas_list_and_tile_routes(world: World, sim_clock: SimClock, area: str) -> None:
    provider = area_gateway()
    world.aggregator.meteofrance_radar = collector(provider)
    body = world.client.get(f"/v1/radar/{area}").json()
    assert body["status"] == "ok" and body["area"] == area and body["latest_utc"] == LAST
    assert body["tile_url_template"] == f"/v1/radar/{area}/tiles/{{epoch}}/{{z}}/{{x}}/{{y}}.png"
    assert body["bounds"] == list(meteofrance_radar.AREA_BOXES[area]) and body["attribution"] == credit_text("meteofrance_radar")
    assert world.client.get("/v1/radar/france").json()["area"] == "france"
    # a tile on the area: relayed from the area's route of the gateway
    lat, lon = OVERSEAS_POINTS[area]
    z = 9
    x = int((lon + 180.0) / 360.0 * 2**z)
    y = int((1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * 2**z)
    r = world.client.get(f"/v1/radar/{area}/tiles/{LAST}/{z}/{x}/{y}.png")
    assert r.status_code == 200 and r.content == FAKE_PNG
    assert provider.requests[-1].url.path == f"/v1/radar/{area}/tiles/{LAST}/{z}/{x}/{y}.png"
    # a tile of Paris is outside the Antilles' grid: transparent without a request
    count = provider.count
    paris = world.client.get(f"/v1/radar/{area}/tiles/{LAST}/{PARIS_TILE[0]}/{PARIS_TILE[1]}/{PARIS_TILE[2]}.png")
    assert paris.status_code == 200 and paris.content == TRANSPARENT_PNG and provider.count == count
    # an image the area does not list
    assert world.client.get(f"/v1/radar/{area}/tiles/{LAST + 300}/{z}/{x}/{y}.png").status_code == 404


def test_an_unknown_area_is_refused(world: World, sim_clock: SimClock) -> None:
    world.aggregator.meteofrance_radar = collector(area_gateway())
    assert world.client.get("/v1/radar/atlantis").status_code == 422
    assert world.client.get(f"/v1/radar/atlantis/tiles/{LAST}/8/1/1.png").status_code == 422
    world.aggregator.meteofrance_radar = None
    assert world.client.get("/v1/radar/antilles").json()["status"] == "not_configured"


async def test_an_overseas_gateway_without_the_area_leaves_it_unavailable(sim_clock: SimClock) -> None:
    """An older gateway (France only) answers 404 for an overseas route: the area is unavailable, France is not touched."""

    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/radar/frames":
            return httpx.Response(200, json=frames_payload())
        return httpx.Response(404, json={"detail": "Not Found"})

    radar = collector(Provider(answer=answer))
    assert await radar.frames("antilles") is None and await radar.frames() is not None


@pytest.mark.parametrize(("area", "place"), list(OVERSEAS_POINTS.items()))
async def test_an_overseas_point_points_to_the_radar_of_its_area(sim_clock: SimClock, tmp_path: Path, area: str, place: tuple[float, float]) -> None:
    provider = area_gateway()
    service = _service(tmp_path, collector(provider))
    point = await service.build_point_response(*place)
    rain = point.rain_nowcast
    assert rain.radar_timestamp_utc == LAST
    assert rain.radar_tile_url_template == f"/v1/radar/{area}/tiles/{LAST}/{{z}}/{{x}}/{{y}}.png"
    assert rain.attribution is not None and credit_text("meteofrance_radar") in rain.attribution
    assert [r.url.path for r in provider.requests] == [f"/v1/radar/{area}/frames"]  # only the area's list, never France's


async def test_a_point_without_a_radar_of_its_own_asks_nothing(sim_clock: SimClock, tmp_path: Path) -> None:
    provider = area_gateway()
    service = _service(tmp_path, collector(provider))
    mayotte = await service.build_point_response(-12.8, 45.2)
    assert mayotte.rain_nowcast.radar_tile_url_template is None and provider.count == 0
