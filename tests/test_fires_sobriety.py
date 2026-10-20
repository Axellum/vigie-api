"""What the NASA FIRMS fires cost in requests, transactions and bytes over one simulated day.

The day is played by ``tests/simulation.py``: 1,000 clients on 50 points ask every 30 minutes (a frame, or a full page for a few: each
asks the fires near its point), the scheduler runs at its period of 90 seconds, the clock is simulated, and every request the simulated FIRMS
receives is counted. A world read is weighed 36 transactions of the key and sized as the file is at that moment (see ``simulation.py``
for the measurements it comes from). The figures of ``docs/data-sources.md`` are these.

FIRMS publishes a limit of 5,000 transactions per 10 minutes per key. The API's refresh (an hour by default) is far below it; the proof is
that the number of reads depends on the lifetime of the cache entry and on the days it holds, not on the number of clients, that nobody
asking means no read, and that a FIRMS that is down is not hammered.
"""

from __future__ import annotations

import pytest

from app.collectors.firms import DEFAULT_REFRESH_MINUTES, SETTLE_S
from tests.firms_samples import N20, N21
from tests.helpers import SimClock
from tests.simulation import (
    DAY_S,
    FIRMS_DAY_BYTES,
    FIRMS_WORLD_DAY_TRANSACTIONS,
    Scenario,
    Simulation,
)

NO_CLIENT = Scenario(clients=0, points=50)
FEW_CLIENTS = Scenario(clients=100, points=50)
FIRMS = "nasa_firms"
QUOTA, WINDOW_S = 5000, 600
DEFAULT_REFRESH_S = DEFAULT_REFRESH_MINUTES * 60
MIDNIGHT = 22_400  # second of the simulation at which the day of the start ends (it starts at 17:46:40 UTC)


def peak_transactions(reads: list[tuple[float, str, str]]) -> float:
    """The most transactions in any 10 minutes (the window of the published limit)."""
    times = sorted(second for second, _, _ in reads)
    best = 0.0
    for index, start in enumerate(times):
        inside = [second for second in times[index:] if second < start + WINDOW_S]
        best = max(best, len(inside) * FIRMS_WORLD_DAY_TRANSACTIONS)
    return best


def reads_bound(refresh_s: int) -> int:
    """The most reads a day can hold: the first set (4), a refresh per lifetime of the entry that reads the day in progress of each satellite,
    the day that just ended read again at each refresh until it has settled, and once more."""
    refreshes = DAY_S // refresh_s + 1
    ending_refreshes = SETTLE_S // refresh_s + 1
    return 4 + 2 * refreshes + 2 * ending_refreshes + 2


def bursts(reads: list[tuple[float, str, str]]) -> list[float]:
    """The moments at which a refresh started (the reads of one refresh follow each other within a minute)."""
    seconds = sorted(second for second, _, _ in reads)
    return [second for index, second in enumerate(seconds) if index == 0 or second - seconds[index - 1] > 60]


