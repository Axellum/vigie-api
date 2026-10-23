"""What the NASA FIRMS fires cost in requests and bytes over one simulated day.

The day is played by ``tests/simulation.py``: 1,000 clients on 50 points ask every 30 minutes (a frame, or a full page for a few: each
asks the fires near its point), the scheduler runs at its period of 90 seconds, the clock is simulated, and every request the simulated FIRMS
receives is counted, with the bytes the collector really pulled. FIRMS writes each file once an hour; the collector asks it at its refresh (60
minutes by default) and reads only what changed (see ``simulation.py`` for the measurements the model comes from). The figures of
``docs/data-sources.md`` are these.

What the tests show: the number of requests depends on the refresh and not on the number of clients; nobody asking means no request; a refresh
shorter than the rhythm of FIRMS costs more requests and **no more bytes**; the bytes of a day are those of the first reading, of the day that
rolls over (the file shrinks: it is read whole) and of what was added in between, as an independent computation over the same model says; a FIRMS
that does not honour ranges would cost ten times as much; a FIRMS that is down is not hammered.
"""

from __future__ import annotations

import pytest

from app.collectors.firms import BACKOFF_S, DEFAULT_REFRESH_MINUTES, DEFAULT_WINDOW_HOURS
from tests.firms_samples import N20, N21
from tests.helpers import SimClock
from tests.simulation import (
    DAY_S,
    FIRMS_HEAD_BYTES,
    FirmsRequest,
    Scenario,
    Simulation,
    firms_file_bytes,
    firms_publication,
)

NO_CLIENT = Scenario(clients=0, points=50)
FEW_CLIENTS = Scenario(clients=100, points=50)
FIRMS = "nasa_firms"
SOURCES = (N20, N21)
DEFAULT_REFRESH_S = DEFAULT_REFRESH_MINUTES * 60


def bursts(reads: list[FirmsRequest]) -> list[float]:
    """The moments at which a refresh started (the requests of one refresh follow each other within a minute)."""
    seconds = sorted(read.second for read in reads)
    return [second for index, second in enumerate(seconds) if index == 0 or second - seconds[index - 1] > 60]


def expected_bytes(simulation: Simulation, window: int) -> float:
    """The bytes of the day by an independent computation over the model: what the files weigh when they are read, and what is added to them.

    The first reading of a source is its whole file; a later one costs nothing when FIRMS has not written a file since, what was added when
    the file grew, and the whole file when it got shorter (the oldest UTC day left it at midnight); and every request costs its head.
    """
    reads = simulation.upstream.firms_reads
    total = float(FIRMS_HEAD_BYTES * len(reads))
    for source in SOURCES:
        asked = sorted({read.second for read in reads if read.product == source and not read.ranged})  # a refresh's first request: the check
        previous = None
        for second in asked:
            published = firms_publication(source, simulation.clock.EPOCH + second)
            size = firms_file_bytes(source, window, published)
            if previous is None:
                total += size
            elif published != previous[0]:
                total += size - previous[1] if size > previous[1] else size
            previous = (published, size)
    return total


def check_a_day(simulation: Simulation, refresh_s: int, window: int) -> tuple[int, int, float]:
    """The checks that hold for any day and any refresh; returns the requests, the ranged ones and the megabytes."""
    up = simulation.upstream
    reads = up.firms_reads
    calls = len(reads)
    firms = simulation.aggregator.firms
    assert firms is not None and firms.window_hours == window
    simulation.clock.now = DAY_S - 1.0  # inside the 24 hours window of the first requests
    health = firms.get_health()
    assert health.calls_24h == calls and health.consecutive_failures == 0 and health.budget_refusals == 0
    assert health.weight_24h == 0 and health.quota is not None and health.quota.exhausted is False  # a static file costs no transaction of the key
    # The first request starts the reading: the file of each source (the clients arrive within the first minute)...
    seconds = sorted(read.second for read in reads)
    assert seconds[0] < 60 and seconds[1] - seconds[0] < 60 and {read.product for read in reads[:2]} == set(SOURCES)
    assert all(read.window == window for read in reads)
    assert not any(read.ranged for read in reads[:2]) and not any(read.conditional for read in reads[:2])  # nothing is held yet
    # ...then a refresh comes a lifetime after the one before, never sooner.
    starts = bursts(reads)
    assert all(later - earlier >= refresh_s - 60 for earlier, later in zip(starts, starts[1:], strict=False)), starts
    # A refresh asks each source for at most: the check, the added bytes and, if they were not the file's, the whole file.
    assert calls <= 2 * (1 + 3 * len(starts)), (calls, len(starts))
    # The bytes are those of the model: the first reading, the whole file after midnight, what was added between.
    megabytes = up.bytes[FIRMS] / 1e6
    assert megabytes == pytest.approx(expected_bytes(simulation, window) / 1e6, rel=0.03)
    return calls, sum(1 for read in reads if read.ranged), megabytes


@pytest.mark.asyncio
async def test_nobody_asking_means_no_request(sim_clock: SimClock) -> None:
    """The fires are read when a visitor needs them, not on a timer: the scheduler alone never asks FIRMS."""
    simulation = Simulation(sim_clock, NO_CLIENT)
    result = await simulation.run()
    assert result.failures == 0 and result.calls[FIRMS] == 0 and simulation.upstream.bytes[FIRMS] == 0
    assert simulation.aggregator.firms is not None and simulation.aggregator.firms.get_health().calls_24h == 0


