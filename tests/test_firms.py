"""The NASA FIRMS collector: the CSV it reads, the files it asks for, and what it does when FIRMS stalls or fails.

The rows are the recorded ones of ``tests/fixtures/firms`` (a selection of the real static files of 2026-10-20 for NOAA-20 and NOAA-21, and of a
real area read for the fallback); the servers are simulated ones (``tests/firms_samples``) behind an ``httpx.MockTransport``. Nothing touches the network.
"""

from __future__ import annotations

import asyncio
import hashlib
from calendar import timegm
from datetime import datetime, timezone
from typing import Any

import httpx
import pytest

from app.collectors.base import CollectorUnavailableError
from app.collectors.budget import CallBudget, Limit, Quota
from app.collectors.firms import (
    ALLOWED_SOURCES,
    BACKOFF_S,
    DEFAULT_REFRESH_MINUTES,
    DEFAULT_SOURCES,
    DEFAULT_WINDOW_HOURS,
    PARTIAL_KEEP_S,
    WORLD_DAY_TRANSACTIONS,
    FIRMSCollector,
    HotspotTable,
    Validators,
    _joined_if_md5,
    _md5_of,
    load_settings,
    parse_csv,
    parse_csvs,
    static_url,
    utc_dates,
)
from tests.firms_samples import (
    FIXTURE_DAY,
    HEADER,
    KEY,
    N20,
    N21,
    NOW,
    PUBLISHED,
    REAL_VALIDATORS,
    STATIC_HEADER,
    AreaServer,
    FirmsAnywhere,
    FirmsServer,
    area_rows,
    at,
    csv_for,
    csv_rows,
    md5_etag,
    static_csv,
    static_csv_rows,
    static_rows,
)
from tests.helpers import Provider, SimClock

MIDNIGHT = timegm(datetime.strptime(FIXTURE_DAY, "%Y-%m-%d").timetuple())
STATIC_BASE = "https://firms.modaps.eosdis.nasa.gov/data/active_fire"


def find(table: HotspotTable, lat: float, lon: float) -> int:
    """The row of a recorded detection, by its position."""
    for position in table.rows_between(round(lat * 1e5), round(lat * 1e5)):
        row = table.order[position]
        if table.lon[row] == round(lon * 1e5):
            return int(row)
    raise AssertionError(f"no detection at {lat}, {lon}")


# ------------------------------------------------------------------------------------------------ the CSV


def test_the_recorded_rows_of_a_static_file_are_read_with_the_fields_of_the_file() -> None:
    table, stats = parse_csv(static_csv(N21))
    assert stats.rows == len(static_rows(N21)) == 235 and stats.skipped == 0
    row = find(table, 54.6165, 160.29666)  # the first line of the recording
    assert table.acq[row] == int(at("2026-10-19", "00:39"))  # the row's own date: a static file holds two days
    assert (table.satellite(row), table.instrument(row), table.version(row)) == ("N21", None, "2.0NRT")  # no `instrument` column in the static files
    assert table.conf[row] == 3 and table.dn[row] == 1  # high (written in full), day
    assert table.number(table.frp, row) == pytest.approx(35.57)
    assert table.number(table.ti4, row) == pytest.approx(367.0) and table.number(table.ti5, row) == pytest.approx(291.97)
    assert table.number(table.scan, row) == pytest.approx(0.43) and table.number(table.track, row) == pytest.approx(0.62)
    assert stats.latest_acq_utc == int(at("2026-10-20", "16:20"))  # the latest recorded detection


def test_a_static_file_holds_the_rows_of_every_date_it_has() -> None:
    table, _ = parse_csv(static_csv(N21))
    days = {datetime.fromtimestamp(table.acq[i], timezone.utc).strftime("%Y-%m-%d") for i in range(len(table))}
    assert days == {"2026-10-19", "2026-10-20"}
    table48, _ = parse_csv(static_csv(N21, 48))
    assert {datetime.fromtimestamp(table48.acq[i], timezone.utc).strftime("%Y-%m-%d") for i in range(len(table48))} == {"2026-10-18", "2026-10-19", "2026-10-20"}


def test_the_confidence_is_read_in_full_or_by_its_first_letter() -> None:
    table, _ = parse_csv(static_csv(N20))
    rows = static_rows(N20)
    assert sum(1 for code in table.conf if code == 1) == sum(1 for row in rows if row["confidence"] == "low") > 0
    assert sum(1 for code in table.conf if code == 3) == sum(1 for row in rows if row["confidence"] == "high") > 0
    assert sum(1 for code in table.conf if code == 0) == 0
    area, _ = parse_csv(csv_for(N21, FIXTURE_DAY), FIXTURE_DAY)  # the area API writes `l`, `n`, `h`
    assert sum(1 for code in area.conf if code == 1) == sum(1 for row in area_rows(N21) if row["confidence"] == "l") > 0


def test_every_area_row_is_there_whatever_its_confidence_or_version() -> None:
    table, _ = parse_csv(csv_for(N21, FIXTURE_DAY), FIXTURE_DAY)
    rows = area_rows(N21)
    assert len(table) == len(rows) == 218
    assert sorted(set(table.versions)) == ["2.0NRT", "2.0URT"] and table.satellites == ("N21",) and table.instruments == ("VIIRS",)
    positions = [table.lat[row] for row in table.order]
    assert positions == sorted(positions)  # the index lists the rows by latitude


@pytest.mark.parametrize(("hhmm", "seconds"), [("0", 0), ("1", 60), ("59", 59 * 60), ("0100", 3600), ("100", 3600), ("949", 9 * 3600 + 49 * 60), ("0949", 9 * 3600 + 49 * 60), ("1809", 18 * 3600 + 9 * 60), ("2359", 23 * 3600 + 59 * 60)])
def test_the_time_is_hhmm_with_or_without_a_leading_zero(hhmm: str, seconds: int) -> None:
    """The static files write ``0949``; the area API wrote ``949`` and ``1`` for 00:01."""
    table, _ = parse_csv(static_csv_rows([(10.0, 20.0, FIXTURE_DAY, hhmm, 1.0, "nominal")]))
    assert table.acq[0] == MIDNIGHT + seconds


