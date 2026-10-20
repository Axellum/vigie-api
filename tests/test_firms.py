"""The NASA FIRMS collector: the CSV it reads, the days it asks for, and what it does when FIRMS fails.

The rows are the recorded ones of ``tests/fixtures/firms`` (a real world read of 2026-10-20 for NOAA-20 and NOAA-21, a selection
of rows); the server is a simulated one (``tests/firms_samples.FirmsServer``) behind an ``httpx.MockTransport``. Nothing touches the network.
"""

from __future__ import annotations

import asyncio
from calendar import timegm
from datetime import datetime

import pytest

from app.collectors.base import CollectorUnavailableError
from app.collectors.budget import CallBudget, Limit, Quota
from app.collectors.firms import (
    ALLOWED_SOURCES,
    DEFAULT_REFRESH_MINUTES,
    DEFAULT_SOURCES,
    RETRY_S,
    SETTLE_S,
    WORLD_DAY_TRANSACTIONS,
    FIRMSCollector,
    HotspotTable,
    day_end_utc,
    load_settings,
    parse_csv,
    utc_dates,
)
from tests.firms_samples import (
    EVENING,
    FIXTURE_DAY,
    HEADER,
    KEY,
    N20,
    N21,
    FirmsServer,
    at,
    csv_for,
    csv_rows,
    fixture_rows,
)
from tests.helpers import SimClock

MIDNIGHT = timegm(datetime.strptime(FIXTURE_DAY, "%Y-%m-%d").timetuple())


def find(table: HotspotTable, lat: float, lon: float) -> int:
    """The row of a recorded detection, by its position."""
    for position in table.rows_between(round(lat * 1e5), round(lat * 1e5)):
        row = table.order[position]
        if table.lon[row] == round(lon * 1e5):
            return int(row)
    raise AssertionError(f"no detection at {lat}, {lon}")


# ------------------------------------------------------------------------------------------------ the CSV


def test_the_recorded_rows_are_read_with_the_fields_of_the_file() -> None:
    table, stats = parse_csv(csv_for(N20, FIXTURE_DAY), FIXTURE_DAY)
    assert stats.rows == len(fixture_rows(N20)) == 258 and stats.skipped == 0
    row = find(table, 28.85126, 33.07164)  # the first line of the recording
    assert table.acq[row] == MIDNIGHT + 60  # acq_time "1" is 00:01, not 01:00
    assert (table.satellite(row), table.instrument(row), table.version(row)) == ("N20", "VIIRS", "2.0NRT")
    assert table.conf[row] == 2 and table.dn[row] == 2  # nominal, night
    assert table.number(table.frp, row) == pytest.approx(1.42)
    assert table.number(table.ti4, row) == pytest.approx(314.28) and table.number(table.ti5, row) == pytest.approx(294.59)
    assert table.number(table.scan, row) == pytest.approx(0.41) and table.number(table.track, row) == pytest.approx(0.45)
    assert stats.latest_acq_utc == MIDNIGHT + 18 * 3600 + 9 * 60  # the latest recorded detection: acq_time 1809


def test_every_recorded_row_is_there_whatever_its_confidence_or_version() -> None:
    table, _ = parse_csv(csv_for(N21, FIXTURE_DAY), FIXTURE_DAY)
    rows = fixture_rows(N21)
    assert len(table) == len(rows)
    assert sorted(set(table.versions)) == ["2.0NRT", "2.0URT"] and table.satellites == ("N21",)
    assert sum(1 for code in table.conf if code == 1) == sum(1 for row in rows if row["confidence"] == "l")
    assert sum(1 for code in table.conf if code == 3) == sum(1 for row in rows if row["confidence"] == "h")
    positions = [table.lat[row] for row in table.order]
    assert positions == sorted(positions)  # the index lists the rows by latitude


@pytest.mark.parametrize(("hhmm", "seconds"), [(0, 0), (1, 60), (59, 59 * 60), (100, 3600), (949, 9 * 3600 + 49 * 60), (1809, 18 * 3600 + 9 * 60), (2359, 23 * 3600 + 59 * 60)])
def test_the_time_is_hhmm_without_a_leading_zero(hhmm: int, seconds: int) -> None:
    table, _ = parse_csv(csv_rows([(10.0, 20.0, hhmm, 1.0, "n")]), FIXTURE_DAY)
    assert table.acq[0] == MIDNIGHT + seconds