@pytest.mark.asyncio
async def test_a_day_of_1000_clients_costs_the_same_as_a_day_of_100(sim_clock: SimClock) -> None:
    large = Simulation(sim_clock, Scenario())
    result = await large.run()
    assert result.failures == 0 and result.calls[FIRMS] == len(large.upstream.firms_reads)
    calls, ranged, megabytes = check_a_day(large, DEFAULT_REFRESH_S, DEFAULT_WINDOW_HOURS)
    print(
        f"\nNASA FIRMS, files of {DEFAULT_WINDOW_HOURS} hours, refresh {DEFAULT_REFRESH_MINUTES} min, a day of 1,000 clients: {calls} requests "
        f"({ranged} for the added bytes only), no transaction of a key, {megabytes:.1f} MB"
    )
    assert 20 < ranged < calls  # most refreshes find a new file and read what was added to it
    sim_clock.now = 0.0
    small = Simulation(sim_clock, FEW_CLIENTS)
    await small.run()
    assert len(small.upstream.firms_reads) == calls  # ten times fewer clients, the same requests
    assert small.upstream.bytes[FIRMS] / 1e6 == pytest.approx(megabytes, rel=0.02)  # a few seconds apart, so a few bytes apart


@pytest.mark.asyncio
async def test_files_of_24_hours_cost_less_than_files_of_48(sim_clock: SimClock) -> None:
    narrow = Simulation(sim_clock, FEW_CLIENTS, firms_window_hours=24)
    await narrow.run()
    calls, _, narrow_mb = check_a_day(narrow, DEFAULT_REFRESH_S, 24)
    sim_clock.now = 0.0
    wide = Simulation(sim_clock, FEW_CLIENTS, firms_window_hours=48)
    await wide.run()
    _, _, wide_mb = check_a_day(wide, DEFAULT_REFRESH_S, 48)
    print(f"\nNASA FIRMS, a day: files of 24 hours {narrow_mb:.1f} MB ({calls} requests), files of 48 hours {wide_mb:.1f} MB")
    assert narrow_mb < wide_mb < 2 * narrow_mb  # what is added is the same; the first reading and the one after midnight are longer


@pytest.mark.asyncio
async def test_a_refresh_every_thirty_minutes_costs_more_requests_and_no_more_bytes(sim_clock: SimClock) -> None:
    """FIRMS writes a file once an hour: a check that finds the one it holds is a request and its head, and nothing else."""
    hourly = Simulation(sim_clock, FEW_CLIENTS)
    await hourly.run()
    hourly_calls, _, hourly_mb = check_a_day(hourly, DEFAULT_REFRESH_S, DEFAULT_WINDOW_HOURS)
    sim_clock.now = 0.0
    half = Simulation(sim_clock, FEW_CLIENTS, firms_refresh_s=1800)
    await half.run()
    calls, _, megabytes = check_a_day(half, 1800, DEFAULT_WINDOW_HOURS)
    print(f"\nNASA FIRMS, refresh 30 min: {calls} requests, {megabytes:.1f} MB; refresh 60 min: {hourly_calls} requests, {hourly_mb:.1f} MB")
    assert calls > 1.4 * hourly_calls  # the checks that find nothing new
    assert megabytes == pytest.approx(hourly_mb, rel=0.02)  # ...and no byte more


@pytest.mark.asyncio
async def test_a_firms_that_does_not_honour_ranges_costs_the_whole_file_each_hour(sim_clock: SimClock) -> None:
    """What the added bytes save: the same day with a server that answers every range with the whole file."""
    with_ranges = Simulation(sim_clock, FEW_CLIENTS)
    await with_ranges.run()
    _, _, ranged_mb = check_a_day(with_ranges, DEFAULT_REFRESH_S, DEFAULT_WINDOW_HOURS)
    sim_clock.now = 0.0
    without = Simulation(sim_clock, FEW_CLIENTS)
    without.upstream.firms_ranges = False
    await without.run()
    whole_mb = without.upstream.bytes[FIRMS] / 1e6
    print(f"\nNASA FIRMS, a day: added bytes only {ranged_mb:.1f} MB; every new file read whole {whole_mb:.1f} MB")
    assert whole_mb > 8 * ranged_mb
    firms = without.aggregator.firms
    assert firms is not None and firms.consecutive_failures == 0 and not firms.failing()  # a server without ranges is read, not refused


@pytest.mark.asyncio
async def test_a_firms_that_is_down_all_day_is_not_hammered(sim_clock: SimClock) -> None:
    """Every answer is a 503: each source is asked after 1, 2, 5, then every 10 minutes, and the clients are served without fires."""
    simulation = Simulation(sim_clock, FEW_CLIENTS)
    simulation.upstream.firms_down = True
    result = await simulation.run()
    assert result.failures == 0  # the API answers all the same
    up = simulation.upstream
    calls = result.calls[FIRMS]
    attempts = len(BACKOFF_S) + DAY_S // int(BACKOFF_S[-1]) + 1
    assert calls <= len(SOURCES) * attempts, calls  # at most the rhythm of the longest wait, for each source
    assert up.bytes[FIRMS] == 0 and not any(read.ranged for read in up.firms_reads)
    firms = simulation.aggregator.firms
    assert firms is not None and firms.last_error is not None and "HTTP 503" in firms.last_error and firms.circuit_state.value == "CLOSED"
    assert firms.get_health().weight_24h == 0
    print(f"\nNASA FIRMS down all day: {calls} requests (a head each), no transaction of a key")