def test_a_row_without_a_readable_position_or_time_is_skipped_and_counted() -> None:
    body = (
        STATIC_HEADER
        + "\n10.0,20.0,300,0.4,0.4,2026-10-20,0330,N20,nominal,2.0NRT,290,5.5,D"  # good
        + "\nabc,20.0,300,0.4,0.4,2026-10-20,0330,N20,nominal,2.0NRT,290,5.5,D"  # latitude is not a number
        + "\n95.0,20.0,300,0.4,0.4,2026-10-20,0330,N20,nominal,2.0NRT,290,5.5,D"  # latitude beyond the poles
        + "\n10.0,181.0,300,0.4,0.4,2026-10-20,0330,N20,nominal,2.0NRT,290,5.5,D"  # longitude beyond the globe
        + "\nnan,20.0,300,0.4,0.4,2026-10-20,0330,N20,nominal,2.0NRT,290,5.5,D"  # not a finite position
        + "\n10.0,20.0,300,0.4,0.4,2026-10-20,2460,N20,nominal,2.0NRT,290,5.5,D"  # minute 60
        + "\n10.0,20.0,300,0.4,0.4,2026-10-20,2400,N20,nominal,2.0NRT,290,5.5,D"  # hour 24
        + "\n10.0,20.0,300,0.4,0.4,2026-10-20,soon,N20,nominal,2.0NRT,290,5.5,D"  # no time
        + "\n10.0,20.0,300,0.4,0.4,yesterday,0330,N20,nominal,2.0NRT,290,5.5,D"  # no date
        + "\n10.0,20.0,300,0.4,0.4,2026-13-45,0330,N20,nominal,2.0NRT,290,5.5,D"  # a date that does not exist
        + "\n10.0,20.0\n"  # cut short
    ).encode()
    table, stats = parse_csv(body)
    assert (stats.rows, stats.skipped) == (1, 10) and len(table) == 1


def test_a_row_of_another_day_than_the_one_asked_is_skipped_by_an_area_read() -> None:
    body = csv_rows([(10.0, 20.0, 330, 5.0, "n")]) + b"10.1,20.1,330.5,0.4,0.4,2026-10-19,330,N20,VIIRS,n,2.0NRT,295.1,5.0,D\n"
    table, stats = parse_csv(body, FIXTURE_DAY)
    assert (stats.rows, stats.skipped) == (1, 1) and len(table) == 1  # it belongs to that day's own read
    both, stats_both = parse_csv(body)  # a static file has no day asked: both rows are kept
    assert len(both) == 2 and stats_both.skipped == 0


def test_several_files_make_one_table() -> None:
    today, yesterday = csv_for(N20, "2026-10-20"), csv_for(N20, "2026-10-19")
    table, stats = parse_csvs([(today, "2026-10-20"), (yesterday, "2026-10-19")])
    assert stats.rows == len(table) == 2 * 258 and stats.skipped == 0
    assert {datetime.fromtimestamp(table.acq[i], timezone.utc).strftime("%Y-%m-%d") for i in range(len(table))} == {"2026-10-20", "2026-10-19"}
    assert table.satellites == ("N20",)  # one palette for both


def test_a_value_the_file_does_not_give_is_unknown_not_zero() -> None:
    body = (STATIC_HEADER + "\n10.0,20.0,,0.4,0.4,2026-10-20,0330,N20,x,2.0NRT,,,D\n").encode()
    table, _ = parse_csv(body)
    assert table.number(table.frp, 0) is None and table.number(table.ti4, 0) is None and table.number(table.ti5, 0) is None
    assert table.conf[0] == 0  # a confidence class that is not l, n, h (or in full) is unknown


def test_a_file_with_fewer_columns_is_read_with_unknown_values() -> None:
    table, stats = parse_csv(b"latitude,longitude,acq_date,acq_time\n10.5,20.5,2026-10-20,330\n")
    assert stats.rows == 1
    assert (table.satellite(0), table.instrument(0), table.version(0)) == (None, None, None)
    assert table.number(table.frp, 0) is None and table.conf[0] == 0 and table.dn[0] == 0


def test_a_header_and_no_row_is_a_valid_empty_window() -> None:
    table, stats = parse_csv((STATIC_HEADER + "\n").encode())
    assert len(table) == 0 and stats.rows == 0 and stats.latest_acq_utc is None


@pytest.mark.parametrize(
    "body",
    [b"", b"Invalid MAP_KEY.", b"<html><body>Service unavailable</body></html>", b"latitude,longitude\n1,2\n", b"Exceeding transaction limit"],
)
def test_what_is_not_the_csv_is_refused_not_read_as_no_fire(body: bytes) -> None:
    with pytest.raises(ValueError):
        parse_csv(body)


def test_a_file_with_more_dates_than_a_window_holds_is_not_a_firms_file() -> None:
    lines = [STATIC_HEADER] + [f"10.0,20.0,300,0.4,0.4,2026-01-{day:02d},0330,N20,nominal,2.0NRT,290,5.5,D" for day in range(1, 21)]
    with pytest.raises(ValueError, match="distinct dates"):
        parse_csv(("\n".join(lines) + "\n").encode())


def test_the_index_by_latitude_finds_exactly_the_rows_of_a_band() -> None:
    table, _ = parse_csv(static_csv(N20))
    low, high = round(30 * 1e5), round(45 * 1e5)
    expected = sorted(i for i in range(len(table)) if low <= table.lat[i] <= high)
    assert sorted(table.order[k] for k in table.rows_between(low, high)) == expected and expected
    assert len(table.rows_between(round(89 * 1e5), round(90 * 1e5))) == 0


def test_the_rows_are_kept_in_a_few_dozen_bytes_each() -> None:
    table, _ = parse_csv(static_csv(N20))
    columns = [table.lat, table.lon, table.acq, table.frp, table.ti4, table.ti5, table.scan, table.track, table.conf, table.dn, table.sat, table.inst, table.ver, table.order]
    assert sum(column.itemsize for column in columns) == 41  # 3 ints, 5 floats, 5 bytes of codes and the index: measured 43 bytes a row with the slack


# ------------------------------------------------------------------------------------------------ the files and the settings


def test_the_files_are_those_of_the_documented_list() -> None:
    assert static_url(N21, 24) == f"{STATIC_BASE}/noaa-21-viirs-c2/csv/J2_VIIRS_C2_Global_24h.csv"
    assert static_url(N20, 48) == f"{STATIC_BASE}/noaa-20-viirs-c2/csv/J1_VIIRS_C2_Global_48h.csv"
    assert "VIIRS_SNPP_NRT" not in ALLOWED_SOURCES  # Suomi NPP stops on 2026-11-02


def test_the_source_is_on_without_a_key_by_default() -> None:
    settings = load_settings({})
    assert settings.enabled is True and settings.off_reason is None and settings.key is None and settings.key_problem is None
    assert settings.sources == DEFAULT_SOURCES == (N20, N21) and settings.refresh_s == 3600 and settings.window_hours == DEFAULT_WINDOW_HOURS == 48
    assert FIRMSCollector().ttl_seconds == 3600 and DEFAULT_REFRESH_MINUTES == 60


