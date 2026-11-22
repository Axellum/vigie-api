"""Global, Europe and France forecast precipitation map overlays (``GET /v1/rain/field``, ADR 0041).

The GRIB2 fixtures are real ``GetCoverage`` answers of Météo-France's WCS (``TOTAL_PRECIPITATION__GROUND_OR_WATER_SURFACE___<run>_PT1H``)
cut to small boxes (``tests/fixtures/meteofrance_rain_field``); nothing here touches the network.
"""

from __future__ import annotations

import struct
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlsplit

import eccodes
import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import clock
from app.collectors import meteofrance_rain_field as rf
from app.collectors.grib import DECODE_LOCK, split_messages
from app.main import aggregator, app
from app.services import rain_field
from tests.helpers import Provider, SimClock, make_service

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "meteofrance_rain_field"
WIND_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "meteofrance_wind_field"
SAMPLE_KEY = "R" * 40
RUN_ARPEGE = datetime(2026, 11, 22, 6, tzinfo=timezone.utc)
RUN_AROME = datetime(2026, 11, 22, 9, tzinfo=timezone.utc)
VALID_FIXTURE = datetime(2026, 11, 22, 14, tzinfo=timezone.utc)
FIXTURE_OF = {
    "france": "arome-tp-2026112209-05h.grib2",
    "europe": "arpege-europe-tp-2026112206-08h.grib2",
    "world": "arpege-globe-tp-2026112206-08h.grib2",
}


def fixture(level: str) -> bytes:
    return (FIXTURES / FIXTURE_OF[level]).read_bytes()


def restamp_grib(raw: bytes, run: datetime, valid: datetime) -> bytes:
    """Return a copy of a real GRIB2 fixture with its run date/time and 1-hour step range updated to ``(run, valid)``."""
    end_step = int(round((valid - run).total_seconds() / 3600))
    start_step = end_step - 1
    with DECODE_LOCK:
        handle = eccodes.codes_new_from_message(raw)
        try:
            eccodes.codes_set(handle, "dataDate", int(run.strftime("%Y%m%d")))
            eccodes.codes_set(handle, "dataTime", int(run.strftime("%H%M")))
            eccodes.codes_set(handle, "startStep", start_step)
            eccodes.codes_set(handle, "endStep", end_step)
            return bytes(eccodes.codes_get_message(handle))
        finally:
            eccodes.codes_release(handle)