def test_a_row_without_a_readable_position_or_time_is_skipped_and_counted() -> None:
    body = (
        HEADER
        + "\n10.0,20.0,300,0.4,0.4,2026-10-20,330,N20,VIIRS,n,2.0NRT,290,5.5,D"  # good
        + "\nabc,20.0,300,0.4,0.4,2026-10-20,330,N20,VIIRS,n,2.0NRT,290,5.5,D"  # latitude is not a number
        + "\n95.0,20.0,300,0.4,0.4,2026-10-20,330,N20,VIIRS,n,2.0NRT,290,5.5,D"  # latitude beyond the poles
        + "\n10.0,181.0,300,0.4,0.4,2026-10-20,330,N20,VIIRS,n,2.0NRT,290,5.5,D"  # longitude beyond the globe
        + "\nnan,20.0,300,0.4,0.4,2026-10-20,330,N20,VIIRS,n,2.0NRT,290,5.5,D"  # not a finite position
        + "\n10.0,20.0,300,0.4,0.4,2026-10-20,2460,N20,VIIRS,n,2.0NRT,290,5.5,D"  # minute 60
        + "\n10.0,20.0,300,0.4,0.4,2026-10-20,2400,N20,VIIRS,n,2.0NRT,290,5.5,D"  # hour 24
        + "\n10.0,20.0,300,0.4,0.4,2026-10-20,soon,N20,VIIRS,n,2.0NRT,290,5.5,D"  # no time
        + "\n10.0,20.0,300,0.4,0.4,2026-10-19,330,N20,VIIRS,n,2.0NRT,290,5.5,D"  # another day: it belongs to that day's own read
        + "\n10.0,20.0\n"  # cut short
    ).encode()
    table, stats = parse_csv(body, FIXTURE_DAY)
    assert (stats.rows, stats.skipped) == (1, 9) and len(table) == 1


def test_a_value_the_file_does_not_give_is_unknown_not_zero() -> None:
    body = (HEADER + "\n10.0,20.0,,0.4,0.4,2026-10-20,330,N20,VIIRS,x,2.0NRT,,,D\n").encode()
    table, _ = parse_csv(body, FIXTURE_DAY)
    assert table.number(table.frp, 0) is None and table.number(table.ti4, 0) is None and table.number(table.ti5, 0) is None
    assert table.conf[0] == 0  # a confidence class that is not l, n, h is unknown


def test_a_file_with_fewer_columns_is_read_with_unknown_values() -> None:
    table, stats = parse_csv(b"latitude,longitude,acq_date,acq_time\n10.5,20.5,2026-10-20,330\n", FIXTURE_DAY)
    assert stats.rows == 1
    assert (table.satellite(0), table.instrument(0), table.version(0)) == (None, None, None)
    assert table.number(table.frp, 0) is None and table.conf[0] == 0 and table.dn[0] == 0


def test_a_header_and_no_row_is_a_valid_empty_day() -> None:
    table, stats = parse_csv((HEADER + "\n").encode(), FIXTURE_DAY)
    assert len(table) == 0 and stats.rows == 0 and stats.latest_acq_utc is None


@pytest.mark.parametrize(
    "body",
    [b"", b"Invalid MAP_KEY.", b"<html><body>Service unavailable</body></html>", b"latitude,longitude\n1,2\n", b"Exceeding transaction limit"],
)
def test_what_is_not_the_csv_is_refused_not_read_as_no_fire(body: bytes) -> None:
    with pytest.raises(ValueError):
        parse_csv(body, FIXTURE_DAY)


def test_the_index_by_latitude_finds_exactly_the_rows_of_a_band() -> None:
    table, _ = parse_csv(csv_for(N20, FIXTURE_DAY), FIXTURE_DAY)
    low, high = round(30 * 1e5), round(45 * 1e5)
    expected = sorted(i for i in range(len(table)) if low <= table.lat[i] <= high)
    assert sorted(table.order[k] for k in table.rows_between(low, high)) == expected and expected
    assert len(table.rows_between(round(89 * 1e5), round(90 * 1e5))) == 0


# ------------------------------------------------------------------------------------------------ settings


def test_without_a_key_the_source_is_off_and_says_why() -> None:
    settings = load_settings({})
    assert settings.key is None and settings.off_reason == "VIGIE_FIRMS_MAP_KEY is not set"
    assert load_settings({"VIGIE_FIRMS_MAP_KEY": "   "}).key is None