@pytest.mark.parametrize("value", ["0", "false", "No", "OFF", " off "])
def test_an_instance_can_turn_the_source_off_and_it_says_why(value: str) -> None:
    settings = load_settings({"VIGIE_ENABLE_FIRMS": value})
    assert settings.enabled is False and settings.off_reason is not None and "VIGIE_ENABLE_FIRMS" in settings.off_reason


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", ""])
def test_anything_else_leaves_it_on(value: str) -> None:
    assert load_settings({"VIGIE_ENABLE_FIRMS": value}).enabled is True


def test_a_key_is_optional_and_taken_as_it_is_stripped() -> None:
    assert load_settings({"VIGIE_FIRMS_MAP_KEY": f"  {KEY}\n"}).key == KEY
    assert load_settings({"VIGIE_FIRMS_MAP_KEY": "   "}).key is None


@pytest.mark.parametrize("value", ["bad key with spaces", "key/with/slashes", "../../etc", "abc", "k" * 65, "clé0123456789"])
def test_a_key_that_is_not_a_firms_key_is_not_used_and_not_repeated(value: str) -> None:
    settings = load_settings({"VIGIE_FIRMS_MAP_KEY": value})
    assert settings.key is None and settings.enabled is True  # the files need none
    assert settings.key_problem is not None and "not a FIRMS key" in settings.key_problem and value not in settings.key_problem


@pytest.mark.parametrize(
    ("sources", "expected"),
    [(N21, (N21,)), (f" {N20} , {N21} ", (N20, N21)), (f"{N20},{N20}", (N20,)), ("VIIRS_SNPP_NRT", DEFAULT_SOURCES), (f"{N20},MODIS_NRT", DEFAULT_SOURCES), ("", DEFAULT_SOURCES)],
)
def test_the_sources_come_from_the_environment_or_the_default(sources: str, expected: tuple[str, ...]) -> None:
    assert load_settings({"VIGIE_FIRMS_SOURCES": sources}).sources == expected


@pytest.mark.parametrize(("minutes", "seconds"), [("10", 600), ("30", 1800), ("360", 21600), ("5", 3600), ("361", 3600), ("often", 3600), ("", 3600), ("-30", 3600)])
def test_the_refresh_is_ten_minutes_to_six_hours_else_the_default(minutes: str, seconds: int) -> None:
    assert load_settings({"VIGIE_FIRMS_REFRESH_MINUTES": minutes}).refresh_s == seconds


@pytest.mark.parametrize(("hours", "expected"), [("24", 24), ("48", 48), (" 24 ", 24), ("7", 48), ("72", 48), ("many", 48), ("", 48)])
def test_the_window_is_the_file_of_24_or_of_48_hours_else_the_default(hours: str, expected: int) -> None:
    assert load_settings({"VIGIE_FIRMS_WINDOW_HOURS": hours}).window_hours == expected


def test_a_collector_refuses_a_source_or_a_window_it_has_no_file_for() -> None:
    with pytest.raises(ValueError):
        FIRMSCollector(sources=("VIIRS_SNPP_NRT",))
    with pytest.raises(ValueError):
        FIRMSCollector(window_hours=7)


def test_the_two_days_of_the_area_api_are_those_of_the_utc_day() -> None:
    assert utc_dates(at("2026-10-20", "00:00")) == ("2026-10-20", "2026-10-19")
    assert utc_dates(at("2026-10-20", "23:59")) == ("2026-10-20", "2026-10-19")
    assert utc_dates(at("2026-03-01", "00:30")) == ("2026-03-01", "2026-02-28")


def test_a_file_is_the_one_held_when_its_etag_is_or_else_its_date_and_size() -> None:
    held = Validators('"abc"', "Tue, 20 Oct 2026 19:43:17 GMT", 100)
    assert Validators('"abc"', "another date", 5).same_as(held)  # the ETag decides when both have one
    assert not Validators('"abd"', "Tue, 20 Oct 2026 19:43:17 GMT", 100).same_as(held)
    no_etag = Validators(None, "Tue, 20 Oct 2026 19:43:17 GMT", 100)
    assert Validators(None, "Tue, 20 Oct 2026 19:43:17 GMT", 100).same_as(no_etag)
    assert not Validators(None, "Tue, 20 Oct 2026 19:43:17 GMT", 101).same_as(no_etag)
    assert not Validators(None, None, None).same_as(Validators(None, None, None))  # nothing to compare: not "the same"


# ------------------------------------------------------------------------------------------------ the files that are asked


def make(sim_clock: SimClock, start: float = NOW, **kwargs: Any) -> tuple[FIRMSCollector, FirmsServer]:
    sim_clock.EPOCH = start
    server = FirmsServer()
    kwargs.setdefault("refresh_s", 1800)  # the timelines below are written for a refresh every 30 minutes (the default is an hour)
    kwargs.setdefault("window_hours", 24)
    return FIRMSCollector(http_client=server.client(), **kwargs), server


@pytest.mark.asyncio
async def test_the_first_refresh_asks_the_file_of_each_source_once_with_no_key(sim_clock: SimClock) -> None:
    collector, server = make(sim_clock)
    data = await collector.fetch()
    assert server.reads() == [(N20, 24), (N21, 24)] and server.count == 2
    assert data.sources() == (N20, N21) and data.total_rows() == len(static_rows(N20)) + len(static_rows(N21))
    first = server.requests[0]
    assert str(first.url) == f"{STATIC_BASE}/noaa-20-viirs-c2/csv/J1_VIIRS_C2_Global_24h.csv" and first.url.query == b""
    assert first.headers["accept"].startswith("text/csv") and first.headers["accept-encoding"] == "identity"
    assert "if-none-match" not in first.headers and "if-modified-since" not in first.headers and "range" not in first.headers  # nothing held yet
    reading = data.reading(N21)
    assert reading is not None and reading.via == "static" and reading.read_utc == int(NOW) and reading.published_utc == int(PUBLISHED[N21])
    assert reading.bytes_read == len(static_csv(N21)) and reading.skipped == 0
    assert reading.validators == Validators(*REAL_VALIDATORS[N21][:1], REAL_VALIDATORS[N21][1], len(static_csv(N21)))
    # the request is counted, and weighs nothing against the quota of the key: a static file costs no transaction
    health = collector.get_health()
    assert health.calls_24h == 2 and health.weight_24h == 0
    assert health.quota is not None and [(limit.window_s, limit.limit, limit.used) for limit in health.quota.limits] == [(600, 5000, 0.0)]


@pytest.mark.asyncio
async def test_the_window_of_48_hours_asks_the_files_of_48_hours(sim_clock: SimClock) -> None:
    collector, server = make(sim_clock, sources=(N21,), window_hours=48)
    data = await collector.fetch()
    assert server.reads() == [(N21, 48)] and collector.window_hours == 48
    assert data.total_rows() == len(static_rows(N21, 48)) == 241


