"""What the API does with the FIRMS detections: the box, the hotspots, the grid and the summary around a point.

Every figure is checked against an **independent** computation made from the recorded CSV rows (floats, plain loops, the great-circle
formula written out here), not against the code under test. The detections are the recorded ones of ``tests/fixtures/firms`` (the static files
of FIRMS of 2026-10-20: two UTC days each).
"""

from __future__ import annotations

import asyncio
import math
from typing import Any, Optional

import httpx
import pytest

from app.collectors.firms import FireReading, FireSet, FIRMSCollector, parse_csv
from app.services.fires import (
    CONFIDENCE_LEVELS,
    FireService,
    FiresOff,
    FiresUnavailable,
    grid_cells,
    lon_ranges,
    near,
    parse_bbox,
    select,
)
from tests.firms_samples import (
    FIXTURE_DAY,
    MIDNIGHT,
    N20,
    N21,
    NOW,
    SINCE_ALL,
    FirmsServer,
    csv_rows,
    haversine,
    oracle_grid,
    oracle_ids,
    oracle_near,
    rows,
    static_csv,
)
from tests.helpers import SimClock

WORLD = parse_bbox("-180,-90,180,90")


def reading_of(source: str, content: Optional[bytes] = None) -> FireReading:
    """A source as FIRMS gave it: the recorded static file, or ``content`` (a made-up file in the layout of the area API, of ``FIXTURE_DAY``)."""
    table, stats = parse_csv(static_csv(source)) if content is None else parse_csv(content, FIXTURE_DAY)
    return FireReading(source=source, via="static", read_utc=int(NOW), published_utc=None, bytes_read=0, skipped=stats.skipped, table=table)


def recorded_set() -> FireSet:
    return FireSet(readings=(reading_of(N20), reading_of(N21)), read_utc=int(NOW))


# ------------------------------------------------------------------------------------------------ the box


def test_a_box_is_west_south_east_north() -> None:
    box = parse_bbox("-6, 35, 10, 45")
    assert box.asked == (-6.0, 35.0, 10.0, 45.0) and box.as_list() == [-6.0, 35.0, 10.0, 45.0]
    assert (box.lat_low, box.lat_high) == (3_500_000, 4_500_000) and box.lon_ranges == ((-600_000, 1_000_000),)


@pytest.mark.parametrize(
    ("west", "east", "expected"),
    [
        (-6, 10, [(-6, 10)]),
        (170, -170, [(170, 180), (-180, -170)]),  # west > east: across the antimeridian (RFC 7946)
        (170, 190, [(170, 180), (-180, -170)]),  # the same, written the other way
        (-190, -170, [(170, 180), (-180, -170)]),
        (-180, 180, [(-180, 180)]),
        (-200, 200, [(-180, 180)]),  # a map that repeats the world: all of it
        (0, 360, [(-180, 180)]),
        (-540, -520, [(-180, -160)]),
        (179, 181, [(179, 180), (-180, -179)]),
        (10, 10, [(10, 10)]),
    ],
)
def test_the_longitudes_are_brought_back_into_the_globe_and_cut_at_the_antimeridian(west: float, east: float, expected: list[tuple[int, int]]) -> None:
    assert lon_ranges(west, east) == tuple((a * 100_000, b * 100_000) for a, b in expected)


@pytest.mark.parametrize(
    "text",
    ["", "1,2,3", "1,2,3,4,5", "a,b,c,d", "0,nan,1,2", "0,0,inf,2", "0,10,10,10", "0,10,10,5", "0,-91,10,0", "0,0,10,91", "0,0,541,1", "-541,0,0,1", "0;0;1;1"],
)
def test_a_box_that_is_not_one_is_refused_with_the_reason(text: str) -> None:
    with pytest.raises(ValueError, match="bbox"):
        parse_bbox(text)


# ------------------------------------------------------------------------------------------------ the hotspots