@pytest.mark.parametrize("value", ["bad key with spaces", "key/with/slashes", "../../etc", "abc", "k" * 65, "clé0123456789"])
def test_a_key_that_is_not_a_firms_key_turns_the_source_off_without_repeating_it(value: str) -> None:
    settings = load_settings({"VIGIE_FIRMS_MAP_KEY": value})
    assert settings.key is None and settings.off_reason is not None and value not in settings.off_reason


def test_a_key_is_taken_as_it_is_stripped() -> None:
    assert load_settings({"VIGIE_FIRMS_MAP_KEY": f"  {KEY}\n"}).key == KEY


def test_the_defaults_are_both_noaa_satellites_every_hour() -> None:
    settings = load_settings({"VIGIE_FIRMS_MAP_KEY": KEY})
    assert settings.sources == DEFAULT_SOURCES == (N20, N21) and settings.refresh_s == 3600
    assert FIRMSCollector(KEY).ttl_seconds == 3600 and DEFAULT_REFRESH_MINUTES == 60
    assert "VIIRS_SNPP_NRT" not in ALLOWED_SOURCES  # Suomi NPP stops on 2026-11-02


@pytest.mark.parametrize(
    ("sources", "expected"),
    [(N21, (N21,)), (f" {N20} , {N21} ", (N20, N21)), (f"{N20},{N20}", (N20,)), ("VIIRS_SNPP_NRT", DEFAULT_SOURCES), (f"{N20},MODIS_NRT", DEFAULT_SOURCES), ("", DEFAULT_SOURCES)],
)
def test_the_sources_come_from_the_environment_or_the_default(sources: str, expected: tuple[str, ...]) -> None:
    assert load_settings({"VIGIE_FIRMS_MAP_KEY": KEY, "VIGIE_FIRMS_SOURCES": sources}).sources == expected


@pytest.mark.parametrize(("minutes", "seconds"), [("10", 600), ("30", 1800), ("360", 21600), ("5", 3600), ("361", 3600), ("often", 3600), ("", 3600), ("-30", 3600)])
def test_the_refresh_is_ten_minutes_to_six_hours_else_the_default(minutes: str, seconds: int) -> None:
    assert load_settings({"VIGIE_FIRMS_MAP_KEY": KEY, "VIGIE_FIRMS_REFRESH_MINUTES": minutes}).refresh_s == seconds


def test_the_dates_are_those_of_the_utc_day() -> None:
    assert utc_dates(at("2026-10-20", "00:00")) == ("2026-10-20", "2026-10-19")
    assert utc_dates(at("2026-10-20", "23:59")) == ("2026-10-20", "2026-10-19")
    assert utc_dates(at("2026-03-01", "00:30")) == ("2026-03-01", "2026-02-28")
    assert day_end_utc("2026-10-20") == at("2026-10-21", "00:00")


# ------------------------------------------------------------------------------------------------ the days that are asked


def make(sim_clock: SimClock, start: float = EVENING, **kwargs: object) -> tuple[FIRMSCollector, FirmsServer]:
    sim_clock.EPOCH = start
    server = FirmsServer()
    kwargs.setdefault("refresh_s", 1800)  # the timelines below are written for a refresh every 30 minutes (the default is an hour)
    return FIRMSCollector(KEY, http_client=server.client(), **kwargs), server  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_the_first_refresh_reads_today_and_yesterday_of_each_source(sim_clock: SimClock) -> None:
    collector, server = make(sim_clock)
    data = await collector.fetch()
    assert server.dates() == [(N20, "2026-10-20"), (N20, "2026-10-19"), (N21, "2026-10-20"), (N21, "2026-10-19")]
    assert [(day.source, day.date) for day in data.days] == server.dates()
    assert data.failed == () and data.errors == () and data.total_rows() == 4 * 0 + 2 * (258 + 218)
    # the key is in the path of the request, as FIRMS wants it, and nowhere else
    first = server.requests[0]
    assert first.url.path == f"/api/area/csv/{KEY}/{N20}/world/1/2026-10-20" and first.url.query == b""
    assert first.url.host == "firms.modaps.eosdis.nasa.gov" and first.headers["accept"].startswith("text/csv")
    assert KEY not in str(first.headers)
    # every read is counted as the transactions FIRMS counts, against the published quota
    health = collector.get_health()
    assert health.calls_24h == 4 and health.weight_24h == 4 * WORLD_DAY_TRANSACTIONS == 144
    assert health.quota is not None and [(limit.window_s, limit.limit) for limit in health.quota.limits] == [(600, 5000)]
    assert health.quota.source_url == "https://firms.modaps.eosdis.nasa.gov/api/area/"