@pytest.mark.asyncio
async def test_one_source_is_enough(sim_clock: SimClock) -> None:
    collector, server = make(sim_clock, sources=(N21,))
    data = await collector.fetch()
    assert server.reads() == [(N21, 24)] and data.sources() == (N21,)


@pytest.mark.asyncio
async def test_a_refresh_inside_its_lifetime_reads_nothing(sim_clock: SimClock) -> None:
    collector, server = make(sim_clock)
    await collector.fetch()
    sim_clock.advance(1799)
    await collector.fetch()
    await asyncio.gather(*(collector.fetch() for _ in range(5)))
    assert server.count == 2


@pytest.mark.asyncio
async def test_concurrent_callers_share_one_set_of_requests(sim_clock: SimClock) -> None:
    collector, server = make(sim_clock)
    results = await asyncio.gather(*(collector.fetch() for _ in range(8)))
    assert server.count == 2 and all(result is results[0] for result in results)


@pytest.mark.asyncio
async def test_the_two_sources_are_asked_at_the_same_time(sim_clock: SimClock) -> None:
    """One that is slow must not make the other wait for it: both requests are on their way before either answers."""
    sim_clock.EPOCH = NOW
    server = FirmsServer(latency_ticks=0)
    started: list[str] = []
    release = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        started.append(request.url.path.split("/")[3])
        await release.wait()
        return server._respond(request)

    collector = FIRMSCollector(http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), window_hours=24)
    task = asyncio.ensure_future(collector.fetch())
    for _ in range(20):
        await asyncio.sleep(0)
    assert sorted(started) == ["noaa-20-viirs-c2", "noaa-21-viirs-c2"]
    release.set()
    assert (await task).sources() == (N20, N21)


# ------------------------------------------------------------------------------------------------ the validators: has the file changed?


@pytest.mark.asyncio
async def test_a_file_that_has_not_changed_is_not_read_again(sim_clock: SimClock) -> None:
    """The real server answers 200 and the whole file to a conditional request: the headers say it is the one held, and the connection is closed."""
    collector, server = make(sim_clock)
    first = await collector.fetch()
    pulled = dict(server.body_bytes)
    sim_clock.advance(1801)
    second = await collector.fetch()
    assert server.count == 4 and server.reads() == [(N20, 24), (N21, 24)] * 2
    second_asks = server.requests[2:]
    assert second_asks[0].headers["if-none-match"] == REAL_VALIDATORS[N20][0] and second_asks[0].headers["if-modified-since"] == REAL_VALIDATORS[N20][1]
    assert server.body_bytes == pulled  # not one byte of the files was pulled
    for source in (N20, N21):
        old, new = first.reading(source), second.reading(source)
        assert old is not None and new is not None
        assert new.table is old.table and new.bytes_read == old.bytes_read  # the same rows, not parsed again
        assert new.read_utc == int(NOW) + 1801 and new.published_utc == old.published_utc  # confirmed current now, written when it was
    assert second.read_utc == int(NOW) + 1801


@pytest.mark.asyncio
async def test_a_server_that_does_answer_304_is_believed(sim_clock: SimClock) -> None:
    collector, server = make(sim_clock)
    server.honour_304 = True
    first = await collector.fetch()
    sim_clock.advance(1801)
    second = await collector.fetch()
    assert server.body_bytes == {N20: len(static_csv(N20)), N21: len(static_csv(N21))}
    assert second.reading(N20).table is first.reading(N20).table and second.reading(N20).read_utc == int(NOW) + 1801  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_a_file_that_changed_is_read_and_parsed_again(sim_clock: SimClock) -> None:
    collector, server = make(sim_clock)
    first = await collector.fetch()
    sim_clock.advance(1801)
    shorter = b"\n".join(static_csv(N20).split(b"\n")[:101]) + b"\n"  # a new file: 100 rows
    server.publish(N20, shorter, modified="Tue, 20 Oct 2026 20:50:00 GMT")
    second = await collector.fetch()
    new, kept = second.reading(N20), second.reading(N21)
    assert new is not None and len(new.table) == 100 and new.published_utc == int(at("2026-10-20", "20:50")) and new.bytes_read == len(shorter)
    assert new.table is not first.reading(N20).table  # type: ignore[union-attr]
    assert kept is not None and kept.table is first.reading(N21).table  # the other source was not touched
    assert server.requests[2].headers["if-none-match"] == REAL_VALIDATORS[N20][0] and server.requests[-1].headers["if-none-match"] == REAL_VALIDATORS[N21][0]


@pytest.mark.asyncio
async def test_a_file_with_the_same_rows_and_a_new_etag_is_a_new_file(sim_clock: SimClock) -> None:
    collector, server = make(sim_clock, sources=(N20,))
    first = await collector.fetch()
    sim_clock.advance(1801)
    server.publish(N20)  # nothing new in it, but FIRMS wrote it again: it is read (the validators are all that is known of a file)
    second = await collector.fetch()
    assert second.reading(N20).table is not first.reading(N20).table and len(second.reading(N20).table) == len(first.reading(N20).table)  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_without_validators_a_file_is_read_each_time(sim_clock: SimClock) -> None:
    sim_clock.EPOCH = NOW

    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=static_csv(N20), headers={"Content-Type": "text/csv"})

    provider = Provider(answer=answer, latency_ticks=0)
    collector = FIRMSCollector(http_client=provider.client(), sources=(N20,), window_hours=24, refresh_s=1800)
    first = await collector.fetch()
    sim_clock.advance(1801)
    second = await collector.fetch()
    assert provider.count == 2 and second.reading(N20).table is not first.reading(N20).table  # type: ignore[union-attr]
    assert second.reading(N20).published_utc is None and "if-none-match" not in provider.requests[1].headers  # type: ignore[union-attr]


# ------------------------------------------------------------------------------------------------ a server that stalls


@pytest.mark.asyncio
async def test_a_source_that_stalls_fails_alone_and_is_asked_again_after_a_minute(sim_clock: SimClock) -> None:
    collector, server = make(sim_clock)
    server.stall = {N20: 0}
    server.stall_timeout = True  # what httpx raises after 25 s without a byte
    data = await collector.fetch()
    assert data.sources() == (N21,) and collector.failing() is True and collector.source_ok(N20) is False and collector.source_ok(N21) is True
    assert collector.errors() == [f"{N20}: no data for 25 s (ReadTimeout)"]
    assert collector.last_error == f"{N20}: no data for 25 s (ReadTimeout)"  # GET /v1/providers says it
    assert collector.circuit_state.value == "CLOSED"
    # nothing is asked for a minute, whoever asks: the set is fresh for as long as the backoff of the source that failed
    sim_clock.advance(BACKOFF_S[0] - 1)
    before = server.count
    assert collector.waiting() is True and await collector.fetch() is data
    assert server.count == before
    # after it, that source alone is asked, and the one that works is left alone
    sim_clock.advance(2)
    data = await collector.fetch()
    assert server.reads()[before:] == [(N20, 24)] and data.sources() == (N20, N21) and collector.failing() is False and collector.last_error is None


