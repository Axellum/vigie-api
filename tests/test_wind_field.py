"""The forecast wind of the map (``GET /v1/wind/field``, ADR 0038): AROME and ARPEGE read from the portal's WCS, served as images.

The GRIB2 files are real answers of the WCS cut to a small box (``tests/fixtures/meteofrance_wind_field``); nothing here reaches the
network.
"""

from __future__ import annotations

import struct
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlsplit

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import clock
from app.collectors import meteofrance_wind_field as wf
from app.collectors.grib import split_messages
from app.main import aggregator, app
from app.services import wind_field
from tests.helpers import Provider, SimClock

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "meteofrance_wind_field"
SAMPLE_KEY = "A" * 40
RUN = datetime(2026, 11, 21, 18, tzinfo=timezone.utc)
VALID = datetime(2026, 11, 22, 0, tzinfo=timezone.utc)
FILE_OF = {"france": "arome", "europe": "arpege-europe", "world": "arpege-globe"}


def fixture(level: str, component: str) -> bytes:
    return (FIXTURES / f"{FILE_OF[level]}-{component.lower()}-2026112118-06h.grib2").read_bytes()


def read_png(data: bytes) -> np.ndarray:
    """The pixels of an 8-bit RGB PNG of this API (filters 0 and 1 only), as (rows, columns, 3)."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    i, idat, size = 8, b"", None
    while i < len(data):
        length = struct.unpack(">I", data[i : i + 4])[0]
        kind, body = data[i + 4 : i + 8], data[i + 8 : i + 8 + length]
        if kind == b"IHDR":
            width, height, depth, color = struct.unpack(">IIBB", body[:10])
            assert (depth, color) == (8, 2)
            size = (height, width)
        elif kind == b"IDAT":
            idat += body
        i += 12 + length
    assert size is not None
    rows = np.frombuffer(zlib.decompress(idat), np.uint8).reshape(size[0], 1 + size[1] * 3)
    out = np.zeros((size[0], size[1] * 3), np.int32)
    for r in range(size[0]):
        line = rows[r, 1:].astype(np.int32)
        if rows[r, 0] == 1:  # Sub: add the byte three to the left
            for x in range(3, line.size):
                line[x] = (line[x] + line[x - 3]) % 256
        else:
            assert rows[r, 0] == 0
        out[r] = line
    return out.reshape(size[0], size[1], 3)


# ============================================================================
# Files and grids
# ============================================================================


@pytest.mark.parametrize(
    ("level", "shape", "north", "west", "step"),
    [("france", (61, 101), 49.0, -5.5, 0.025), ("europe", (51, 61), 50.0, -6.0, 0.1), ("world", (33, 41), 48.0, 0.0, 0.25)],
)
def test_a_real_answer_of_each_service_is_read_at_its_place_and_hour(level: str, shape: tuple[int, int], north: float, west: float, step: float) -> None:
    u = wf.decode_component(fixture(level, "U"))
    v = wf.decode_component(fixture(level, "V"))
    assert (u.short_name, v.short_name) == ("10u", "10v")
    assert u.run == RUN and u.valid == VALID
    assert u.shape == shape and (u.north, u.west, u.step_lat, u.step_lon) == (north, west, step, step)
    assert np.isfinite(u.values).all() and not np.array_equal(u.values, v.values)
    assert -40 < float(u.values.min()) < float(u.values.max()) < 40


def test_what_is_not_one_component_of_the_wind_is_refused() -> None:
    with pytest.raises(ValueError):
        wf.decode_component(b"<html>quota</html>")
    with pytest.raises(ValueError):
        wf.decode_component(fixture("france", "U") + fixture("france", "V"))  # two messages
    assert len(split_messages(fixture("france", "U"))) == 1


def test_a_whole_world_grid_is_turned_to_begin_at_180_west() -> None:
    columns = np.tile(np.arange(1440, dtype=np.float64) * 0.25, (3, 1))  # the longitude of each column, 0 to 359.75
    rolled, west = wf._roll_to_dateline(columns, 0.0, 0.25)
    assert west == -180.0 and rolled[0, 0] == 180.0 and rolled[0, 719] == 359.75 and rolled[0, 720] == 0.0


def test_thinning_keeps_a_steady_wind_steady_and_no_value_where_there_was_none() -> None:
    steady = np.full((7, 9), 5.0, np.float32)
    steady[0, 0] = np.nan
    thin = wf.thin_grid(steady, 2, whole_world=False)
    assert thin.shape == (4, 5) and np.isnan(thin[0, 0]) and np.allclose(thin[np.isfinite(thin)], 5.0)
    assert wf.thin_grid(steady, 1, whole_world=False).shape == (7, 9)
    # around the world, the first column is smoothed with the last one
    ring = np.zeros((3, 8), np.float32)
    ring[:, -1] = 8.0
    assert wf.thin_grid(ring, 2, whole_world=True)[1, 0] == pytest.approx(2.0)
    assert wf.thin_grid(ring, 2, whole_world=False)[1, 0] == pytest.approx(0.0)


def test_an_image_gives_the_components_back_within_half_a_step() -> None:
    level = wf.LEVELS[2]  # france
    u, v = wf.decode_component(fixture("france", "U")), wf.decode_component(fixture("france", "V"))
    image = wf.make_image(level, u, v)
    assert (image.width, image.height) == (51, 31) and image.bbox == (-5.5, 47.5, -3.0, 49.0)
    assert image.step_lat == 0.05 and image.stamp == "2026112200-2026112118" and not image.whole_world
    pixels = read_png(image.png)
    for channel, grid in ((0, u), (1, v)):
        expected = wf.thin_grid(grid.values, 2, False)
        back = (pixels[:, :, channel] - 127.5) / 127.5 * image.scale_ms
        assert np.abs(back - expected).max() <= image.scale_ms / 255 + 1e-6
    assert (pixels[:, :, 2] == 255).all()  # a value everywhere in this box
    assert image.scale_ms == max(wf.SCALE_MIN_MS, np.ceil(np.abs(np.stack([wf.thin_grid(u.values, 2, False), wf.thin_grid(v.values, 2, False)])).max()))


def test_where_the_model_has_no_value_the_image_says_so() -> None:
    u = wf.decode_component(fixture("france", "U"))
    v = wf.decode_component(fixture("france", "V"))
    hole = u.values.copy()
    hole[:6, :6] = np.nan
    image = wf.make_image(wf.LEVELS[2], wf.Grid(**{**u.__dict__, "values": hole}), v)
    pixels = read_png(image.png)
    assert (pixels[:3, :3, 2] == 0).all() and (pixels[10:, 10:, 2] == 255).all()


def test_two_components_of_different_grids_or_hours_are_refused() -> None:
    u = wf.decode_component(fixture("france", "U"))
    other = wf.decode_component(fixture("europe", "V"))
    with pytest.raises(ValueError):
        wf.make_image(wf.LEVELS[2], u, other)
    with pytest.raises(ValueError):
        wf.make_image(wf.LEVELS[2], u, u)  # east twice


# ============================================================================
# Time
# ============================================================================


def at(text: str) -> float:
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp()


def test_the_hour_is_the_nearest_one_and_changes_at_half_past() -> None:
    assert wf.nearest_hour(at("2026-11-22T01:29:59")) == datetime(2026, 11, 22, 1, tzinfo=timezone.utc)
    assert wf.nearest_hour(at("2026-11-22T01:30:00")) == datetime(2026, 11, 22, 2, tzinfo=timezone.utc)
    assert wf.seconds_to_next_hour_change(at("2026-11-22T00:40:00")) == 3000
    assert wf.seconds_to_next_hour_change(at("2026-11-22T01:29:30")) == 60  # never less than a minute


def test_the_runs_tried_are_the_newest_published_then_the_ones_before() -> None:
    now = at("2026-11-22T00:10:00")
    valid = wf.nearest_hour(now)
    world, _, france = wf.LEVELS
    assert [r.hour for r in wf.candidate_runs(france, valid, now)] == [21, 18, 15]  # AROME every 3 h, 2 h to publish
    assert [r.hour for r in wf.candidate_runs(world, valid, now)] == [18, 12, 6]  # ARPEGE every 6 h, 3 h 30 to publish
    assert wf.run_stamp(RUN) == "2026-11-21T18.00.00Z" and wf.image_stamp(VALID, RUN) == "2026112200-2026112118"
    far = wf.Level("x", "AROME", "s", 3, 2.0, 6, 2, 0)  # leads beyond 6 h are not asked
    assert [r.hour for r in wf.candidate_runs(far, valid, now)] == [21, 18]


def test_the_key_and_the_switch() -> None:
    assert wf.wind_field_key_from_env({}) == (None, None)
    assert wf.wind_field_key_from_env({"VIGIE_METEOFRANCE_API_KEY": SAMPLE_KEY}) == (SAMPLE_KEY, None)
    assert wf.wind_field_key_from_env({"VIGIE_METEOFRANCE_WIND_API_KEY": "B" * 40, "VIGIE_METEOFRANCE_API_KEY": SAMPLE_KEY}) == ("B" * 40, None)
    assert wf.wind_field_key_from_env({"VIGIE_METEOFRANCE_API_KEY": SAMPLE_KEY, "VIGIE_WIND_FIELD": "0"}) == (None, None)
    key, problem = wf.wind_field_key_from_env({"VIGIE_METEOFRANCE_API_KEY": "short"})
    assert key is None and problem is not None and "short" not in problem


# ============================================================================
# The collector
# ============================================================================


class Wcs:
    """The WCS of the portal: a GetCoverage per component, answered from the fixtures; ``missing`` runs answer 404, ``failing``
    services 500, and ``answer_for`` replaces the hour of the file asked."""

    def __init__(self, missing: Optional[set[tuple[str, str]]] = None, failing: Optional[set[str]] = None) -> None:
        self.missing = missing if missing is not None else {("france", "2026-11-21T21.00.00Z")}
        self.failing = failing or set()
        self.asked: list[tuple[str, str, str, str]] = []  # (level, component, run, time)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["apikey"] == SAMPLE_KEY and SAMPLE_KEY not in str(request.url)  # the key in the header only
        url = urlsplit(str(request.url))
        assert url.path.endswith("/GetCoverage")
        level = next(level.id for level in wf.LEVELS if level.service in url.path)
        query = parse_qs(url.query)
        coverage = query["coverageid"][0]
        component, run = coverage[0], coverage.split("___")[1]
        subsets = query["subset"]
        assert "height(10)" in subsets and query["format"] == ["application/wmo-grib"]
        moment = next(s for s in subsets if s.startswith("time("))
        self.asked.append((level, component, run, moment))
        if level in self.failing:
            return httpx.Response(500, text="error")
        if (level, run) in self.missing:
            return httpx.Response(404, text="no such coverage")
        assert run == "2026-11-21T18.00.00Z"
        return httpx.Response(200, content=fixture(level, component))


def collector(server: Wcs) -> wf.MeteoFranceWindFieldCollector:
    return wf.MeteoFranceWindFieldCollector(SAMPLE_KEY, http_client=Provider(answer=server).client())


def at_ten_past_midnight(sim_clock: SimClock) -> None:
    sim_clock.now = at("2026-11-22T00:10:00") - SimClock.EPOCH


async def test_the_three_grids_of_the_hour_are_read_once(sim_clock: SimClock) -> None:
    at_ten_past_midnight(sim_clock)
    server = Wcs()
    c = collector(server)
    got = await c.field()
    assert got is not None and not got.stale and got.value.valid == VALID
    assert [i.level.id for i in got.value.images] == ["world", "europe", "france"]
    assert {i.run for i in got.value.images} == {RUN}
    # AROME of 21 UTC lacks the hour: one request for it, then 18 UTC; two components of each grid
    assert sorted(server.asked) == sorted(
        [("france", "U", "2026-11-21T21.00.00Z", "time(2026-11-22T00:00:00Z)")]
        + [(level, comp, "2026-11-21T18.00.00Z", "time(2026-11-22T00:00:00Z)") for level in FILE_OF for comp in "UV"]
    )
    for image in got.value.images:
        assert c.image(image.level.id, image.stamp) == image.png
    asked = len(server.asked)
    sim_clock.advance(600)  # the same hour: nothing asked again
    again = await c.field()
    assert again is not None and again.value is got.value and len(server.asked) == asked
    sim_clock.advance(601)  # 00:30: the next hour is asked; the files of the portal here are still of 00 UTC, so each grid fails
    later = await c.field()
    assert later is not None and later.value.valid == datetime(2026, 11, 22, 1, tzinfo=timezone.utc)
    assert [i.valid for i in later.value.images] == [VALID] * 3  # the images of 00 UTC kept, their own hour said
    assert {a[3] for a in server.asked[asked:]} == {"time(2026-11-22T01:00:00Z)"}
    assert ("france", "U", "2026-11-21T21.00.00Z", "time(2026-11-22T01:00:00Z)") in server.asked[asked:]  # missing 20 min ago: asked again
    asked = len(server.asked)
    sim_clock.advance(300)  # an answer that is not whole is made again after 10 minutes, not before
    assert (await c.field()) is not None and len(server.asked) == asked
    sim_clock.advance(301)
    assert (await c.field()) is not None and len(server.asked) > asked


async def test_a_grid_that_fails_is_left_out_and_asked_again_ten_minutes_later(sim_clock: SimClock) -> None:
    at_ten_past_midnight(sim_clock)
    server = Wcs(failing={"france"})
    c = collector(server)
    got = await c.field()
    assert got is not None and [i.level.id for i in got.value.images] == ["world", "europe"]
    asked = len(server.asked)
    sim_clock.advance(601)
    server.failing = set()
    later = await c.field()
    assert later is not None and [i.level.id for i in later.value.images] == ["world", "europe", "france"]
    assert {a[0] for a in server.asked[asked:]} == {"france"}  # the two other grids are kept, not asked again


async def test_a_file_of_another_hour_is_not_served_as_this_one(sim_clock: SimClock) -> None:
    sim_clock.now = at("2026-11-22T01:10:00") - SimClock.EPOCH  # 01 UTC asked, the files are of 00 UTC
    got = await collector(Wcs()).field()
    assert got is None


async def test_with_every_grid_failing_there_is_nothing(sim_clock: SimClock) -> None:
    at_ten_past_midnight(sim_clock)
    assert await collector(Wcs(failing={"france", "europe", "world"})).field() is None


# ============================================================================
# The routes
# ============================================================================


async def test_the_routes(sim_clock: SimClock, monkeypatch: pytest.MonkeyPatch) -> None:
    at_ten_past_midnight(sim_clock)
    c = collector(Wcs())
    assert await c.field() is not None
    client = TestClient(app)

    monkeypatch.setattr(aggregator, "meteofrance_wind_field", None)
    off = client.get("/v1/wind/field").json()
    assert off["status"] == "not_configured" and off["levels"] == [] and "Licence Ouverte" in off["licence"]
    assert client.get("/v1/wind/field/france/2026112200-2026112118.png").status_code == 404

    monkeypatch.setattr(aggregator, "meteofrance_wind_field", c)
    on = client.get("/v1/wind/field")
    assert on.status_code == 200 and on.headers["cache-control"] == "public, max-age=300"
    body = on.json()
    assert body["status"] == "ok" and body["valid_at"] == "2026-11-22T00:00:00Z" and body["stale"] is False
    assert [lv["id"] for lv in body["levels"]] == ["world", "europe", "france"]
    france = body["levels"][2]
    assert france["model"] == "AROME" and france["run_at"] == "2026-11-21T18:00:00Z" and france["min_zoom"] == 5.0
    assert france["bbox"] == [-5.5, 47.5, -3.0, 49.0] and (france["width"], france["height"]) == (51, 31) and france["step_deg"] == 0.05
    assert france["image"] == "/v1/wind/field/france/2026112200-2026112118.png"
    assert "AROME" in body["attribution"] and "Licence Ouverte 2.0" in body["attribution"]

    image = client.get(france["image"])
    assert image.status_code == 200 and image.headers["content-type"] == "image/png"
    assert image.headers["cache-control"] == "public, max-age=86400, immutable" and len(image.content) == france["size_bytes"]
    assert client.get("/v1/wind/field/france/2026112201-2026112118.png").status_code == 404  # an image never made
    assert client.get("/v1/wind/field/mars/2026112200-2026112118.png").status_code == 404
    assert client.get("/v1/wind/field/france/latest.png").status_code == 404


async def test_the_route_says_unavailable_when_nothing_could_be_read(sim_clock: SimClock) -> None:
    at_ten_past_midnight(sim_clock)
    answer = await wind_field.wind_field_map(collector(Wcs(failing={"france", "europe", "world"})), clock.wall())
    assert answer.status == "unavailable" and answer.levels == [] and answer.valid_at == "2026-11-22T00:00:00Z"


