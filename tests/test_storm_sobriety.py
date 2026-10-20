"""What the tsunami warning centres and the National Hurricane Center cost in requests and bytes over one simulated day.

The day is played by ``tests/simulation.py``: the scheduler of the application runs with its period of 90 seconds, the clock is
simulated, and every request the providers would receive is counted. The answers are the recordings: tsunami.gov replays its list of
bulletins as it grew (so a day with the Panama earthquake, 2026-10-09, is the real thing), and the NHC serves its file of the active
storms (empty, or Hurricane Isaias with the real bundle of its advisory 13A for every advisory). The figures in
``docs/data-sources.md`` are these.

Neither provider publishes a rate limit or a quota (the pages read on 2026-10-20 say nothing: **not verified** that none exists), so
there is no budget to hold to: the proof is that the number of requests is bounded by the lifetime of the cache entry, whatever the
number of clients (the day of 1,000 clients of ``tests/test_call_sobriety.py`` counts these two sources too), and that a request that
finds nothing new costs a ``304`` of a few hundred bytes.
"""

from __future__ import annotations

import gzip
import math
import re
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

import pytest

from tests import storm_samples as S
from tests.helpers import SimClock
from tests.simulation import DAY_S, NHC_304_HEAD_BYTES, SCHEDULER_PERIOD_S, TSUNAMI_304_HEAD_BYTES, Scenario, Simulation

NO_CLIENT = Scenario(clients=0, points=50)
TSUNAMI, NHC = "tsunami_warning_centers", "nhc_storms"
QUIET_EPOCH = SimClock.EPOCH  # 2026-10-14 17:46 UTC: the recorded list holds nothing newer than that day's information statements
ADVISORY_EVERY_S = 3 * 3600  # the NHC issues an advisory every six hours, an intermediate one every three when a watch or warning is up


def wire_size(body: bytes) -> int:
    return len(gzip.compress(body))


def day_of(sim_clock: SimClock, start: float = QUIET_EPOCH, scenario: Scenario = NO_CLIENT) -> Simulation:
    sim_clock.EPOCH = start
    return Simulation(sim_clock, scenario)


def isaias_advisory(number: int) -> bytes:
    """The status file of Isaias at its ``number``-th advisory of the day (the same real file, the advisory number changed)."""
    return S.isaias_only().replace(b"013A", f"{number}A".encode()).replace(b"013a", f"{number}a".encode())


# ------------------------------------------------------------------------------------------------ a quiet day


@pytest.mark.asyncio
async def test_a_quiet_day_costs_one_conditional_request_every_ninety_seconds_at_each_source(sim_clock: SimClock) -> None:
    simulation = day_of(sim_clock)
    result = await simulation.run()
    up = simulation.upstream
    assert result.failures == 0 and result.scheduler_cycles == DAY_S // SCHEDULER_PERIOD_S

    # tsunami.gov: 60 s of cache, the scheduler looks every 90 s: a look every 90 s. All but the first answer are a 304.
    assert up.calls[TSUNAMI] == DAY_S // 90 == 960
    assert up.not_modified[TSUNAMI] == 959
    first_list, _ = S.tsunami_listing_at(QUIET_EPOCH)
    assert up.bytes[TSUNAMI] == wire_size(first_list) + 959 * TSUNAMI_304_HEAD_BYTES  # the list once (1.7 KB gzipped), then heads of 239 bytes
    # No document is read: the list shows information statements only, and they are never downloaded.
    assert up.methods[(TSUNAMI, "GET")] == up.calls[TSUNAMI]

    # NHC: 300 s of cache looked at every 90 s: a look every 360 s. The file with no storm is 24 bytes; a 304 is 484 bytes of head.
    assert up.calls[NHC] == DAY_S // 360 == 240
    assert up.not_modified[NHC] == 239
    assert up.bytes[NHC] == len(S.nhc_fixture("CurrentStorms_empty.json")) + 239 * NHC_304_HEAD_BYTES
    print(
        f"\na quiet day: tsunami.gov {up.calls[TSUNAMI]} requests ({up.not_modified[TSUNAMI]} of them 304), {up.bytes[TSUNAMI] / 1000:.0f} kB; "
        f"nhc {up.calls[NHC]} requests ({up.not_modified[NHC]} of them 304), {up.bytes[NHC] / 1000:.0f} kB"
    )
    assert up.bytes[TSUNAMI] + up.bytes[NHC] < 400_000


# ------------------------------------------------------------------------------------------------ a day with a storm


