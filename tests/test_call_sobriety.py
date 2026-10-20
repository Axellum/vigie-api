"""Proof of the call sobriety: a day of 1,000 clients on 50 points stays under the Open-Meteo quota.

The simulation (``tests/simulation.py``) plays one day with the scheduler on and its five default zones:
1,000 clients spread over 50 points, 20 each, polling every 30 minutes (a frame, or a full page for a few),
10 of the 50 points being harbours whose clients also ask for the marine conditions. Every client reconnects
within the first minute (the API has just started). The 50 points are at least 0.5 degree apart, so none of
them shares a grid cell: the figures do not count on the grids to save calls.

The upstream is simulated (``httpx.MockTransport``) and the clock is simulated: nothing touches the network
and nothing sleeps. Every request received by the simulated providers is counted per source, and the Open-Meteo
requests are weighed by the published rule (an independent implementation of it, in ``simulation.py``).

``BEFORE`` is the same simulation run against ``main`` as it was before this work (commit 1864e25), measured
once; the code it measured no longer exists, so the figures are recorded here.
"""

from __future__ import annotations

import pytest

from app.services.storage_wal import DEFAULT_WATCHLIST_SEED
from tests.helpers import SimClock
from tests.simulation import (
    DAY_S,
    OPEN_METEO_SOURCES,
    Scenario,
    Simulation,
    open_meteo_peaks,
    open_meteo_total,
)

# Upstream requests per source over the day, `main` at 1864e25 (30-minute polling, as SCENARIO below).
BEFORE_CALLS = {
    "open_meteo_hd": 17_212,
    "open_meteo_aqi": 7_182,
    "open_meteo_flood": 6_122,
    "open_meteo_marine": 2_216,
    "usgs_eq": 1_980,
    "emsc_seismic": 1_980,
    "gdacs_global": 676,
    "noaa_nws": 1_069,
    "usgs_volcano_gvp": 212,
    "noaa_swpc": 403,
    "rainviewer_radar": 662,
}
BEFORE_OPEN_METEO_WEIGHT = 46_501.6  # for a quota of 10,000 a day
BEFORE_OPEN_METEO_PEAK_HOUR = 2_305.4
BEFORE_OPEN_METEO_PEAK_MINUTE = 496.6

# The published free quota of Open-Meteo (https://open-meteo.com/en/terms) and the part the API lets through.
QUOTA_DAY, QUOTA_HOUR, QUOTA_MINUTE = 10_000, 5_000, 600
BUDGET_DAY = 0.9 * QUOTA_DAY

SCENARIO = Scenario()  # 1,000 clients, 50 points, polling every 30 minutes


def test_the_scenario_is_the_one_that_was_measured() -> None:
    assert (SCENARIO.clients, SCENARIO.points, SCENARIO.harbour_points) == (1000, 50, 10)
    assert SCENARIO.poll_period_s == 1800 and SCENARIO.startup_spread_s == 60
    assert SCENARIO.clients // SCENARIO.points == 20  # clients per point