@pytest.mark.parametrize(
    "box",
    ["-10,35,5,48", "-20,-35,50,15", "110,-40,155,-10", "170,-25,-170,-10", "-180,-90,180,90", "160,50,-165,72", "-125,25,-60,60", "30,30,31,31"],
)
@pytest.mark.parametrize(("min_conf", "min_frp"), [(0, 0.0), (2, 0.0), (3, 0.0), (0, 5.0), (2, 12.5)])
def test_the_hotspots_of_a_box_are_those_of_an_independent_scan(box: str, min_conf: int, min_frp: float) -> None:
    expected = oracle_ids(box, SINCE_ALL, min_conf, min_frp)
    total, kept = select(recorded_set(), parse_bbox(box), SINCE_ALL, min_conf, min_frp, 10_000)
    assert total == len(expected) == len(kept)
    got = sorted((round(frp, 3), acq) for frp, acq, _, _ in kept)
    want = sorted((round(r["frp"], 3) if not math.isnan(r["frp"]) else -1.0, r["acq"]) for r in expected)
    assert got == want


def test_the_box_across_the_antimeridian_holds_both_sides() -> None:
    _, kept = select(recorded_set(), parse_bbox("170,-25,-170,-10"), SINCE_ALL, 0, 0.0, 10_000)
    fs = recorded_set()
    longitudes = sorted(fs.readings[t].table.lon[row] / 1e5 for _, _, t, row in kept)
    assert any(x > 170 for x in longitudes) and any(x < -170 for x in longitudes)
    assert not any(-170 <= x <= 170 for x in longitudes)


def test_the_strongest_are_kept_first_and_the_total_still_counts_all() -> None:
    fs = recorded_set()
    total, kept = select(fs, WORLD, SINCE_ALL, 0, 0.0, 25)
    assert total == len(rows()) == 237 + 235 and len(kept) == 25
    strongest = sorted((r["frp"] for r in rows() if not math.isnan(r["frp"])), reverse=True)[:25]
    assert [round(frp, 3) for frp, _, _, _ in kept] == [round(x, 3) for x in strongest]
    ordered = [(frp, acq) for frp, acq, _, _ in kept]
    assert ordered == sorted(ordered, reverse=True)


def test_the_window_keeps_the_detections_acquired_since_its_start() -> None:
    since = MIDNIGHT + 12 * 3600
    expected = [r for r in rows() if r["acq"] >= since]
    total, _ = select(recorded_set(), WORLD, since, 0, 0.0, 10)
    assert total == len(expected) and 0 < total < len(rows())
    assert select(recorded_set(), WORLD, MIDNIGHT + 24 * 3600, 0, 0.0, 10)[0] == 0


def test_a_detection_with_no_power_does_not_pass_a_minimum_power_and_sorts_last() -> None:
    body = csv_rows([(10.0, 20.0, 330, 5.0, "n"), (10.1, 20.1, 331, 9.0, "n")]).replace(b",5.0,D", b",,D")
    fs = FireSet(readings=(reading_of(N20, body),), read_utc=0)
    assert select(fs, WORLD, 0, 0, 1.0, 10)[0] == 1  # the one with a power of 9
    total, kept = select(fs, WORLD, 0, 0, 0.0, 10)
    assert total == 2 and [frp for frp, _, _, _ in kept] == [9.0, -1.0]


def test_an_unknown_confidence_passes_only_the_lowest_threshold() -> None:
    body = csv_rows([(10.0, 20.0, 330, 5.0, "x"), (10.1, 20.1, 331, 9.0, "n")])
    fs = FireSet(readings=(reading_of(N20, body),), read_utc=0)
    assert select(fs, WORLD, 0, CONFIDENCE_LEVELS["low"], 0.0, 10)[0] == 2
    assert select(fs, WORLD, 0, CONFIDENCE_LEVELS["nominal"], 0.0, 10)[0] == 1
    assert select(fs, WORLD, 0, CONFIDENCE_LEVELS["high"], 0.0, 10)[0] == 0


# ------------------------------------------------------------------------------------------------ the grid