@pytest.mark.asyncio
async def test_a_source_that_keeps_failing_is_asked_after_1_2_5_then_10_minutes(sim_clock: SimClock) -> None:
    collector, server = make(sim_clock, sources=(N20,))
    server.failing = {N20}
    waited = []
    for _ in range(6):
        with pytest.raises(CollectorUnavailableError):
            await collector.fetch()
        state = collector._states[N20]
        waited.append(state.retry_at - sim_clock.time())
        sim_clock.advance(waited[-1] + 0.5)
    assert waited == [60.0, 120.0, 300.0, 600.0, 600.0, 600.0] and BACKOFF_S == (60.0, 120.0, 300.0, 600.0)
    assert server.count == 6  # one request per attempt, never more


@pytest.mark.asyncio
async def test_a_download_that_never_ends_is_stopped_by_the_deadline(sim_clock: SimClock) -> None:
    collector, server = make(sim_clock, sources=(N20,))
    collector.deadline_s = 0.2
    server.stall = {N20: 4096}  # 4 KB, then silence for good
    with pytest.raises(CollectorUnavailableError, match="no complete answer within 0.2 s"):
        await collector.fetch()
    assert collector.failing() and server.body_bytes[N20] == 4096


@pytest.mark.asyncio
async def test_a_source_that_stalls_keeps_serving_its_last_reading_as_stale(sim_clock: SimClock) -> None:
    collector, server = make(sim_clock)
    good = await collector.fetch()
    sim_clock.advance(1801)
    server.stall = {N21: 0}
    server.stall_timeout = True
    server.publish(N21)  # the file changed: it has to be downloaded, and it stalls
    data = await collector.fetch()
    assert collector.failing() and collector.source_ok(N21) is False
    assert data.reading(N21) is good.reading(N21)  # the previous reading of the same source
    assert data.reading(N20) is not good.reading(N20) and data.reading(N20).read_utc == int(NOW) + 1801  # type: ignore[union-attr]
    sim_clock.advance(BACKOFF_S[0] + 1)
    server.stall.clear()
    again = await collector.fetch()
    assert collector.failing() is False and again.reading(N21).table is not good.reading(N21).table  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_a_download_stopped_in_the_middle_is_continued_not_started_again(sim_clock: SimClock) -> None:
    collector, server = make(sim_clock, sources=(N20,))
    full = static_csv(N20)
    server.stall = {N20: 6000}
    server.stall_timeout = True
    with pytest.raises(CollectorUnavailableError):
        await collector.fetch()
    assert collector.errors() == [f"{N20}: no data for 25 s (ReadTimeout) (after 6000 of {len(full)} bytes, kept to continue)"]
    assert server.body_bytes[N20] == 6000
    sim_clock.advance(BACKOFF_S[0] + 1)
    data = await collector.fetch()
    retry = server.requests[-1]
    assert retry.headers["range"] == "bytes=6000-" and retry.headers["if-range"] == REAL_VALIDATORS[N20][0] and "if-none-match" not in retry.headers
    assert server.body_bytes[N20] == len(full)  # 6,000 bytes, then only the rest of the file
    reading = data.reading(N20)
    assert reading is not None and len(reading.table) == len(static_rows(N20)) and reading.bytes_read == len(full)
    assert collector.failing() is False and collector._states[N20].partial is None


@pytest.mark.asyncio
async def test_a_cut_connection_is_continued_too_and_the_result_is_the_whole_file(sim_clock: SimClock) -> None:
    collector, server = make(sim_clock, sources=(N21,))
    server.cut = {N21: 10_000}
    with pytest.raises(CollectorUnavailableError, match="ReadError"):
        await collector.fetch()
    sim_clock.advance(BACKOFF_S[0] + 1)
    data = await collector.fetch()
    assert server.requests[-1].headers["range"] == "bytes=10000-"
    # the rows are those of an uninterrupted download, not a cut file that happens to be valid
    whole, _ = parse_csv(static_csv(N21))
    got = data.reading(N21).table  # type: ignore[union-attr]
    assert len(got) == len(whole) and list(got.lat) == list(whole.lat) and list(got.acq) == list(whole.acq) and list(got.frp)[:50] == list(whole.frp)[:50]


@pytest.mark.asyncio
async def test_a_server_that_ignores_the_range_sends_the_whole_file_and_it_is_taken(sim_clock: SimClock) -> None:
    collector, server = make(sim_clock, sources=(N20,))
    server.ranges = False
    server.cut = {N20: 5000}
    with pytest.raises(CollectorUnavailableError):
        await collector.fetch()
    sim_clock.advance(BACKOFF_S[0] + 1)
    data = await collector.fetch()
    assert server.requests[-1].headers["range"] == "bytes=5000-"  # asked...
    assert len(data.reading(N20).table) == len(static_rows(N20)) and server.body_bytes[N20] == 5000 + len(static_csv(N20))  # ...and not given: all of it again


@pytest.mark.asyncio
async def test_a_file_that_changed_in_between_is_not_glued_to_the_old_beginning(sim_clock: SimClock) -> None:
    collector, server = make(sim_clock, sources=(N20,))
    server.cut = {N20: 5000}
    with pytest.raises(CollectorUnavailableError):
        await collector.fetch()
    shorter = b"\n".join(static_csv(N20).split(b"\n")[:51]) + b"\n"
    server.publish(N20, shorter)  # If-Range no longer matches: the server sends the new file whole
    sim_clock.advance(BACKOFF_S[0] + 1)
    data = await collector.fetch()
    assert len(data.reading(N20).table) == 50 and data.reading(N20).bytes_read == len(shorter)  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_a_continuation_that_is_not_the_range_asked_is_refused(sim_clock: SimClock) -> None:
    sim_clock.EPOCH = NOW
    server = FirmsServer(latency_ticks=0)
    server.cut = {N20: 5000}
    mismatches: list[httpx.Response] = []

    def answer(request: httpx.Request) -> httpx.Response:
        response = server._respond(request)
        if "range" in request.headers:  # a server that answers another range than the one asked
            data = static_csv(N20)
            response = httpx.Response(206, headers={"ETag": REAL_VALIDATORS[N20][0], "Content-Range": f"bytes 0-{len(data) - 1}/{len(data)}", "Content-Length": str(len(data))}, content=data)
            mismatches.append(response)
        return response

    collector = FIRMSCollector(http_client=Provider(answer=answer, latency_ticks=0).client(), sources=(N20,), window_hours=24)
    with pytest.raises(CollectorUnavailableError):
        await collector.fetch()
    sim_clock.advance(BACKOFF_S[0] + 1)
    with pytest.raises(CollectorUnavailableError, match="did not answer the range that was asked"):
        await collector.fetch()
    assert mismatches and collector._states[N20].partial is None  # what was kept is dropped: the next attempt starts again