@pytest.mark.asyncio
async def test_the_next_refresh_reads_today_only_once_yesterday_has_settled(sim_clock: SimClock) -> None:
    collector, server = make(sim_clock)  # 18:12 UTC: yesterday ended 18 hours ago
    first = await collector.fetch()
    sim_clock.advance(1801)  # the refresh is the lifetime of the entry: 30 minutes
    second = await collector.fetch()
    assert server.dates()[4:] == [(N20, "2026-10-20"), (N21, "2026-10-20")]
    kept = {(day.source, day.date): day for day in first.days}
    for day in second.days:
        if day.date == "2026-10-19":
            assert day is kept[(day.source, day.date)]  # the same table, not read again
        else:
            assert day is not kept[(day.source, day.date)] and day.read_utc == int(EVENING) + 1801


@pytest.mark.asyncio
async def test_a_refresh_inside_its_lifetime_reads_nothing(sim_clock: SimClock) -> None:
    collector, server = make(sim_clock)
    await collector.fetch()
    sim_clock.advance(1799)
    await collector.fetch()
    await asyncio.gather(*(collector.fetch() for _ in range(5)))
    assert server.count == 4


@pytest.mark.asyncio
async def test_concurrent_callers_share_one_set_of_requests(sim_clock: SimClock) -> None:
    collector, server = make(sim_clock)
    results = await asyncio.gather(*(collector.fetch() for _ in range(8)))
    assert server.count == 4 and all(result is results[0] for result in results)


@pytest.mark.asyncio
async def test_yesterday_is_read_again_until_it_has_settled_then_once_more(sim_clock: SimClock) -> None:
    """The NRT data of a day keeps arriving until about 3 hours after it ends (settled at 04:00 UTC with a margin)."""
    collector, server = make(sim_clock, at("2026-10-20", "02:00"))
    await collector.fetch()  # 02:00: yesterday is far from complete
    sim_clock.advance(6600)  # 03:50: still being completed, so at the rhythm of the refresh
    await collector.fetch()
    sim_clock.advance(1801)  # 04:20: settled since 04:00 and the last reading was before that: one final read
    await collector.fetch()
    for _ in range(5):  # ...and no more from then on, all day
        sim_clock.advance(1801)
        await collector.fetch()
    assert [read for read in server.dates(N20) if read[1] == "2026-10-19"] == [(N20, "2026-10-19")] * 3
    assert SETTLE_S == 4 * 3600


@pytest.mark.asyncio
async def test_yesterday_is_read_at_each_refresh_while_it_is_not_settled(sim_clock: SimClock) -> None:
    collector, server = make(sim_clock, at("2026-10-20", "00:30"))
    for _ in range(4):  # 00:30, 01:00, 01:30, 02:00
        await collector.fetch()
        sim_clock.advance(1801)
    assert len([read for read in server.dates(N20) if read[1] == "2026-10-19"]) == 4


@pytest.mark.asyncio
async def test_at_midnight_today_becomes_yesterday_and_the_oldest_day_is_dropped(sim_clock: SimClock) -> None:
    collector, server = make(sim_clock, at("2026-10-20", "23:50"))
    before = await collector.fetch()
    assert {day.date for day in before.days} == {"2026-10-20", "2026-10-19"}
    sim_clock.advance(1801)  # 00:20 on 2026-10-21
    after = await collector.fetch()
    assert {day.date for day in after.days} == {"2026-10-21", "2026-10-20"}
    assert server.dates(N20)[2:] == [(N20, "2026-10-21"), (N20, "2026-10-20")]  # the day that just ended is read again: it was not complete


@pytest.mark.asyncio
async def test_one_source_is_enough(sim_clock: SimClock) -> None:
    collector, server = make(sim_clock, sources=(N21,))
    data = await collector.fetch()
    assert server.dates() == [(N21, "2026-10-20"), (N21, "2026-10-19")] and data.sources() == (N21,)


# ------------------------------------------------------------------------------------------------ when FIRMS fails