@pytest.mark.parametrize("cell", [0.25, 0.5, 1.0, 2.0, 5.0, 10.0])
def test_the_cells_are_those_of_an_independent_count(cell: float) -> None:
    result = grid_cells(recorded_set(), WORLD, SINCE_ALL, cell, 0, 0.0, 100_000)
    expected = oracle_grid(cell)
    assert result.total_cells == len(expected) == len(result.features) and result.total_detections == len(rows())
    got = {}
    for feature in result.features:
        lon, lat = feature["geometry"]["coordinates"]
        row, column = math.floor((lat + 90.0) / cell), math.floor((lon + 180.0) / cell)  # the centre is inside its own cell
        got[(row, column)] = feature["properties"]
        assert feature["id"] == f"{cell:g}_{row}_{column}"
        assert lon == pytest.approx(-180 + (column + 0.5) * cell) and lat == pytest.approx(-90 + (row + 0.5) * cell)
    assert set(got) == set(expected)
    for key, (count, power, latest) in expected.items():
        assert got[key]["count"] == count and got[key]["latest_utc"] == latest
        assert got[key]["max_frp_mw"] == pytest.approx(power if power >= 0 else None)
    assert sum(p["count"] for p in got.values()) == result.total_detections


def test_the_busiest_cells_come_first_and_the_limit_keeps_them() -> None:
    full = grid_cells(recorded_set(), WORLD, SINCE_ALL, 5.0, 0, 0.0, 100_000)
    counts = [f["properties"]["count"] for f in full.features]
    assert counts == sorted(counts, reverse=True) and counts[0] > 1
    cut = grid_cells(recorded_set(), WORLD, SINCE_ALL, 5.0, 0, 0.0, 3)
    assert cut.total_cells == full.total_cells and cut.features == full.features[:3]


def test_a_detection_on_the_edge_of_the_globe_is_in_the_last_cell_not_outside() -> None:
    body = csv_rows([(90.0, 180.0, 330, 5.0, "n"), (-90.0, -180.0, 330, 5.0, "n"), (0.0, 0.0, 330, 5.0, "n"), (0.5, 1.0, 330, 5.0, "n")])
    result = grid_cells(FireSet(readings=(reading_of(N20, body),), read_utc=0), WORLD, 0, 1.0, 0, 0.0, 100)
    # (90, 180) is the last cell, (-90, -180) the first; (0, 0) and (0.5, 1.0) are in two cells that touch at longitude 1
    assert sorted(f["id"] for f in result.features) == sorted(["1_179_359", "1_0_0", "1_90_180", "1_90_181"])
    assert all(f["properties"]["count"] == 1 for f in result.features)


def test_cells_of_a_box_count_only_the_detections_in_the_box() -> None:
    result = grid_cells(recorded_set(), parse_bbox("-20,-35,50,15"), SINCE_ALL, 1.0, 0, 0.0, 100_000)
    assert result.total_detections == len(oracle_ids("-20,-35,50,15")) and result.total_detections > 0


# ------------------------------------------------------------------------------------------------ around a point


@pytest.mark.parametrize(
    ("lat", "lon", "radius"),
    [(-16.6, 179.0, 500.0), (-16.6, 179.0, 50.0), (38.7, -9.1, 50.0), (-17.5, 179.9, 100.0), (-17.5, -179.9, 300.0), (60.0, 175.0, 400.0), (89.9, 10.0, 500.0), (0.0, 0.0, 10.0), (45.0, 5.0, 25.0)],
)
def test_the_summary_around_a_point_is_that_of_an_independent_scan(lat: float, lon: float, radius: float) -> None:
    count, nearest, strongest, latest = near(recorded_set(), lat, lon, radius, SINCE_ALL)
    want_count, want_nearest, want_strongest, want_latest = oracle_near(lat, lon, radius)
    assert count == want_count
    assert latest == want_latest
    assert (nearest is None) == (want_nearest is None) and (strongest is None) == (want_strongest is None)
    if nearest is not None and want_nearest is not None:
        assert nearest == pytest.approx(want_nearest, abs=0.02)  # the API rounds a distance to 10 m
    if strongest is not None and want_strongest is not None:
        assert strongest == pytest.approx(want_strongest, abs=1e-3)


def test_the_summary_around_a_point_crosses_the_antimeridian() -> None:
    east = near(recorded_set(), -17.5, 179.9, 300.0, SINCE_ALL)[0]
    west = near(recorded_set(), -17.5, -179.9, 300.0, SINCE_ALL)[0]
    assert east > 0 and west > 0
    # on the antimeridian itself, with Fiji's detections on both sides of it within 150 km
    count, nearest, _, _ = near(recorded_set(), -17.2, 180.0, 150.0, SINCE_ALL)
    assert count == oracle_near(-17.2, 180.0, 150.0)[0]
    on_west_side = [r for r in rows() if r["lon"] < 0 and haversine(-17.2, 180.0, r["lat"], r["lon"]) <= 150.0]
    on_east_side = [r for r in rows() if r["lon"] > 0 and haversine(-17.2, 180.0, r["lat"], r["lon"]) <= 150.0]
    assert on_west_side and on_east_side and count == len(on_west_side) + len(on_east_side)