@pytest.mark.asyncio
async def test_a_day_with_a_storm_reads_each_bundle_once_and_the_rest_is_304s(sim_clock: SimClock) -> None:
    """Isaias, with a new advisory every three hours (the most the NHC does): seven changes after the one the day starts with."""
    simulation = day_of(sim_clock, S.at("2026-10-09", "00:00"))
    up = simulation.upstream
    up.record_urls = True
    up.bodies[NHC] = isaias_advisory(13)

    def publish(number: int) -> Callable[[], Awaitable[None]]:
        async def go() -> None:
            up.bodies[NHC] = isaias_advisory(number)

        return go

    result = await simulation.run(extra={ADVISORY_EVERY_S * (k + 1): [publish(14 + k)] for k in range(7)})
    assert result.failures == 0

    bundles = [url for source, url in up.requests if source == NHC and url.endswith(".zip")]
    assert len(bundles) == 8 and len(set(bundles)) == 8  # the day's first advisory and seven more, and not one of them twice
    bundle_bytes = 8 * len(S.nhc_fixture("al092026_5day_013A.zip"))
    files = 8  # the file itself is read in full when it changes: the first time, and at each advisory
    assert up.calls[NHC] == 240 + len(bundles)  # the 240 looks of the day (one every 360 s), and a bundle behind each new advisory
    assert up.not_modified[NHC] == 240 - files  # all the other looks are 304s
    file_bytes = sum(len(isaias_advisory(n)) for n in [13, *range(14, 21)])
    assert up.bytes[NHC] == bundle_bytes + file_bytes + up.not_modified[NHC] * NHC_304_HEAD_BYTES
    print(
        f"\na day with a storm (8 advisories): nhc {up.calls[NHC]} requests: {len(bundles)} bundles ({bundle_bytes / 1000:.0f} kB), "
        f"{files} readings of the file ({file_bytes / 1000:.0f} kB), {up.not_modified[NHC]} 304s; {up.bytes[NHC] / 1000:.0f} kB in all"
    )
    assert up.bytes[NHC] < 450_000
    # The hazard and its forecast were there at the end of the day.
    health = simulation.aggregator.nhc.get_health()
    assert health.consecutive_failures == 0
    assert simulation.aggregator.nhc.last_state.storms[0].forecast is not None


# ------------------------------------------------------------------------------------------------ a day with a tsunami


@pytest.mark.asyncio
async def test_the_day_of_the_panama_earthquake_reads_each_document_once(sim_clock: SimClock) -> None:
    """2026-10-09, as the centre told it: four threat messages in two hours, and a list that changes 15 times in the day."""
    simulation = day_of(sim_clock, S.at("2026-10-09", "00:00"))
    up = simulation.upstream
    up.record_urls = True
    seen: list[tuple[int, int]] = []

    async def look() -> None:
        hazards = await simulation.aggregator.collect_and_fuse_hazards()
        seen.append((int(sim_clock.now), len([h for h in hazards if h.category.value == "TSUNAMI"])))

    # A client asks for the hazards at 18:10 (the first message is 8 minutes old) and again at 19:50 (the final message is out).
    start = S.at("2026-10-09", "00:00")
    extra = {int(S.at("2026-10-09", "18:10") - start): [look], int(S.at("2026-10-09", "19:50") - start): [look]}
    result = await simulation.run(extra=extra)
    assert result.failures == 0
    assert [count for _, count in seen] == [1, 0]  # a tsunami hazard while the alert lasted, none after the final message

    documents = [url for source, url in up.requests if source == TSUNAMI and "previous.js" not in url]
    assert len(documents) == 7 and len(set(documents)) == 7  # four CAP messages, three text bulletins (the final one has no table), once each
    assert sum(url.endswith("CAP.xml") for url in documents) == 4 and sum(url.endswith(".txt") for url in documents) == 3

    # The list: read in full at the start and each time a bulletin was added; every other look is a 304. The bulletins of the day
    # are created at 17 moments, which fall in as many 90-second looks (some share a look).
    text = S.tsunami_fixture("previous.js").decode()
    created = [
        datetime(*(int(x) for x in m.groups()), tzinfo=timezone.utc).timestamp()
        for m in re.finditer(r"'createdDB'\s*:\s*'(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})", text)
    ]
    of_the_day = [c for c in created if start < c <= start + DAY_S]
    looks_that_find_news = {math.ceil((c - start) / SCHEDULER_PERIOD_S) for c in of_the_day}
    list_reads = 1 + len(looks_that_find_news)
    lists = up.calls[TSUNAMI] - len(documents)
    assert len(of_the_day) == 17
    assert lists == DAY_S // SCHEDULER_PERIOD_S and up.not_modified[TSUNAMI] == lists - list_reads
    print(
        f"\nthe Panama day: tsunami.gov {up.calls[TSUNAMI]} requests: {lists} looks at the list ({list_reads} read in full, "
        f"{up.not_modified[TSUNAMI]} 304s), {len(documents)} documents; {up.bytes[TSUNAMI] / 1000:.0f} kB in all"
    )
    assert up.bytes[TSUNAMI] < 300_000
    assert simulation.aggregator.tsunami.get_health().consecutive_failures == 0