@pytest.mark.asyncio
async def test_a_source_that_fails_is_reported_and_the_other_is_kept(sim_clock: SimClock) -> None:
    collector, server = make(sim_clock)
    server.failing = {N21}
    data = await collector.fetch()
    assert data.failed == (N21,) and data.sources() == (N20,)
    assert data.errors[0].startswith(f"{N21} 2026-10-20: ConnectError")
    assert collector.last_error is not None and N21 in collector.last_error  # GET /v1/providers says it
    assert collector.circuit_state.value == "CLOSED" and collector.consecutive_failures == 0  # one source down is not the provider down
    # it is asked again after five minutes, and the source that works is left alone
    sim_clock.advance(RETRY_S + 1)
    server.failing = set()
    before = server.count
    data = await collector.fetch()
    assert server.dates()[before:] == [(N21, "2026-10-20"), (N21, "2026-10-19")]
    assert data.failed == () and data.sources() == (N20, N21) and collector.last_error is None


@pytest.mark.asyncio
async def test_a_source_that_does_not_answer_is_not_asked_for_its_other_day_in_the_same_refresh(sim_clock: SimClock) -> None:
    collector, server = make(sim_clock)
    server.failing = {N20}
    await collector.fetch()
    assert server.dates(N20) == [(N20, "2026-10-20")] * 2  # the connect phase is tried twice, and only for today


@pytest.mark.asyncio
async def test_a_source_that_fails_later_keeps_its_previous_reading(sim_clock: SimClock) -> None:
    collector, server = make(sim_clock)
    first = await collector.fetch()
    sim_clock.advance(1801)
    server.failing = {N20}
    second = await collector.fetch()
    assert second.failed == (N20,)
    old = {(day.source, day.date): day for day in first.days}
    for day in second.days:
        if day.source == N20:
            assert day is old[(day.source, day.date)]  # both days of N20 are the previous readings
    assert {(day.source, day.date) for day in second.days} == set(old)


@pytest.mark.asyncio
async def test_when_every_read_fails_the_last_set_is_served_and_the_failure_counted(sim_clock: SimClock) -> None:
    collector, server = make(sim_clock)
    good = await collector.fetch()
    sim_clock.advance(1801)
    server.failing = {N20, N21}
    stale = await collector.fetch_ex()
    assert stale.value is good and stale.stale is True
    assert collector.consecutive_failures == 1 and collector.last_error is not None and "ConnectError" in collector.last_error


@pytest.mark.asyncio
async def test_with_nothing_read_yet_a_failure_is_an_error_not_an_empty_answer(sim_clock: SimClock) -> None:
    collector, server = make(sim_clock)
    server.failing = {N20, N21}
    with pytest.raises(CollectorUnavailableError):
        await collector.fetch()
    assert collector.cached() == (None, False)


@pytest.mark.asyncio
async def test_the_circuit_opens_after_three_failures_and_closes_with_a_success(sim_clock: SimClock) -> None:
    collector, server = make(sim_clock)
    server.failing = {N20, N21}
    for _ in range(3):
        with pytest.raises(CollectorUnavailableError):
            await collector.fetch()
    assert collector.circuit_state.value == "OPEN"
    count = server.count
    with pytest.raises(CollectorUnavailableError, match="Circuit breaker OPEN"):
        await collector.fetch()
    assert server.count == count  # an open circuit asks nothing
    server.failing = set()
    sim_clock.advance(601)  # the recovery timeout is ten minutes
    assert (await collector.fetch()).failed == ()
    assert collector.circuit_state.value == "CLOSED"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "body", "expected"),
    [(500, b"oops", "HTTP 500"), (429, b"slow down", "HTTP 429"), (403, b"", "HTTP 403"), (200, b"Invalid MAP_KEY.", "not a FIRMS CSV"), (200, b"<html>maintenance</html>", "not a FIRMS CSV"), (200, b"", "empty answer")],
)
async def test_an_answer_that_is_not_the_file_is_a_failure_with_its_reason(sim_clock: SimClock, code: int, body: bytes, expected: str) -> None:
    collector, server = make(sim_clock, sources=(N20,))
    server.status[N20] = (code, body)
    with pytest.raises(CollectorUnavailableError, match=expected):
        await collector.fetch()


def first_rows(source: str, date: str, rows: int) -> bytes:
    """The file of ``date`` cut after ``rows`` rows (a header and that many lines)."""
    return b"\n".join(csv_for(source, date).split(b"\n")[: 1 + rows]) + b"\n"