def test_nothing_nearby_is_zero_and_unknown_not_a_guess() -> None:
    assert near(recorded_set(), -60.0, -120.0, 50.0, SINCE_ALL) == (0, None, None, None)


# ------------------------------------------------------------------------------------------------ reading: never wait once there is something


class GatedFirms:
    """A FIRMS whose answers wait for the test to let them through."""

    def __init__(self) -> None:
        self.server = FirmsServer(latency_ticks=0)
        self.gate = asyncio.Event()
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(self.handler))

    async def handler(self, request: httpx.Request) -> httpx.Response:
        await self.gate.wait()
        self.server.requests.append(request)
        return self.server._respond(request)

    def open(self) -> None:
        self.gate.set()

    def close(self) -> None:
        self.gate = asyncio.Event()


def service_with(sim_clock: SimClock, firms: GatedFirms, **kwargs: Any) -> tuple[FireService, FIRMSCollector]:
    sim_clock.EPOCH = NOW
    collector = FIRMSCollector(http_client=firms.client, refresh_s=1800, window_hours=24, **kwargs)
    return FireService(collector, None), collector


@pytest.mark.asyncio
async def test_when_the_source_is_turned_off_the_routes_are_off_and_the_point_has_no_block(sim_clock: SimClock) -> None:
    service = FireService(None, "NASA FIRMS is turned off on this instance (VIGIE_ENABLE_FIRMS=0)")
    assert service.enabled is False
    with pytest.raises(FiresOff, match="VIGIE_ENABLE_FIRMS=0"):
        await service.features(WORLD, 24.0, "low", 0.0, 10)
    with pytest.raises(FiresOff):
        await service.grid(WORLD, 24.0, 1.0, "low", 0.0, 10)
    assert await service.nearby(48.0, 2.0) is None
    service.warm_up()
    await service.drain()


@pytest.mark.asyncio
async def test_the_point_never_waits_for_the_first_reading_and_has_the_block_once_it_is_there(sim_clock: SimClock) -> None:
    firms = GatedFirms()
    service, collector = service_with(sim_clock, firms)
    assert await service.nearby(-16.6, 179.0, 500.0) is None  # nothing read yet: no block, no wait...
    assert service._task is not None and not service._task.done()  # ...and the first read is on its way
    firms.open()
    await service.drain()
    nearby = await service.nearby(-16.6, 179.0, 500.0)
    assert nearby is not None and nearby.count_24h == oracle_near(-16.6, 179.0, 500.0, int(NOW) - 86400)[0] and nearby.stale is False
    assert nearby.sources == [N20, N21] and nearby.data_read_utc == int(NOW)
    assert collector.get_health().calls_24h == 2  # one request for the file of each source


@pytest.mark.asyncio
async def test_the_first_request_for_the_hotspots_waits_for_the_reading(sim_clock: SimClock) -> None:
    firms = GatedFirms()
    service, _ = service_with(sim_clock, firms)
    pending = asyncio.ensure_future(service.features(parse_bbox("-20,-35,50,15"), 24.0, "low", 0.0, 5))
    await asyncio.sleep(0.05)
    assert not pending.done()  # waiting for FIRMS
    firms.open()
    answer = await pending
    assert answer["metadata"]["count"] == 5 and answer["metadata"]["total_matching"] > 5 and answer["metadata"]["truncated"] is True


