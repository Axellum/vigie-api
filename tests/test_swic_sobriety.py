"""What the services of the WMO registry cost: bounded by the services and the clock, never by the clients.

One simulated day of the scheduler, against the registry and the feeds as they were measured on 2026-10-20
(``tests/fixtures/swic/profile.json``, ``tests/swic_upstream.py``: simulated providers, nothing touches the network, nothing
sleeps). The worst case a personal instance can ask of this part: one followed point in **every** country a service of the
registry answers for, with ``VIGIE_WMO_SOURCES=all`` (every service the owner decided never to read is left out). The figures
printed with ``pytest -s`` are the ones quoted in ``docs/data-sources.md`` and in the README.
"""

from __future__ import annotations

from collections import Counter

import pytest

from app.collectors.meteoalarm import MeteoAlarmCapCollector, MeteoAlarmCollector
from app.collectors.swic import HEAVY_FEED_STEP_BYTES, MAX_SOURCE_TTL_S, SOURCE_TTL_S, SwicCollector
from app.collectors.swic_registry import SwicRegistryCollector
from app.collectors.swic_terms import EXCLUDED, status_of
from app.services.geoindex import default_countries, default_geometries
from app.services.vigilance import FollowedPoint, VigilanceService
from tests.helpers import SimClock
from tests.swic_upstream import PROFILE, SwicWorld
from tests.vigilance_upstream import Upstream as MeteoAlarmUpstream

DAY_S = 86_400
SCHEDULER_PERIOD_S = 90
RECORDED_AT = 1_792_515_600  # 2026-10-20T17:00:00Z, when the feeds were measured


def build(world: SwicWorld, mode: str) -> VigilanceService:
    client = world.client()
    meteoalarm = MeteoAlarmUpstream()
    return VigilanceService(
        meteoalarm=MeteoAlarmCollector(http_client=meteoalarm.meteoalarm.client()),
        meteoalarm_cap=MeteoAlarmCapCollector(http_client=meteoalarm.meteoalarm.client()),
        noaa_nws=None,  # type: ignore[arg-type]
        countries=default_countries(),
        geometries=default_geometries(),
        watched_zones=lambda: [],
        swic=SwicCollector(http_client=client),
        swic_registry=SwicRegistryCollector(http_client=client),
        swic_mode=mode,
    )


def followed_points(service: VigilanceService) -> tuple[list[FollowedPoint], list[str]]:
    """One followed point inside each country a registry service answers for (countries of a collector of their own are left out)."""
    borders = default_countries()._borders  # noqa: SLF001
    countries = sorted(service._swic_index()[0])  # noqa: SLF001
    points = []
    for country in countries:
        spot = borders[country].representative_point()
        points.append(FollowedPoint(key=f"zone:{country}", lat=spot.y, lon=spot.x, label={}, audience="all"))  # type: ignore[arg-type]
    return points, countries


async def play(service: VigilanceService, clock: SimClock, points: list[FollowedPoint], duration_s: int = DAY_S) -> None:
    end = clock.now + duration_s
    while clock.now < end:
        await service.refresh(points)
        await service.poll_changes(points)
        clock.advance(SCHEDULER_PERIOD_S)