def check_a_day(simulation: Simulation, refresh_s: int) -> tuple[int, float, float, float]:
    """The checks that hold for any day and any refresh; returns the reads, the peak, the megabytes and their upper bound."""
    up = simulation.upstream
    reads = up.firms_reads
    calls = len(reads)
    assert simulation.aggregator.firms is not None
    simulation.clock.now = DAY_S - 1.0  # inside the 24 hours window of the first requests
    health = simulation.aggregator.firms.get_health()
    assert health.calls_24h == calls and health.consecutive_failures == 0 and health.budget_refusals == 0
    assert health.quota is not None and health.quota.exhausted is False
    assert health.weight_24h == calls * FIRMS_WORLD_DAY_TRANSACTIONS
    assert calls <= reads_bound(refresh_s), f"{calls} reads, bound {reads_bound(refresh_s)}"
    # The first request starts the reading: both days of both satellites (the start is at 17:46 UTC, yesterday has settled)...
    seconds = sorted(second for second, _, _ in reads)
    assert seconds[0] < 60 and seconds[3] - seconds[0] < 60  # (the clients arrive within the first minute)
    assert {date for _, _, date in reads[:4]} == {"2026-10-14", "2026-10-13"}
    # ...then a refresh comes a lifetime after the one before, never sooner.
    starts = bursts(reads)
    assert all(later - earlier >= refresh_s for earlier, later in zip(starts, starts[1:], strict=False)), starts
    # The day of the start ends at midnight UTC: it is read at each refresh until it has settled (4 hours later), once more, and never again.
    settled = MIDNIGHT + SETTLE_S
    after = [second for second, _, date in reads if date == "2026-10-14" and second > MIDNIGHT]
    assert 2 * (SETTLE_S // refresh_s) <= len(after) <= 2 * (SETTLE_S // refresh_s + 2), len(after)
    assert settled <= max(after) <= settled + refresh_s + 60, max(after)
    # The published limit: the most transactions in any 10 minutes is far below 5,000 (and below the 4,500 the budget lets through).
    peak = peak_transactions(reads)
    assert peak <= 4 * FIRMS_WORLD_DAY_TRANSACTIONS < 0.1 * QUOTA, peak
    # The bytes: the upper bound is every read at the size of a full day; the estimate follows the size of the file at the time of each read.
    upper = sum(FIRMS_DAY_BYTES[product] for _, product, _ in reads) / 1e6
    assert 0 < up.bytes[FIRMS] / 1e6 <= upper
    return calls, peak, up.bytes[FIRMS] / 1e6, upper


@pytest.mark.asyncio
async def test_nobody_asking_means_no_read(sim_clock: SimClock) -> None:
    """The fires are read when a visitor needs them, not on a timer: the scheduler alone never asks FIRMS."""
    simulation = Simulation(sim_clock, NO_CLIENT)
    result = await simulation.run()
    assert result.failures == 0 and result.calls[FIRMS] == 0 and simulation.upstream.bytes[FIRMS] == 0
    assert simulation.aggregator.firms is not None and simulation.aggregator.firms.get_health().calls_24h == 0


@pytest.mark.asyncio
async def test_a_day_of_1000_clients_costs_the_same_reads_as_a_day_of_100(sim_clock: SimClock) -> None:
    large = Simulation(sim_clock, Scenario())
    result = await large.run()
    assert result.failures == 0 and result.calls[FIRMS] == len(large.upstream.firms_reads)
    calls, peak, megabytes, upper = check_a_day(large, DEFAULT_REFRESH_S)
    print(
        f"\nNASA FIRMS, refresh {DEFAULT_REFRESH_MINUTES} min, a day of 1,000 clients: {calls} world reads (bound {reads_bound(DEFAULT_REFRESH_S)}), "
        f"{calls * FIRMS_WORLD_DAY_TRANSACTIONS:.0f} transactions, peak {peak:.0f} in 10 minutes (limit {QUOTA}), "
        f"{megabytes:.0f} MB estimated (at most {upper:.0f} MB)"
    )
    sim_clock.now = 0.0
    small = Simulation(sim_clock, FEW_CLIENTS)
    await small.run()
    assert len(small.upstream.firms_reads) == calls  # ten times fewer clients, the same reads
    assert small.upstream.bytes[FIRMS] / 1e6 == pytest.approx(megabytes, rel=0.02)  # a few seconds apart, so a few bytes apart


@pytest.mark.asyncio
async def test_a_refresh_every_thirty_minutes_costs_twice_as_much(sim_clock: SimClock) -> None:
    simulation = Simulation(sim_clock, FEW_CLIENTS, firms_refresh_s=1800)
    result = await simulation.run()
    assert result.failures == 0
    calls, peak, megabytes, upper = check_a_day(simulation, 1800)
    print(
        f"\nNASA FIRMS, refresh 30 min: {calls} world reads (bound {reads_bound(1800)}), {calls * FIRMS_WORLD_DAY_TRANSACTIONS:.0f} transactions, "
        f"peak {peak:.0f} in 10 minutes, {megabytes:.0f} MB estimated (at most {upper:.0f} MB)"
    )
    assert calls > 100


@pytest.mark.asyncio
async def test_a_firms_that_is_down_all_day_is_not_hammered(sim_clock: SimClock) -> None:
    """Every answer is a 503: the circuit opens after three failures and is tried again every ten minutes, the clients are served without fires."""
    simulation = Simulation(sim_clock, FEW_CLIENTS)
    simulation.upstream.firms_down = True
    result = await simulation.run()
    assert result.failures == 0  # the API answers all the same
    up = simulation.upstream
    calls = result.calls[FIRMS]
    # a refresh asks each satellite once (a 503 is not retried); the circuit allows three, then one every 10 minutes
    assert calls <= 2 * (3 + DAY_S // 600 + 2), calls
    # three refreshes in the first minute (the clients arrive), then the circuit is open: 6 requests in 10 minutes at most, far below 5,000
    assert peak_transactions(up.firms_reads) <= 6 * FIRMS_WORLD_DAY_TRANSACTIONS
    assert up.bytes[FIRMS] == 0
    firms = simulation.aggregator.firms
    assert firms is not None and firms.last_error is not None and "HTTP 503" in firms.last_error
    print(f"\nNASA FIRMS down all day: {calls} requests, {calls * FIRMS_WORLD_DAY_TRANSACTIONS:.0f} transactions, peak {peak_transactions(up.firms_reads):.0f} in 10 minutes")


def test_the_sizes_of_a_day_are_known_for_both_satellites() -> None:
    assert set(FIRMS_DAY_BYTES) == {N20, N21}