@pytest.mark.asyncio
async def test_a_day_of_1000_clients_on_50_points_stays_under_the_open_meteo_quota(sim_clock: SimClock) -> None:
    simulation = Simulation(sim_clock, SCENARIO)
    result = await simulation.run()

    # The whole day was played, and nothing failed.
    assert result.failures == 0
    assert result.scheduler_cycles == DAY_S // 90
    assert result.requests_served == SCENARIO.clients * (1 + DAY_S // SCENARIO.poll_period_s)

    # The Open-Meteo quota, on the weighted calls: the day, the hour, the minute.
    calls, weight = open_meteo_total(result)
    peak_hour, peak_minute = open_meteo_peaks(result)
    print(f"\nOpen-Meteo: {calls} calls, weight {weight} (before: weight {BEFORE_OPEN_METEO_WEIGHT})")
    assert weight < QUOTA_DAY
    assert peak_hour < QUOTA_HOUR
    assert peak_minute < QUOTA_MINUTE
    # ...without the budget having to hold anything back: the cache never had to be served past its TTL.
    assert weight <= BUDGET_DAY
    assert sum(collector.budget_refusals for collector in simulation.aggregator.get_all_collectors()) == 0

    # Much less than before, source by source.
    for source, before in BEFORE_CALLS.items():
        assert result.calls[source] < before, f"{source}: {result.calls[source]} calls, {before} before"
    assert weight < 0.3 * BEFORE_OPEN_METEO_WEIGHT
    assert peak_minute < BEFORE_OPEN_METEO_PEAK_MINUTE and peak_hour < BEFORE_OPEN_METEO_PEAK_HOUR
    assert result.calls["met_norway_fallback"] == 0  # Open-Meteo never failed: the fallback was not asked
    # The official warnings: the scheduler asks the MeteoAlarm feeds of the countries of the watched zones only (Paris:
    # France, Reykjavik: Iceland; Tokyo and Pointe-a-Pitre have no source, San Francisco is the NWS answer the hazards
    # already use), and fetches no CAP document while no warning changes. See tests/test_vigilance_sobriety.py.
    assert 0 < result.calls["meteoalarm_atom"] <= 2 * (DAY_S // 180 + 1)
    assert result.calls.get("meteoalarm_cap", 0) == 0

    # The number of calls does not depend on the number of clients: a refresh happens at most once per
    # lifetime of a cache entry, so each source is bounded by (cells x refreshes per day), whatever the load.
    zones = len(DEFAULT_WATCHLIST_SEED)
    forecast_cells = zones + SCENARIO.points  # watched zones and client points
    refreshes = {
        "open_meteo_hd": forecast_cells * (DAY_S / 900 + 1) + forecast_cells * (DAY_S / 3600 + 1),
        "open_meteo_aqi": forecast_cells * (DAY_S / 3600 + 1),
        "open_meteo_flood": SCENARIO.points * (DAY_S / 21_600 + 1),
        "open_meteo_marine": SCENARIO.harbour_points * (DAY_S / 3600 + 1),
    }
    for source, bound in refreshes.items():
        assert result.calls[source] <= bound, f"{source}: {result.calls[source]} calls, bound {bound}"

    # The tsunami warning centres and the National Hurricane Center (tests/test_storm_sobriety.py has their quiet, storm and tsunami
    # days): each is asked at most once per lifetime of its cache entry (60 s and 300 s), and every ask after the first is a 304.
    tsunami_calls, nhc_calls = result.calls["tsunami_warning_centers"], result.calls["nhc_storms"]
    assert 960 < tsunami_calls <= DAY_S // 60 + 1 and 240 < nhc_calls <= DAY_S // 300 + 1
    assert simulation.upstream.not_modified["tsunami_warning_centers"] == tsunami_calls - 1
    assert simulation.upstream.not_modified["nhc_storms"] == nhc_calls - 1
    print(f"tsunami.gov: {tsunami_calls} requests, {simulation.upstream.bytes['tsunami_warning_centers'] / 1000:.0f} kB; NHC: {nhc_calls} requests, "
          f"{simulation.upstream.bytes['nhc_storms'] / 1000:.0f} kB")

    # What the API counted is what the providers received (the figures of GET /v1/providers are real).
    sim_clock.now = DAY_S - 1.0  # inside the 24 h window of the first requests
    for collector in simulation.aggregator.get_all_collectors():
        health = collector.get_health()
        assert health.calls_24h == result.calls[collector.provider_id], collector.provider_id
        assert health.weight_24h == pytest.approx(result.weight[collector.provider_id], abs=0.05), collector.provider_id
    quota = simulation.aggregator.open_meteo.get_health().quota
    assert quota is not None and quota.exhausted is False
    day_limit = next(limit for limit in quota.limits if limit.window_s == DAY_S)
    assert day_limit.used == pytest.approx(weight, abs=0.2)  # one budget for the four Open-Meteo APIs
    assert {source for source in OPEN_METEO_SOURCES} <= set(result.calls)

    # No request is left hanging.
    assert all(collector._inflight == {} for collector in simulation.aggregator.get_all_collectors())