@pytest.mark.asyncio
async def test_an_answer_that_was_cut_short_does_not_replace_the_reading_it_would_shrink(sim_clock: SimClock) -> None:
    """FIRMS loses connections: a cut answer that ends cleanly is a valid CSV with fewer rows. The file of a day only grows."""
    collector, server = make(sim_clock)
    first = await collector.fetch()
    sim_clock.advance(1801)
    server.answers[(N20, "2026-10-20")] = first_rows(N20, "2026-10-20", 20)
    second = await collector.fetch()
    assert second.failed == (N20,) and "20 rows after a reading of 258, the previous one is kept" in second.errors[0]
    old = {(day.source, day.date): day for day in first.days}
    assert {(day.source, day.date): day for day in second.days}[(N20, "2026-10-20")] is old[(N20, "2026-10-20")]
    sim_clock.advance(301)  # asked again after five minutes, and a complete answer is taken
    del server.answers[(N20, "2026-10-20")]
    third = await collector.fetch()
    assert third.failed == () and len({(day.source, day.date): day for day in third.days}[(N20, "2026-10-20")].table) == 258


@pytest.mark.asyncio
async def test_when_the_only_source_is_cut_the_previous_reading_is_served_as_stale(sim_clock: SimClock) -> None:
    collector, server = make(sim_clock, sources=(N20,))
    first = await collector.fetch()
    sim_clock.advance(1801)
    server.answers[(N20, "2026-10-20")] = first_rows(N20, "2026-10-20", 20)
    answer = await collector.fetch_ex()
    assert answer.stale is True and answer.value is first
    assert collector.last_error is not None and "20 rows after a reading of 258" in collector.last_error


@pytest.mark.asyncio
async def test_a_small_shrink_is_not_taken_for_a_cut_answer(sim_clock: SimClock) -> None:
    collector, server = make(sim_clock, sources=(N20,))
    await collector.fetch()
    sim_clock.advance(1801)
    server.answers[(N20, "2026-10-20")] = first_rows(N20, "2026-10-20", 240)  # 240 rows after 258: more than 90 %
    data = await collector.fetch()
    assert data.failed == () and len({day.date: day for day in data.days}["2026-10-20"].table) == 240


@pytest.mark.asyncio
async def test_a_day_with_no_detection_is_read_as_no_detection(sim_clock: SimClock) -> None:
    collector, server = make(sim_clock, sources=(N20,))
    server.answers[(N20, "2026-10-20")] = (HEADER + "\n").encode()
    data = await collector.fetch()
    assert data.failed == () and [len(day.table) for day in data.days] == [0, 258]


@pytest.mark.asyncio
async def test_the_budget_holds_the_reads_back_before_the_quota(sim_clock: SimClock) -> None:
    collector, server = make(sim_clock, sources=(N20,))
    collector.budget = CallBudget(Quota("nasa-firms", (Limit(600, 30),), "https://example.invalid/"), 0.9)  # 27: not even one world read
    with pytest.raises(CollectorUnavailableError, match="budget"):
        await collector.fetch()
    assert server.count == 0 and collector.budget_refusals == 1
    assert collector.consecutive_failures == 0  # a refused request is not a failure of the provider


@pytest.mark.asyncio
async def test_a_budget_that_runs_out_midway_keeps_what_was_read(sim_clock: SimClock) -> None:
    collector, server = make(sim_clock, sources=(N20,))
    collector.budget = CallBudget(Quota("nasa-firms", (Limit(600, 60),), "https://example.invalid/"), 0.9)  # 54: one world read, not two
    data = await collector.fetch()
    assert server.dates() == [(N20, "2026-10-20")] and [day.date for day in data.days] == ["2026-10-20"]
    assert data.failed == (N20,) and data.errors == ("VIIRS_NOAA20_NRT 2026-10-19: call budget used up",)
    assert collector.budget_refusals == 1 and collector.consecutive_failures == 0


@pytest.mark.asyncio
async def test_the_set_is_fresh_for_the_refresh_then_stale_but_still_there(sim_clock: SimClock) -> None:
    collector, _ = make(sim_clock, refresh_s=600)
    assert collector.cached() == (None, False)
    data = await collector.fetch()
    assert collector.cached() == (data, True)
    sim_clock.advance(601)
    assert collector.cached() == (data, False)


def test_the_rows_are_kept_in_a_few_dozen_bytes_each() -> None:
    table, _ = parse_csv(csv_for(N20, FIXTURE_DAY), FIXTURE_DAY)
    columns = [table.lat, table.lon, table.acq, table.frp, table.ti4, table.ti5, table.scan, table.track, table.conf, table.dn, table.sat, table.inst, table.ver, table.order]
    assert sum(column.itemsize for column in columns) == 41  # 3 ints, 5 floats, 5 bytes of codes and the index: measured 43 bytes a row with the slack
