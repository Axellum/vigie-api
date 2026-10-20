"""What the national sources cost in requests over one simulated day.

The day is played by ``tests/simulation.py``: the scheduler of the application runs with its period of 90 seconds on the
watched zones, the clock is simulated, and every request the providers would receive is counted per source. The answers are
the recordings of 2026-10-20 (the DWD and MetService ones send their validators and answer ``304`` to a repeated request).

Two things are proved: (1) a zone, a followed point or a client in Germany, Canada or New Zealand costs one request per cache
lifetime of its source, whatever the number of clients; (2) the five default zones cost nothing at the new sources (Paris adds
Vigicrues, which is one request for all of France). The figures in ``docs/data-sources.md`` are these.

Vigicrues weighs 2.2 MB a read, uncompressed, with no validator: its cost is counted in **bytes** too. The simulated server
publishes at 07:55:15 and 13:55:15 UTC (10 a.m. and 4 p.m. in Paris) and the collector looks at the image of the publication
(a HEAD of about 330 bytes) and reads the flow only when it changed (see ``app/collectors/vigicrues.py``).
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any, Awaitable, Callable

import pytest

from tests import vigilance_samples as samples
from tests.helpers import SimClock
from tests.simulation import DAY_S, VIGICRUES_FLOW_BYTES, VIGICRUES_HEAD_BYTES, Scenario, Simulation

NO_CLIENT = Scenario(clients=0, points=50)

# What the same simulated day cost before the collector looked at the publication image: the whole flow every 630 s while a
# section is orange (30 minutes of cache looked at every 90 s: every 1,890 s when calm). Measured with that collector in this
# very simulation: 138 reads, 309,905,358 bytes, and 46 reads, 103,301,786 bytes.
BEFORE_ORANGE = (138, 309_905_358)
BEFORE_CALM = (46, 103_301_786)


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


def vigicrues(simulation: Simulation) -> tuple[int, int, int]:
    """The reads of the whole flow, the looks at the image, and the bytes the simulated Vigicrues sent."""
    up = simulation.upstream
    return up.methods[("vigicrues_flow", "GET")], up.methods[("vigicrues_flow", "HEAD")], up.bytes["vigicrues_flow"]


@pytest.mark.asyncio
async def test_the_five_default_zones_do_not_ask_the_new_services_but_france_adds_the_rivers(sim_clock: SimClock) -> None:
    simulation = await one_day(sim_clock)
    calls = simulation.upstream.calls
    assert (calls["dwd_cap"], calls["eccc_alerts"], calls["metservice_cap"]) == (0, 0, 0)
    assert calls["meteoalarm_atom"] == 640  # Paris (France) and Reykjavik (Iceland): as before this work
    # Vigicrues, with two sections orange all day: a look at the image every 6 minutes (5 minutes of cache looked at every
    # 90 s), and the flow read at the start, at each of the two publications and once when 12 hours had passed.
    reads, looks, sent = vigicrues(simulation)
    assert (reads, looks) == (4, 240)
    assert sent == reads * VIGICRUES_FLOW_BYTES + looks * VIGICRUES_HEAD_BYTES
    assert sent < BEFORE_ORANGE[1] / 25
    print(f"\nthe five default zones, with a section in orange: vigicrues_flow {reads} flow reads and {looks} looks at the image, "
          f"{sent / 1e6:.1f} MB (was {BEFORE_ORANGE[0]} reads, {BEFORE_ORANGE[1] / 1e6:.1f} MB)")


@pytest.mark.asyncio
async def test_on_a_calm_day_vigicrues_costs_a_few_megabytes(sim_clock: SimClock) -> None:
    simulation = await one_day(sim_clock, bodies={"vigicrues_flow": calm_flow()})
    reads, looks, sent = vigicrues(simulation)
    assert (reads, looks) == (4, 88)  # a look every 16.5 minutes (15 minutes of cache looked at every 90 s)
    assert sent == reads * VIGICRUES_FLOW_BYTES + looks * VIGICRUES_HEAD_BYTES
    assert sent < BEFORE_CALM[1] / 10
    print(f"\nthe five default zones, a calm day: vigicrues_flow {reads} flow reads and {looks} looks at the image, "
          f"{sent / 1e6:.1f} MB (was {BEFORE_CALM[0]} reads, {BEFORE_CALM[1] / 1e6:.1f} MB)")


@pytest.mark.asyncio
async def test_once_started_a_day_of_vigicrues_is_the_two_publications_and_one_safety_read(sim_clock: SimClock) -> None:
    """The second of two days: the cold start (the first read, and a safety read that follows it) is not in it."""
    simulation = Simulation(sim_clock, NO_CLIENT)
    snapshot: dict[str, tuple[int, int, int]] = {}

    async def first_day_over() -> None:
        snapshot["day 1"] = vigicrues(simulation)

    await simulation.run(duration_s=2 * DAY_S, extra={DAY_S: [first_day_over]})
    reads, looks, sent = (after - before for after, before in zip(vigicrues(simulation), snapshot["day 1"], strict=True))
    assert reads == 3  # 07:55 and 13:55 UTC, and the 12 hours of the long night between them
    assert sent < 7_000_000
    print(f"\nthe five default zones, the second day: vigicrues_flow {reads} flow reads and {looks} looks, {sent / 1e6:.1f} MB")


@pytest.mark.asyncio
async def test_a_flood_published_in_the_afternoon_is_served_within_one_look_and_nothing_is_lost(sim_clock: SimClock) -> None:
    """Fewer reads must not mean later news: the Vistre turns orange at the 13:55 UTC publication of a calm day."""
    simulation = Simulation(sim_clock, NO_CLIENT)
    simulation.upstream.bodies["vigicrues_flow"] = calm_flow()
    publication = [t for t in simulation.upstream.vigicrues_publications if t > 0][1]  # 13:55:15 UTC, the first afternoon
    seen: dict[str, list[tuple[str, str]]] = {}

    async def publish() -> None:
        simulation.upstream.bodies["vigicrues_flow"] = samples.national_fixture("vigicrues", "InfoVigiCru_subset.geojson")

    def look(when: str) -> Callable[[], Awaitable[None]]:
        async def read() -> None:
            response = await simulation.aggregator.vigilances.for_point(*samples.NIMES)
            seen[when] = [(v.area.description or "", v.level.value if v.level else "") for v in response.vigilances if v.source == "vigicrues"]

        return read

    second = int(publication)
    await simulation.run(extra={second - 5: [publish], second - 60: [look("before")], second + 1_000: [look("after")]})
    assert seen["before"] == []
    assert seen["after"] == [("Vistre", "ORANGE")]  # served less than 17 minutes after the publication, the calm look being a quarter of an hour
    print(f"\na flood published at 13:55 UTC reached the point of Nîmes {seen['after']} within {1_000 // 60} minutes of the publication")


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
    # Vigicrues: a look every 5 minutes at most (16.5 minutes when calm), and the flow only on a publication.
    bounds = {"dwd_cap": DAY_S // 120 + 1, "eccc_alerts": DAY_S // 300 + 1, "metservice_cap": DAY_S // 300 + 1, "vigicrues_flow": DAY_S // 300 + 1}
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