@pytest.mark.asyncio
async def test_a_download_that_is_too_old_is_not_continued(sim_clock: SimClock) -> None:
    collector, server = make(sim_clock, sources=(N20,))
    server.cut = {N20: 5000}
    with pytest.raises(CollectorUnavailableError):
        await collector.fetch()
    sim_clock.advance(PARTIAL_KEEP_S + 1)  # the monotonic clock of the collectors is simulated too
    data = await collector.fetch()
    assert "range" not in server.requests[-1].headers and len(data.reading(N20).table) == len(static_rows(N20))  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_an_answer_shorter_than_it_says_is_a_failure_not_a_short_file(sim_clock: SimClock) -> None:
    """The announced size is checked: a file cut at a line break would be a valid CSV with fewer rows."""
    sim_clock.EPOCH = NOW
    data = static_csv(N20)
    cut = b"\n".join(data.split(b"\n")[:50]) + b"\n"
    provider = Provider(answer=lambda request: httpx.Response(200, headers={"Content-Length": str(len(data))}, content=cut), latency_ticks=0)
    collector = FIRMSCollector(http_client=provider.client(), sources=(N20,), window_hours=24)
    with pytest.raises(CollectorUnavailableError, match=f"holds {len(cut)} bytes of the {len(data)} announced"):
        await collector.fetch()
    assert collector.cached() == (None, False)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "body", "expected"),
    [(504, b"Gateway Timeout", "HTTP 504"), (500, b"oops", "HTTP 500"), (429, b"slow down", "HTTP 429"), (403, b"", "HTTP 403"), (404, b"", "HTTP 404"),
     (200, b"Invalid MAP_KEY.", "not a FIRMS CSV"), (200, b"<html>maintenance</html>", "not a FIRMS CSV"), (200, b"", "empty answer")],
)
async def test_an_answer_that_is_not_the_file_is_a_failure_with_its_reason(sim_clock: SimClock, code: int, body: bytes, expected: str) -> None:
    collector, server = make(sim_clock, sources=(N20,))
    server.status[N20] = (code, body)
    with pytest.raises(CollectorUnavailableError, match=expected):
        await collector.fetch()
    assert collector.failing() and collector.cached() == (None, False)


@pytest.mark.asyncio
async def test_a_504_after_a_good_reading_keeps_the_reading_and_says_so(sim_clock: SimClock) -> None:
    collector, server = make(sim_clock)
    good = await collector.fetch()
    sim_clock.advance(1801)
    server.status = {N20: (504, b""), N21: (504, b"")}
    answer = await collector.fetch_ex()
    assert answer.stale is True and answer.value is good
    assert collector.failing() and collector.consecutive_failures == 1
    assert collector.last_error is not None and f"{N20}: HTTP 504 from firms.modaps.eosdis.nasa.gov" in collector.last_error and N21 in collector.last_error
    assert collector.circuit_state.value == "CLOSED"  # the backoff of each source does the work of a circuit breaker


@pytest.mark.asyncio
async def test_nothing_read_yet_and_nothing_readable_is_an_error_not_an_empty_answer(sim_clock: SimClock) -> None:
    collector, server = make(sim_clock)
    server.failing = {N20, N21}
    with pytest.raises(CollectorUnavailableError):
        await collector.fetch()
    assert collector.cached() == (None, False)
    count = server.count
    for _ in range(3):  # whoever asks meanwhile, FIRMS is not asked: both sources are in their minute
        with pytest.raises(CollectorUnavailableError):
            await collector.fetch()
    assert server.count == count and collector.circuit_state.value == "CLOSED"