async def test_a_day_of_every_service_costs_the_clock_not_the_clients(sim_clock: SimClock) -> None:
    sim_clock.advance(RECORDED_AT - SimClock.EPOCH)
    world = SwicWorld(sim_clock)
    service = build(world, "all")
    await service.ensure_registry()
    points, countries = followed_points(service)
    await play(service, sim_clock, points)
    totals = world.totals()
    services = sorted({s for s, _ in world.calls})
    print(
        f"\n{len(countries)} countries, {len(services)} services asked, 24 h on the scheduler: "
        f"{totals['requests']} requests, {totals['bytes'] / 1e6:.2f} MB "
        f"(feeds {totals['index_requests']} / {totals['index_bytes'] / 1e6:.2f} MB, documents {totals['document_requests']} / "
        f"{totals['document_bytes'] / 1e6:.2f} MB, the WMO's copy {totals['mirror_requests']} / {totals['mirror_bytes'] / 1e6:.2f} MB, "
        f"registry {totals['registry_requests']} / {totals['registry_bytes'] / 1e3:.0f} kB)"
    )
    heaviest = sorted(services, key=lambda sid: -sum(n for (s, _), n in world.bytes.items() if s == sid))[:8]
    for sid in heaviest:
        sent = sum(n for (s, _), n in world.bytes.items() if s == sid)
        asked = sum(n for (s, _), n in world.calls.items() if s == sid)
        print(f"  {sid:22s} {asked:5d} requests {sent / 1e6:6.2f} MB")
    assert len(countries) >= 60
    assert not set(EXCLUDED) & set(services)  # the excluded services are never asked
    assert totals["registry_requests"] <= 2  # once a day, with If-Modified-Since
    # No feed is asked more often than its clock allows: every 5 minutes, less for a feed that must be downloaded whole.
    per_feed = Counter()
    for entries in PROFILE["services"].values():
        for entry in entries:
            per_feed[entry["url"]] = 0
    for service_id, entries in PROFILE["services"].items():
        if service_id in EXCLUDED:
            continue
        index = world.calls[(service_id, "index")]
        feeds = len(entries)
        assert index <= feeds * (DAY_S // SOURCE_TTL_S + 1), f"{service_id}: {index} feed requests"
    # A document is downloaded once: never more than the alerts the feed listed, then the new ones of the day.
    for service_id, entries in PROFILE["services"].items():
        if service_id in EXCLUDED:
            continue
        listed = sum(int(e.get("recent") or 0) for e in entries if e.get("status") == 200)
        new_in_a_day = sum(int(e.get("recent") or 0) / 14.0 for e in entries if e.get("status") == 200)
        assert world.calls[(service_id, "document")] <= listed + new_in_a_day + len(entries) + 1, service_id
    # The WMO's copy answers 404 and is left alone after three misses in a row (an hour), and a hit is remembered for a day.
    assert totals["mirror_requests"] <= 24 * 3 + len(services) * 3
    # Pinned so that a change of rhythm is noticed: requests and megabytes per day for the whole set.
    assert 10_000 < totals["requests"] < 60_000
    assert totals["bytes"] < 400_000_000


async def test_the_default_instance_reads_the_verified_services_only(sim_clock: SimClock) -> None:
    sim_clock.advance(RECORDED_AT - SimClock.EPOCH)
    world = SwicWorld(sim_clock)
    service = build(world, "verified")
    await service.ensure_registry()
    points, countries = followed_points(service)
    await play(service, sim_clock, points)
    asked = {s for s, k in world.calls if k == "index"}
    verified = {sid for sid in PROFILE["services"] if status_of(sid) == "verified"}
    totals = world.totals()
    print(
        f"\nverified only: {len(countries)} countries, {len(asked)} services, 24 h: {totals['requests']} requests, "
        f"{totals['bytes'] / 1e6:.2f} MB"
    )
    assert asked <= verified
    assert asked  # and some of them were asked
    assert totals["registry_requests"] <= 2


def test_a_feed_that_cannot_be_asked_conditionally_waits_for_its_weight() -> None:
    """The refresh rhythm of a service: 5 minutes, one more per 64 KiB downloaded whole each time, 30 minutes at most."""
    from app.collectors.swic import IndexState, refresh_period_s

    conditional = IndexState(validators={"ETag": '"x"'}, size=900_000)
    light = IndexState(size=20_000)
    heavy = IndexState(size=2 * HEAVY_FEED_STEP_BYTES + 1)
    huge = IndexState(size=50 * HEAVY_FEED_STEP_BYTES)
    assert refresh_period_s([conditional]) == SOURCE_TTL_S  # a 304 costs a few hundred bytes whatever the feed weighs
    assert refresh_period_s([light]) == SOURCE_TTL_S
    assert refresh_period_s([heavy]) == 3 * SOURCE_TTL_S
    assert refresh_period_s([light, heavy]) == 3 * SOURCE_TTL_S  # a service with several feeds adds them up
    assert refresh_period_s([huge]) == MAX_SOURCE_TTL_S
    assert refresh_period_s([]) == SOURCE_TTL_S


@pytest.mark.parametrize("service_id", ["tt-ttms-en", "mx-smn-es"])
def test_the_measured_feeds_that_give_no_validator(service_id: str) -> None:
    """Trinidad and Tobago (641 KB of XML, 67 KB sent) and Mexico (255 KB, 13 KB sent) give none: what the profile says."""
    feeds = PROFILE["services"][service_id]
    assert feeds[0]["status"] == 200 and not feeds[0]["etag"] and not feeds[0]["last_modified"]
