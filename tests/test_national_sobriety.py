"""What the national sources cost in requests over one simulated day.

The day is played by ``tests/simulation.py``: the scheduler of the application runs with its period of 90 seconds on the
watched zones, the clock is simulated, and every request the providers would receive is counted per source. The answers are
the recordings of 2026-10-20 (the DWD and MetService ones send their validators and answer ``304`` to a repeated request).

Two things are proved: (1) a zone, a followed point or a client in Germany, Canada or New Zealand costs one request per cache
lifetime of its source, whatever the number of clients; (2) the five default zones cost nothing at the new sources (Paris adds
Vigicrues, which is one request for all of France). The figures in ``docs/data-sources.md`` are these.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any, Awaitable, Callable

import pytest

from tests import vigilance_samples as samples
from tests.helpers import SimClock
from tests.simulation import DAY_S, Scenario, Simulation

NO_CLIENT = Scenario(clients=0, points=50)


def zone(index: int, name: str, point: tuple[float, float]) -> dict[str, Any]:
    return {"id": f"z{index}", "name": name, "lat": point[0], "lon": point[1], "utc_offset_s": 0, "alert_radius_km": 500.0, "is_marine": False}


def calm_flow() -> bytes:
    """The recorded flow with every section green: a day without flood."""
    payload = json.loads(samples.national_fixture("vigicrues", "InfoVigiCru_subset.geojson"))
    for feature in payload["features"]:
        feature["properties"]["NivInfViCr"] = 1
    return json.dumps(payload).encode()


async def one_day(sim_clock: SimClock, zones: list[dict[str, Any]] | None = None, bodies: dict[str, bytes] | None = None) -> Simulation:
    simulation = Simulation(sim_clock, NO_CLIENT, zones)
    simulation.upstream.bodies.update(bodies or {})
    await simulation.run()
    return simulation


@pytest.mark.asyncio
async def test_the_five_default_zones_do_not_ask_the_new_services_but_france_adds_the_rivers(sim_clock: SimClock) -> None:
    simulation = await one_day(sim_clock)
    calls = simulation.upstream.calls
    assert (calls["dwd_cap"], calls["eccc_alerts"], calls["metservice_cap"]) == (0, 0, 0)
    assert calls["meteoalarm_atom"] == 640  # Paris (France) and Reykjavik (Iceland): as before this work
    # Vigicrues: one request for all of France. While a section is orange the flow is kept 10 minutes (the scheduler looks
    # every 90 seconds, so a refresh every 630), and 30 minutes otherwise.
    assert calls["vigicrues_flow"] == 138
    print(f"\nthe five default zones, with a section in orange: vigicrues_flow {calls['vigicrues_flow']} requests a day")


@pytest.mark.asyncio
async def test_on_a_calm_day_vigicrues_is_asked_46_times_a_day(sim_clock: SimClock) -> None:
    simulation = await one_day(sim_clock, bodies={"vigicrues_flow": calm_flow()})
    assert simulation.upstream.calls["vigicrues_flow"] == 46  # 30 minutes of cache, looked at every 90 s: a refresh every 31.5 minutes
    print(f"\nthe five default zones, a calm day: vigicrues_flow {simulation.upstream.calls['vigicrues_flow']} requests a day")


@pytest.mark.asyncio
async def test_a_zone_in_germany_costs_one_conditional_request_every_three_minutes_and_no_meteoalarm(sim_clock: SimClock) -> None:
    simulation = await one_day(sim_clock, [zone(0, "Berlin, DE", samples.BERLIN)])
    calls, not_modified = simulation.upstream.calls, simulation.upstream.not_modified
    # 120 s of cache; the scheduler looks every 90 s: a refresh every 180 s.
    assert calls["dwd_cap"] == DAY_S // 180 == 480
    assert not_modified["dwd_cap"] == 479  # all but the first answer are a 304 without a body
    assert calls["meteoalarm_atom"] == 0 and calls["meteoalarm_cap"] == 0  # the copies of MeteoAlarm are not asked for
    print(f"\na zone in Germany: dwd_cap {calls['dwd_cap']} requests a day ({not_modified['dwd_cap']} of them 304)")


@pytest.mark.asyncio
async def test_a_zone_in_canada_costs_one_request_every_six_minutes(sim_clock: SimClock) -> None:
    simulation = await one_day(sim_clock, [zone(0, "Toronto, CA", samples.TORONTO)])
    calls = simulation.upstream.calls
    assert calls["eccc_alerts"] == DAY_S // 360 == 240  # 300 s of cache: a refresh every 4th look; GeoMet has no validator
    assert simulation.upstream.not_modified["eccc_alerts"] == 0
    print(f"\na zone in Canada: eccc_alerts {calls['eccc_alerts']} requests a day (no 304 on offer)")


@pytest.mark.asyncio
async def test_a_zone_in_new_zealand_costs_one_conditional_request_every_six_minutes(sim_clock: SimClock) -> None:
    simulation = await one_day(sim_clock, [zone(0, "Wellington, NZ", samples.WELLINGTON)])
    calls, not_modified = simulation.upstream.calls, simulation.upstream.not_modified
    assert calls["metservice_cap"] == DAY_S // 360 == 240
    assert not_modified["metservice_cap"] == 239
    print(f"\na zone in New Zealand: metservice_cap {calls['metservice_cap']} requests a day ({not_modified['metservice_cap']} of them 304)")


async def readers_day(sim_clock: SimClock, zones: list[dict[str, Any]], readers: int) -> Simulation:
    """A day of the scheduler plus ``readers`` calls to the warnings of a point, spread evenly over the day."""
    sim_clock.now = 0.0
    busy = Simulation(sim_clock, NO_CLIENT, zones)
    points = [samples.BERLIN, samples.TORONTO, samples.WELLINGTON, samples.BORKUM, samples.WHITEHORSE, samples.NIMES]

    async def read(index: int) -> None:
        lat, lon = points[index % len(points)]
        await busy.aggregator.vigilances.for_point(lat, lon)

    extra: dict[int, list[Callable[[], Awaitable[None]]]] = {}
    for index in range(readers):
        extra.setdefault(index * DAY_S // readers, []).append(lambda i=index: read(i))
    await busy.run(extra=extra)
    return busy


@pytest.mark.asyncio
async def test_the_readers_never_push_a_source_past_its_cache_lifetime(sim_clock: SimClock) -> None:
    """The requests to a source are bounded by one per cache lifetime, whatever the number of readers of the warnings."""
    zones = [zone(0, "Berlin, DE", samples.BERLIN), zone(1, "Toronto, CA", samples.TORONTO), zone(2, "Wellington, NZ", samples.WELLINGTON)]
    bounds = {"dwd_cap": DAY_S // 120 + 1, "eccc_alerts": DAY_S // 300 + 1, "metservice_cap": DAY_S // 300 + 1, "vigicrues_flow": DAY_S // 600 + 1}
    counts: dict[int, Counter[str]] = {}
    for readers in (1_000, 10_000):
        busy = await readers_day(sim_clock, zones, readers)
        counts[readers] = Counter(busy.upstream.calls)
        for source, bound in bounds.items():
            assert busy.upstream.calls[source] <= bound, (readers, source)
    # Ten times the readers, not ten times the requests: the second figure is the worst case, one refresh per lifetime.
    for source in bounds:
        assert counts[10_000][source] <= bounds[source]
        assert counts[10_000][source] < 2 * max(counts[1_000][source], 1) + 50
    print(
        "\nwith readers of the warnings every 86 s: "
        + ", ".join(f"{source} {counts[1_000][source]}" for source in ("dwd_cap", "eccc_alerts", "metservice_cap", "vigicrues_flow"))
        + " requests a day; every 9 s: "
        + ", ".join(f"{source} {counts[10_000][source]}" for source in ("dwd_cap", "eccc_alerts", "metservice_cap", "vigicrues_flow"))
    )