@pytest.mark.asyncio
async def test_once_there_is_a_reading_it_is_served_at_once_while_the_next_one_is_read(sim_clock: SimClock) -> None:
    firms = GatedFirms()
    firms.open()
    service, collector = service_with(sim_clock, firms)
    await service.features(WORLD, 24.0, "low", 0.0, 1)
    assert collector.get_health().calls_24h == 2
    sim_clock.advance(1801)  # the reading is past its lifetime
    firms.close()  # FIRMS is slow now
    before = await service.nearby(-16.6, 179.0, 500.0)
    answer = await service.grid(WORLD, 24.0, 5.0, "low", 0.0, 10)  # not waiting either
    assert before is not None and before.data_read_utc == int(NOW) and answer["metadata"]["data_read_utc"] == int(NOW)
    assert answer["metadata"]["stale"] is False  # the last attempt did not fail: it is only running
    firms.open()
    await service.drain()
    after = await service.nearby(-16.6, 179.0, 500.0)
    assert after is not None and after.data_read_utc == int(NOW) + 1801  # the files had not changed: confirmed current, not read again
    assert collector.get_health().calls_24h == 4  # one refresh (a request for each file), however many callers


@pytest.mark.asyncio
async def test_many_callers_start_one_refresh(sim_clock: SimClock) -> None:
    firms = GatedFirms()
    firms.open()
    service, collector = service_with(sim_clock, firms)
    await service.features(WORLD, 24.0, "low", 0.0, 1)
    sim_clock.advance(1801)
    await asyncio.gather(*(service.nearby(-16.6, 179.0, 500.0) for _ in range(50)), *(service.grid(WORLD, 24.0, 1.0, "low", 0.0, 10) for _ in range(5)))
    await service.drain()
    assert collector.get_health().calls_24h == 2 + 2


@pytest.mark.asyncio
async def test_a_failed_refresh_keeps_the_previous_reading_and_says_it_is_stale(sim_clock: SimClock) -> None:
    firms = GatedFirms()
    firms.open()
    service, collector = service_with(sim_clock, firms)
    await service.features(WORLD, 24.0, "low", 0.0, 1)
    sim_clock.advance(1801)
    firms.server.failing = {N20, N21}
    await service.nearby(-16.6, 179.0, 500.0)
    await service.drain()
    nearby = await service.nearby(-16.6, 179.0, 500.0)
    assert nearby is not None and nearby.stale is True and nearby.data_read_utc == int(NOW)
    answer = await service.features(WORLD, 24.0, "low", 0.0, 1)
    assert answer["metadata"]["stale"] is True and answer["metadata"]["total_matching"] > 0
    assert collector.consecutive_failures >= 1 and collector.last_error is not None
    await service.drain()


@pytest.mark.asyncio
async def test_with_nothing_read_and_nothing_readable_the_hotspots_say_so(sim_clock: SimClock) -> None:
    firms = GatedFirms()
    firms.open()
    firms.server.failing = {N20, N21}
    service, _ = service_with(sim_clock, firms)
    with pytest.raises(FiresUnavailable):
        await service.features(WORLD, 24.0, "low", 0.0, 1)
    assert await service.nearby(-16.6, 179.0) is None
    await service.drain()