def read_png_rgba(data: bytes) -> np.ndarray:
    """Decode an 8-bit RGBA PNG (filter 0) into ``(rows, columns, 4)`` uint8."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    i, idat, size = 8, b"", None
    while i < len(data):
        length = struct.unpack(">I", data[i : i + 4])[0]
        kind, body = data[i + 4 : i + 8], data[i + 8 : i + 8 + length]
        if kind == b"IHDR":
            width, height, depth, color = struct.unpack(">IIBB", body[:10])
            assert (depth, color) == (8, 6)
            size = (height, width)
        elif kind == b"IDAT":
            idat += body
        i += 12 + length
    assert size is not None
    rows = np.frombuffer(zlib.decompress(idat), np.uint8).reshape(size[0], 1 + size[1] * 4)
    assert (rows[:, 0] == 0).all()
    return rows[:, 1:].reshape(size[0], size[1], 4)


# ============================================================================
# GRIB2 decoding & RGBA Mercator rendering
# ============================================================================


@pytest.mark.parametrize(
    ("level", "run", "shape", "north", "west", "step"),
    [
        ("france", RUN_AROME, (61, 101), 49.0, -5.5, 0.025),
        ("europe", RUN_ARPEGE, (51, 61), 50.0, -6.0, 0.1),
        ("world", RUN_ARPEGE, (33, 41), 48.0, 0.0, 0.25),
    ],
)
def test_real_wcs_precipitation_fixtures_decode_accurately(
    level: str, run: datetime, shape: tuple[int, int], north: float, west: float, step: float
) -> None:
    grid = rf.decode_precipitation(fixture(level))
    assert grid.short_name == "tp"
    assert grid.run == run and grid.valid == VALID_FIXTURE
    assert grid.shape == shape and (grid.north, grid.west, grid.step_lat, grid.step_lon) == (north, west, step, step)
    assert np.isfinite(grid.values).all()
    assert float(grid.values.min()) >= 0.0 and float(grid.values.max()) >= 0.1


def test_invalid_or_non_hourly_grib_messages_are_refused() -> None:
    with pytest.raises(ValueError):
        rf.decode_precipitation(b"<html>quota</html>")
    with pytest.raises(ValueError):
        rf.decode_precipitation(fixture("france") + fixture("france"))
    assert len(split_messages(fixture("france"))) == 1
    # Wind GRIB2 is not precipitation
    wind_raw = (WIND_FIXTURES / "arome-u-2026112118-06h.grib2").read_bytes()
    with pytest.raises(ValueError, match="not total precipitation"):
        rf.decode_precipitation(wind_raw)
    # Multi-hour accumulation (e.g. 3-hour step) is refused so cumulative totals are never shown as mm/h
    with DECODE_LOCK:
        handle = eccodes.codes_new_from_message(fixture("europe"))
        try:
            eccodes.codes_set(handle, "startStep", 5)
            eccodes.codes_set(handle, "endStep", 8)
            multi_hour = bytes(eccodes.codes_get_message(handle))
        finally:
            eccodes.codes_release(handle)
    with pytest.raises(ValueError, match="1-hour accumulation"):
        rf.decode_precipitation(multi_hour)


def test_colorize_and_png_rendering_respects_legend_transparency_and_mercator() -> None:
    # Synthetic grid spanning Europe-like latitudes (20..72 N) with known bands
    nj, ni = 53, 11  # 1-degree steps: north=72, south=20
    vals = np.zeros((nj, ni), dtype=np.float32)
    # Put rain only at lat >= 52 N (rows 0..20) and missing NaN in top-left corner
    vals[:21, :] = 2.5  # >= 2.0 mm/h -> #22c55e (0x22, 0xc5, 0x5e, round(0.88*255)=224)
    vals[:4, :4] = np.nan
    vals[45:, :] = 0.05  # below 0.1 mm/h -> transparent
    grid = rf.RainGrid(
        run=RUN_ARPEGE,
        valid=VALID_FIXTURE,
        short_name="tp",
        north=72.0,
        south=20.0,
        west=-5.0,
        east=5.0,
        step_lat=1.0,
        step_lon=1.0,
        values=vals,
        whole_world=False,
    )
    level = rf.RainLevel("europe", "ARPEGE", "svc", 6, 3.5, 48, thin=1, min_zoom=3.0)
    image = rf.make_rain_image(level, grid, lead_hours=1)
    assert image.bounds == (-5.0, 20.0, 5.0, 72.0)
    assert image.stamp == "2026112214-2026112206"
    pixels = read_png_rgba(image.png)
    assert pixels.shape == (image.height, image.width, 4)
    # Missing NaN top-left corner is fully transparent
    assert (pixels[0, :4] == 0).all()
    # Dry < 0.1 mm/h bottom rows are fully transparent
    assert (pixels[-5:, :] == 0).all()
    # Middle row of Web Mercator [20, 72] corresponds to ~52.7 N -> inside our wet >= 52 N band!
    # (An equirectangular projection would sample 46 N and be dry.)
    mid_row = pixels.shape[0] // 2
    assert tuple(pixels[mid_row, 5]) == (0x22, 0xC5, 0x5E, round(0.88 * 255))


def test_whole_world_grid_clamps_to_mercator_bounds_and_wraps_longitude() -> None:
    vals = np.zeros((721, 1440), dtype=np.float32)
    vals[360, 0] = 10.0  # equator, 180 W -> should appear at both left and right edges after wrap
    grid = rf.RainGrid(
        run=RUN_ARPEGE,
        valid=VALID_FIXTURE,
        short_name="tp",
        north=90.0,
        south=-90.0,
        west=-180.0,
        east=179.75,
        step_lat=0.25,
        step_lon=0.25,
        values=vals,
        whole_world=True,
    )
    image = rf.make_rain_image(rf.LEVELS[0], grid, lead_hours=1)
    assert image.bounds == (-180.0, -85.0, 180.0, 85.0)
    assert image.width == 721 and image.height == 360
    pixels = read_png_rgba(image.png)
    # Left and right wrapped columns match identically
    assert np.array_equal(pixels[:, 0], pixels[:, -1])


# ============================================================================
# Environment settings & key redaction
# ============================================================================


def test_key_and_switch() -> None:
    assert rf.rain_field_key_from_env({}) == (None, None)
    assert rf.rain_field_key_from_env({"VIGIE_METEOFRANCE_API_KEY": SAMPLE_KEY}) == (SAMPLE_KEY, None)
    assert rf.rain_field_key_from_env(
        {"VIGIE_METEOFRANCE_RAIN_API_KEY": "B" * 40, "VIGIE_METEOFRANCE_API_KEY": SAMPLE_KEY}
    ) == ("B" * 40, None)
    assert rf.rain_field_key_from_env({"VIGIE_METEOFRANCE_API_KEY": SAMPLE_KEY, "VIGIE_RAIN_FIELD": "0"}) == (None, None)
    key, problem = rf.rain_field_key_from_env({"VIGIE_METEOFRANCE_API_KEY": "short"})
    assert key is None and problem is not None and "short" not in problem


# ============================================================================
# Collector
# ============================================================================


def at(text: str) -> float:
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp()


class RainWcs:
    """Fake Météo-France WCS answering ``TOTAL_PRECIPITATION__GROUND_OR_WATER_SURFACE___<run>_PT1H``."""

    def __init__(
        self,
        missing_runs: Optional[set[tuple[str, str]]] = None,
        missing_times: Optional[set[tuple[str, str]]] = None,
        failing_levels: Optional[set[str]] = None,
    ) -> None:
        self.missing_runs = missing_runs or set()
        self.missing_times = missing_times or set()
        self.failing_levels = failing_levels or set()
        self.asked: list[tuple[str, str, str]] = []  # (level, run_stamp, valid_iso)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["apikey"] == SAMPLE_KEY and SAMPLE_KEY not in str(request.url)
        url = urlsplit(str(request.url))
        assert url.path.endswith("/GetCoverage")
        level = next(lv.id for lv in rf.LEVELS if lv.service in url.path)
        query = parse_qs(url.query)
        coverage = query["coverageid"][0]
        assert coverage.startswith("TOTAL_PRECIPITATION__GROUND_OR_WATER_SURFACE___")
        assert coverage.endswith("_PT1H")
        assert query["format"] == ["application/wmo-grib"]
        run_str = coverage.split("___", 1)[1][:-5]
        subset = next(s for s in query["subset"] if s.startswith("time("))
        valid_iso = subset[5:-1]
        self.asked.append((level, run_str, valid_iso))
        if level in self.failing_levels:
            return httpx.Response(500, text="upstream error")
        if (level, run_str) in self.missing_runs or (level, valid_iso) in self.missing_times:
            return httpx.Response(404, text="not available")
        run_dt = datetime.strptime(run_str, "%Y-%m-%dT%H.%M.%SZ").replace(tzinfo=timezone.utc)
        valid_dt = datetime.strptime(valid_iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return httpx.Response(200, content=restamp_grib(fixture(level), run_dt, valid_dt))


def collector(server: RainWcs) -> rf.MeteoFranceRainFieldCollector:
    return rf.MeteoFranceRainFieldCollector(SAMPLE_KEY, http_client=Provider(answer=server).client())


def at_1310(sim_clock: SimClock) -> None:
    sim_clock.now = at("2026-11-22T13:10:00") - SimClock.EPOCH


async def test_fetches_all_three_levels_across_lead_steps_and_reuses_cache(sim_clock: SimClock) -> None:
    at_1310(sim_clock)
    # Newest AROME run at 13:10 (delay 2h) is 09:00 UTC; ARPEGE (delay 3.5h) is 06:00 UTC.
    server = RainWcs(missing_runs={("france", "2026-11-22T09.00.00Z")})
    c = collector(server)
    got = await c.field()
    assert got is not None and not got.stale
    assert got.value.base == datetime(2026, 11, 22, 13, tzinfo=timezone.utc)
    assert [lv.level.id for lv in got.value.levels] == ["world", "europe", "france"]

    by_level = {lv.level.id: lv for lv in got.value.levels}
    # France fell back to 06:00 UTC run after 09:00 returned 404
    assert by_level["france"].run == datetime(2026, 11, 22, 6, tzinfo=timezone.utc)
    assert by_level["world"].run == datetime(2026, 11, 22, 6, tzinfo=timezone.utc)
    assert [f.lead_hours for f in by_level["world"].frames] == list(rf.LEAD_HOURS)
    assert [f.lead_hours for f in by_level["france"].frames] == list(rf.LEAD_HOURS)
    # 1 miss on france 09Z + 3 levels * 6 leads = 19 requests
    assert len(server.asked) == 19
    for lv in got.value.levels:
        for frame in lv.frames:
            assert c.image(lv.level.id, frame.stamp) == frame.png

    # Within the same hour: served directly from cache, 0 extra requests
    sim_clock.advance(600)
    again = await c.field()
    assert again is not None and again.value is got.value and len(server.asked) == 19

    # Advance past 13:30 (base becomes 14:00 UTC): +1h (15:00Z) and +2h (16:00Z) already fetched for 06Z run!
    # They are reused from _frames_by_key without re-requesting them!
    asked_before = len(server.asked)
    sim_clock.advance(1201)  # 13:40 UTC -> base 14:00 UTC
    later = await c.field()
    assert later is not None and later.value.base == datetime(2026, 11, 22, 14, tzinfo=timezone.utc)
    new_world_valid = [v for lv, _, v in server.asked[asked_before:] if lv == "world"]
    assert "2026-11-22T15:00:00Z" not in new_world_valid  # reused from previous +2h
    assert "2026-11-22T16:00:00Z" not in new_world_valid  # reused from previous +3h
    assert "2026-11-22T17:00:00Z" in new_world_valid  # new +3h


async def test_partial_lead_or_level_failure_keeps_working_levels(sim_clock: SimClock) -> None:
    at_1310(sim_clock)
    # +24h (2026-11-23T13:00:00Z) missing on europe, france completely failing
    server = RainWcs(
        missing_times={("europe", "2026-11-23T13:00:00Z")},
        failing_levels={"france"},
    )
    c = collector(server)
    got = await c.field()
    assert got is not None
    by_level = {lv.level.id: lv for lv in got.value.levels}
    assert list(by_level) == ["world", "europe"]
    assert [f.lead_hours for f in by_level["europe"].frames] == [1, 2, 3, 6, 12]
    assert [f.lead_hours for f in by_level["world"].frames] == [1, 2, 3, 6, 12, 24]


async def test_all_levels_failing_returns_none(sim_clock: SimClock) -> None:
    at_1310(sim_clock)
    c = collector(RainWcs(failing_levels={"world", "europe", "france"}))
    assert await c.field() is None


def test_disabled_source_reported_in_health(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VIGIE_METEOFRANCE_API_KEY", raising=False)
    monkeypatch.delenv("VIGIE_METEOFRANCE_RAIN_API_KEY", raising=False)
    service = make_service(tmp_path / "rain.db")
    off = {item["provider_id"]: item["reason"] for item in service.disabled_sources()}
    assert "meteofrance_rain_field" in off and "VIGIE_RAIN_FIELD" in off["meteofrance_rain_field"]


# ============================================================================
# FastAPI routes
# ============================================================================


async def test_rain_field_routes(sim_clock: SimClock, monkeypatch: pytest.MonkeyPatch) -> None:
    at_1310(sim_clock)
    c = collector(RainWcs())
    assert await c.field() is not None
    client = TestClient(app)

    # Not configured
    monkeypatch.setattr(aggregator, "meteofrance_rain_field", None)
    off = client.get("/v1/rain/field").json()
    assert off["status"] == "not_configured" and off["levels"] == [] and off["legend"] == []
    assert "Licence Ouverte 2.0" in off["licence"]
    assert "not a radar measure" in off["note"].lower()
    assert client.get("/v1/rain/field/world/2026112214-2026112206.png").status_code == 404

    # Active
    monkeypatch.setattr(aggregator, "meteofrance_rain_field", c)
    resp = client.get("/v1/rain/field")
    assert resp.status_code == 200 and resp.headers["cache-control"] == "public, max-age=300"
    body = resp.json()
    assert body["status"] == "ok" and body["stale"] is False and body["projection"] == "mercator"
    assert body["base_at"] == "2026-11-22T13:00:00Z"
    assert [lv["id"] for lv in body["levels"]] == ["world", "europe", "france"]
    assert len(body["legend"]) == 9 and body["legend"][0] == {"min_mm_h": 0.1, "color": "#a6cee3", "alpha": 0.67}
    assert "AROME" in body["attribution"] and "Licence Ouverte 2.0" in body["attribution"]

    france = body["levels"][2]
    assert france["model"] == "AROME" and france["min_zoom"] == 5.0 and france["run_at"] == "2026-11-22T09:00:00Z"
    assert len(france["frames"]) == 6
    first = france["frames"][0]
    assert first["lead_hours"] == 1 and first["valid_at"] == "2026-11-22T14:00:00Z"
    assert first["url"] == "/v1/rain/field/france/2026112214-2026112209.png"

    png_resp = client.get(first["url"])
    assert png_resp.status_code == 200 and png_resp.headers["content-type"] == "image/png"
    assert png_resp.headers["cache-control"] == "public, max-age=86400, immutable"
    assert len(png_resp.content) == first["size_bytes"]
    pixels = read_png_rgba(png_resp.content)
    assert (pixels[:, :, 3] > 0).any() and (pixels[:, :, 3] == 0).any()  # real wet & dry pixels!

    # Unknown level / unknown stamp / malformed stamp -> 404
    assert client.get("/v1/rain/field/france/2026112299-2026112209.png").status_code == 404
    assert client.get("/v1/rain/field/mars/2026112214-2026112209.png").status_code == 404
    assert client.get("/v1/rain/field/france/latest.png").status_code == 404


async def test_route_unavailable_when_all_levels_fail(sim_clock: SimClock) -> None:
    at_1310(sim_clock)
    answer = await rain_field.rain_field_map(
        collector(RainWcs(failing_levels={"world", "europe", "france"})), clock.wall()
    )
    assert answer.status == "unavailable" and answer.levels == [] and answer.legend == []
    assert answer.base_at == "2026-11-22T13:00:00Z"