@pytest.mark.asyncio
async def test_a_source_that_failed_for_hours_is_not_hammered(sim_clock: SimClock) -> None:
    """A FIRMS that is down all day, with a visitor every 20 seconds: 144 requests per source at most, the rhythm of the longest backoff."""
    collector, server = make(sim_clock, sources=(N20,))
    server.failing = {N20}
    for _ in range(86_400 // 20):
        sim_clock.advance(20)
        if not collector.waiting():
            with pytest.raises(CollectorUnavailableError):
                await collector.fetch()
    assert server.count <= 3 + 86_400 // 600 + 1, server.count


@pytest.mark.asyncio
async def test_the_set_is_fresh_until_the_first_source_falls_due(sim_clock: SimClock) -> None:
    collector, server = make(sim_clock, refresh_s=600)
    assert collector.cached() == (None, False)
    data = await collector.fetch()
    assert collector.cached() == (data, True)
    sim_clock.advance(601)
    assert collector.cached() == (data, False)
    # one source that fails is asked again in a minute, not in ten
    server.failing = {N20}
    data = await collector.fetch()
    assert collector.cached() == (data, True)
    sim_clock.advance(BACKOFF_S[0] - 1)
    assert collector.cached()[1] is True
    sim_clock.advance(2)
    assert collector.cached()[1] is False


@pytest.mark.asyncio
async def test_a_bug_in_one_source_does_not_take_the_other_with_it(sim_clock: SimClock, monkeypatch: pytest.MonkeyPatch) -> None:
    collector, _ = make(sim_clock)
    real = collector._read_static

    async def broken(client: Any, source: str, state: Any, now: float) -> Any:
        if source == N20:
            raise KeyError("boom")
        return await real(client, source, state, now)

    monkeypatch.setattr(collector, "_read_static", broken)
    data = await collector.fetch()
    assert data.sources() == (N21,) and collector.errors() == [f"{N20}: KeyError: 'boom'"]


# ------------------------------------------------------------------------------------------------ the budget


@pytest.mark.asyncio
async def test_a_static_file_is_read_even_when_the_quota_of_the_key_is_used_up(sim_clock: SimClock) -> None:
    collector, server = make(sim_clock, sources=(N20,))
    collector.budget = CallBudget(Quota("nasa-firms", (Limit(600, 30),), "https://example.invalid/"), 0.9)  # 27 transactions: not even one world day
    data = await collector.fetch()
    assert data.sources() == (N20,) and server.count == 1 and collector.budget_refusals == 0


# ------------------------------------------------------------------------------------------------ the key: the area API as a second way


def make_with_key(sim_clock: SimClock, **kwargs: Any) -> tuple[FIRMSCollector, FirmsServer, AreaServer]:
    sim_clock.EPOCH = NOW
    static, area = FirmsServer(latency_ticks=0), AreaServer(latency_ticks=0)
    kwargs.setdefault("refresh_s", 1800)
    kwargs.setdefault("window_hours", 24)
    collector = FIRMSCollector(KEY, http_client=FirmsAnywhere(static, area).client(), **kwargs)
    return collector, static, area


@pytest.mark.asyncio
async def test_without_a_key_the_area_api_is_never_asked(sim_clock: SimClock) -> None:
    collector, server = make(sim_clock)
    server.failing = {N20, N21}
    with pytest.raises(CollectorUnavailableError):
        await collector.fetch()
    assert all(request.url.path.startswith("/data/active_fire/") for request in server.requests)


@pytest.mark.asyncio
async def test_with_a_key_the_files_come_first_and_the_area_api_is_not_asked_while_they_answer(sim_clock: SimClock) -> None:
    collector, static, area = make_with_key(sim_clock)
    data = await collector.fetch()
    assert static.count == 2 and area.count == 0
    assert {reading.via for reading in data.readings} == {"static"}


@pytest.mark.asyncio
async def test_a_source_whose_file_cannot_be_read_is_read_from_the_area_api_with_the_key(sim_clock: SimClock) -> None:
    collector, static, area = make_with_key(sim_clock)
    static.failing = {N20}
    data = await collector.fetch()
    assert area.dates() == [(N20, "2026-10-20"), (N20, "2026-10-19")]  # that source only, today then yesterday
    assert area.requests[0].url.path == f"/api/area/csv/{KEY}/{N20}/world/1/2026-10-20" and area.requests[0].url.query == b""
    area_reading, static_reading = data.reading(N20), data.reading(N21)
    assert area_reading is not None and area_reading.via == "area" and area_reading.published_utc is None and area_reading.validators is None
    assert len(area_reading.table) == 2 * 258 and static_reading is not None and static_reading.via == "static"
    assert collector.failing() is False and collector.source_ok(N20) is True  # it was read: it is not "stale"
    health = collector.get_health()
    assert health.calls_24h == 4 and health.weight_24h == 2 * WORLD_DAY_TRANSACTIONS == 72  # 2 files, 2 world days of the key
    assert health.quota is not None and health.quota.limits[0].used == 72.0


@pytest.mark.asyncio
async def test_the_next_refresh_asks_the_file_again_first_and_with_no_validators_held_for_it(sim_clock: SimClock) -> None:
    collector, static, area = make_with_key(sim_clock, sources=(N20,))
    static.failing = {N20}
    await collector.fetch()
    static.failing = set()
    sim_clock.advance(1801)
    data = await collector.fetch()
    assert data.reading(N20).via == "static" and "if-none-match" not in static.requests[-1].headers and area.count == 2  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_when_the_area_api_fails_too_the_source_fails_with_both_reasons_and_no_key(sim_clock: SimClock) -> None:
    collector, static, area = make_with_key(sim_clock, sources=(N20,))
    static.status[N20] = (504, b"")
    area.status[N20] = (500, f"error for the key {KEY}".encode())
    with pytest.raises(CollectorUnavailableError) as raised:
        await collector.fetch()
    assert f"{N20}: HTTP 504 from firms.modaps.eosdis.nasa.gov; then from the area API: {N20} 2026-10-20: HTTP 500" in str(raised.value)
    assert KEY not in str(raised.value) and KEY not in (collector.last_error or "")


@pytest.mark.asyncio
async def test_the_area_api_is_held_to_the_quota_of_the_key(sim_clock: SimClock) -> None:
    collector, static, area = make_with_key(sim_clock, sources=(N20,))
    static.failing = {N20}
    collector.budget = CallBudget(Quota("nasa-firms", (Limit(600, 60),), "https://example.invalid/"), 0.9)  # 54: one world day, not two
    with pytest.raises(CollectorUnavailableError, match="budget"):
        await collector.fetch()
    assert area.dates() == [(N20, "2026-10-20")] and collector.budget_refusals == 1


@pytest.mark.asyncio
async def test_a_budget_that_runs_out_for_one_source_keeps_the_other(sim_clock: SimClock) -> None:
    collector, static, area = make_with_key(sim_clock)
    static.failing = {N20}
    collector.budget = CallBudget(Quota("nasa-firms", (Limit(600, 30),), "https://example.invalid/"), 0.9)  # 27: not even one world day
    data = await collector.fetch()
    assert data.sources() == (N21,) and collector.errors() == [f"{N20}: call budget used up"] and collector.budget_refusals == 1
    assert collector.consecutive_failures == 0  # a refused request is not a failure of the provider


@pytest.mark.asyncio
async def test_an_area_answer_that_is_not_the_csv_is_refused_with_its_reason(sim_clock: SimClock) -> None:
    collector, static, area = make_with_key(sim_clock, sources=(N20,))
    static.failing = {N20}
    area.status[N20] = (200, b"Invalid MAP_KEY.")
    with pytest.raises(CollectorUnavailableError, match="not a FIRMS CSV"):
        await collector.fetch()


def test_the_csv_helpers_of_the_fixture_agree_with_the_header_constants() -> None:
    assert HEADER.split(",")[:2] == ["latitude", "longitude"] and "instrument" in HEADER and "instrument" not in STATIC_HEADER


# ------------------------------------------------------------------------------------------------ a file that grew: only what was added is read


def rows_without_header(rows: list[tuple[float, float, str, str, float, str]], satellite: str = "N20") -> bytes:
    return static_csv_rows(rows, satellite).split(b"\n", 1)[1]


EXTRA = rows_without_header([(10.5, 20.5, "2026-10-20", "1700", 7.5, "high"), (11.5, 21.5, "2026-10-20", "1701", 3.5, "nominal")])


async def first_read_of_a_file_that_can_be_extended(sim_clock: SimClock, **kwargs: Any) -> tuple[FIRMSCollector, FirmsServer, bytes]:
    """A collector that has read ``base``, a file whose ``ETag`` is its MD5 (the recorded file's label is not: it is the whole file's, which is not here)."""
    collector, server = make(sim_clock, sources=(N20,), **kwargs)
    base = static_csv(N20)
    server.publish(N20, base, modified="Tue, 20 Oct 2026 19:40:30 GMT")
    await collector.fetch()
    assert server.body_bytes[N20] == len(base) and collector._states[N20].raw == base
    return collector, server, base


@pytest.mark.asyncio
async def test_a_file_that_only_grew_is_read_for_what_was_added(sim_clock: SimClock) -> None:
    collector, server, base = await first_read_of_a_file_that_can_be_extended(sim_clock)
    before = collector._states[N20].reading
    sim_clock.advance(1801)
    grown = base + EXTRA
    server.publish(N20, grown, modified="Tue, 20 Oct 2026 20:40:27 GMT")
    data = await collector.fetch()
    check, tail = server.requests[-2], server.requests[-1]
    assert "range" not in check.headers and check.headers["if-none-match"] == md5_etag(base)  # the question: has it changed?
    assert tail.headers["range"] == f"bytes={len(base)}-" and tail.headers["if-range"] == md5_etag(grown)  # the answer: what is new, of that file
    assert server.body_bytes[N20] == len(base) + len(EXTRA)  # not one byte of the 10 MB that were held was read again
    reading = data.reading(N20)
    assert reading is not None and reading.bytes_read == len(EXTRA) and reading.published_utc == int(at("2026-10-20", "20:40")) + 27
    assert len(reading.table) == len(before.table) + 2 and collector._states[N20].raw == grown  # type: ignore[union-attr]
    whole, _ = parse_csv(grown)
    assert list(reading.table.lat) == list(whole.lat) and list(reading.table.order) == list(whole.order)  # the rows of the whole file, parsed from the whole file
    assert collector.failing() is False


@pytest.mark.asyncio
async def test_a_file_that_grew_again_in_between_is_read_whole(sim_clock: SimClock) -> None:
    """The check saw one file, and by the time the added bytes are asked FIRMS has written another: ``If-Range`` no longer matches, so 200."""
    collector, server, base = await first_read_of_a_file_that_can_be_extended(sim_clock)
    sim_clock.advance(1801)
    server.publish(N20, base + EXTRA, modified="Tue, 20 Oct 2026 20:40:27 GMT")
    newer = base + EXTRA + EXTRA
    asked: list[httpx.Request] = []

    def on_request(request: httpx.Request) -> None:
        asked.append(request)
        if "range" in request.headers and len(asked) == 2:  # between the check and the added bytes
            server.publish(N20, newer, modified="Tue, 20 Oct 2026 20:41:00 GMT")

    server.on_request = on_request
    data = await collector.fetch()
    assert [("range" in r.headers) for r in asked] == [False, True, False]  # the check, the added bytes (refused), the whole file
    assert len(data.reading(N20).table) == len(parse_csv(newer)[0]) and collector._states[N20].raw == newer  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_a_file_that_was_rewritten_and_is_longer_is_not_taken_for_the_old_one_with_bytes_added(sim_clock: SimClock) -> None:
    """After the rollover of a UTC day the oldest rows leave: the file may well be longer, and it does not start with what is held."""
    collector, server, base = await first_read_of_a_file_that_can_be_extended(sim_clock)
    sim_clock.advance(1801)
    lines = base.split(b"\n")
    lines[1] = lines[1].replace(b"2026-10-19", b"2026-10-18")  # the first row is not what it was
    rewritten = b"\n".join(lines) + EXTRA
    server.publish(N20, rewritten, modified="Tue, 20 Oct 2026 20:40:27 GMT")
    data = await collector.fetch()
    assert [("range" in r.headers) for r in server.requests[-3:]] == [False, True, False]  # the check, the added bytes, then the whole file
    reading = data.reading(N20)
    assert reading is not None and reading.bytes_read == len(rewritten) and len(reading.table) == len(parse_csv(rewritten)[0])
    assert server.body_bytes[N20] == len(base) + (len(rewritten) - len(base)) + len(rewritten)  # the tail that did not fit, and the whole file
    assert collector._states[N20].raw == rewritten and collector.failing() is False


@pytest.mark.asyncio
async def test_a_file_that_got_shorter_is_read_in_the_same_request(sim_clock: SimClock) -> None:
    collector, server, base = await first_read_of_a_file_that_can_be_extended(sim_clock)
    sim_clock.advance(1801)
    shorter = b"\n".join(base.split(b"\n")[:101]) + b"\n"
    server.publish(N20, shorter, modified="Tue, 20 Oct 2026 20:40:27 GMT")
    before = server.count
    data = await collector.fetch()
    assert server.count == before + 1 and len(data.reading(N20).table) == 100  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_a_server_that_ignores_ranges_gives_the_whole_file_of_a_file_that_grew(sim_clock: SimClock) -> None:
    collector, server, base = await first_read_of_a_file_that_can_be_extended(sim_clock)
    sim_clock.advance(1801)
    server.ranges = False
    grown = base + EXTRA
    server.publish(N20, grown, modified="Tue, 20 Oct 2026 20:40:27 GMT")
    data = await collector.fetch()
    assert len(data.reading(N20).table) == len(parse_csv(grown)[0]) and server.body_bytes[N20] == len(base) + len(grown)  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_a_file_with_an_etag_that_is_not_an_md5_is_not_kept_to_be_extended(sim_clock: SimClock) -> None:
    collector, server = make(sim_clock, sources=(N20,))
    server.publish(N20)  # the same rows under a label that is not an MD5
    await collector.fetch()
    assert collector._states[N20].raw is None
    sim_clock.advance(1801)
    server.publish(N20, static_csv(N20) + EXTRA)  # md5 now, but there is nothing held to add to
    before = server.count
    await collector.fetch()
    assert server.count == before + 1 and "range" not in server.requests[-1].headers


@pytest.mark.asyncio
async def test_what_is_held_of_a_file_is_let_go_when_the_area_api_takes_over(sim_clock: SimClock) -> None:
    sim_clock.EPOCH = NOW
    static, area = FirmsServer(latency_ticks=0), AreaServer(latency_ticks=0)
    static.publish(N20, static_csv(N20))
    collector = FIRMSCollector(KEY, http_client=FirmsAnywhere(static, area).client(), sources=(N20,), window_hours=24, refresh_s=1800)
    await collector.fetch()
    assert collector._states[N20].raw is not None
    sim_clock.advance(1801)
    static.failing = {N20}
    data = await collector.fetch()
    assert data.reading(N20).via == "area" and collector._states[N20].raw is None  # type: ignore[union-attr]


def test_an_etag_is_an_md5_when_it_is_32_hex_digits_in_quotes() -> None:
    assert _md5_of('"644399737c597320b1e075faab41d530"') == "644399737c597320b1e075faab41d530"
    for etag in (None, "", "644399737c597320b1e075faab41d530", 'W/"644399737c597320b1e075faab41d530"', '"644399737c597320b1e075faab41d530-3"', '"abc"'):
        assert _md5_of(etag) is None  # a weak one, a multipart one, one without quotes: nothing is assembled on their word


def test_what_is_held_and_what_was_added_make_the_file_only_when_its_md5_says_so() -> None:
    whole = b"latitude,longitude\n1,2\n3,4\n"
    good = hashlib.md5(whole).hexdigest()  # noqa: S324
    assert _joined_if_md5(whole[:19], whole[19:], good) == whole
    assert _joined_if_md5(whole[:19], whole[19:] + b"5,6\n", good) is None and _joined_if_md5(b"other", whole[19:], good) is None