@pytest.mark.asyncio
async def test_the_grid_of_the_same_question_is_computed_once_per_reading(sim_clock: SimClock, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.services.fires as module

    firms = GatedFirms()
    firms.open()
    service, _ = service_with(sim_clock, firms)
    calls = []
    real = module.grid_cells

    def counting(*args: Any) -> Any:
        calls.append(args)
        return real(*args)

    monkeypatch.setattr(module, "grid_cells", counting)
    first = await service.grid(WORLD, 24.0, 1.0, "low", 0.0, 100)
    second = await service.grid(WORLD, 24.0, 1.0, "low", 0.0, 100)
    assert len(calls) == 1 and first["features"] == second["features"]
    await service.grid(WORLD, 24.0, 2.0, "low", 0.0, 100)  # another cell size is another question
    assert len(calls) == 2
    sim_clock.advance(1801)  # the window has moved (its start is rounded to 5 minutes) and the reading is past its lifetime
    await service.grid(WORLD, 24.0, 1.0, "low", 0.0, 100)  # served from the previous reading at once, the next is read behind it
    assert len(calls) == 3
    await service.drain()
    await service.grid(WORLD, 24.0, 1.0, "low", 0.0, 100)  # the new reading answers anew
    assert len(calls) == 4


@pytest.mark.asyncio
async def test_the_answers_carry_the_credit_and_the_disclaimer_of_nasa(sim_clock: SimClock) -> None:
    from app.attribution import CREDITS, FIRMS_DISCLAIMER

    firms = GatedFirms()
    firms.open()
    service, _ = service_with(sim_clock, firms)
    for answer in (await service.features(WORLD, 24.0, "low", 0.0, 1), await service.grid(WORLD, 24.0, 1.0, "low", 0.0, 1)):
        assert answer["metadata"]["attribution"] == CREDITS["nasa_firms"].text and answer["metadata"]["disclaimer"] == FIRMS_DISCLAIMER
    nearby = await service.nearby(-16.6, 179.0, 500.0)
    assert nearby is not None and nearby.attribution == CREDITS["nasa_firms"].text and nearby.disclaimer == FIRMS_DISCLAIMER


@pytest.mark.asyncio
async def test_more_hours_than_the_files_this_instance_reads_are_refused(sim_clock: SimClock) -> None:
    from app.services.fires import FiresWindowTooLong

    firms = GatedFirms()
    firms.open()
    service, _ = service_with(sim_clock, firms)  # reads the files of 24 hours
    assert service.window_hours == 24.0
    await service.features(WORLD, 24.0, "low", 0.0, 1)  # the whole window of the files is fine
    for call in (service.features(WORLD, 25.0, "low", 0.0, 1), service.grid(WORLD, 48.0, 1.0, "low", 0.0, 1)):
        with pytest.raises(FiresWindowTooLong, match="at most 24 on this instance"):
            await call
    wide = FIRMSCollector(http_client=firms.client, refresh_s=1800, window_hours=48, sources=(N21,))
    assert FireService(wide, None).window_hours == 48.0
    assert (await FireService(wide, None).features(WORLD, 48.0, "low", 0.0, 1))["metadata"]["window_hours"] == 48.0


@pytest.mark.asyncio
async def test_each_source_says_how_it_was_read_and_when_its_file_was_written(sim_clock: SimClock) -> None:
    from tests.firms_samples import PUBLISHED

    firms = GatedFirms()
    firms.open()
    service, collector = service_with(sim_clock, firms)
    answer = await service.features(WORLD, 24.0, "low", 0.0, 1)
    assert answer["metadata"]["sources"] == [
        {"source": N20, "read_utc": int(NOW), "ok": True, "via": "static", "published_utc": int(PUBLISHED[N20])},
        {"source": N21, "read_utc": int(NOW), "ok": True, "via": "static", "published_utc": int(PUBLISHED[N21])},
    ]
    sim_clock.advance(1801)
    firms.server.failing = {N20}
    await service.nearby(-16.6, 179.0)  # starts the refresh: the file of N20 is not there, the one of N21 has not changed
    await service.drain()
    answer = await service.features(WORLD, 24.0, "low", 0.0, 1)
    assert answer["metadata"]["stale"] is True
    assert answer["metadata"]["sources"] == [
        {"source": N20, "read_utc": int(NOW), "ok": False, "via": "static", "published_utc": int(PUBLISHED[N20])},  # its last reading, 30 minutes old
        {"source": N21, "read_utc": int(NOW) + 1801, "ok": True, "via": "static", "published_utc": int(PUBLISHED[N21])},
    ]
    assert collector.failing()


@pytest.mark.asyncio
async def test_no_refresh_is_started_while_every_source_that_is_due_is_in_its_backoff(sim_clock: SimClock) -> None:
    firms = GatedFirms()
    firms.open()
    service, collector = service_with(sim_clock, firms)
    await service.features(WORLD, 24.0, "low", 0.0, 1)
    sim_clock.advance(1801)
    firms.server.failing = {N20, N21}
    await service.nearby(-16.6, 179.0)
    await service.drain()
    asked = collector.get_health().calls_24h
    for _ in range(20):  # the answers are served from the previous reading, and nothing is sent to FIRMS for a minute
        await service.nearby(-16.6, 179.0)
        await service.grid(WORLD, 24.0, 5.0, "low", 0.0, 10)
    await service.drain()
    assert collector.get_health().calls_24h == asked
    sim_clock.advance(61)
    firms.server.failing = set()
    await service.nearby(-16.6, 179.0)  # a minute has passed: the next request starts one
    await service.drain()
    assert collector.get_health().calls_24h == asked + 2 and collector.failing() is False
