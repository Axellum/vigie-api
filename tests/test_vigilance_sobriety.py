"""What the official warnings cost the providers: bounded by the countries and the clock, never by the clients.

Two simulated days of the scheduler and of the clients play against the recorded MeteoAlarm feeds (simulated clock,
simulated providers: nothing touches the network and nothing sleeps). Every request the providers receive is counted.
The figures printed with ``pytest -s`` are the ones quoted in ``docs/data-sources.md``.
"""

from __future__ import annotations

from collections import Counter

import pytest

from app.collectors.meteoalarm import COUNTRY_FEEDS
from app.services.storage_wal import DEFAULT_WATCHLIST_SEED
from app.services.vigilance import FollowedPoint
from tests import vigilance_samples as samples
from tests.helpers import SimClock
from tests.vigilance_upstream import Upstream, make_service

DAY_S = 86_400
SCHEDULER_PERIOD_S = 90
FEED_TTL_S = 180

# 50 positions in four countries with a recorded feed (the clients of a display are people at home).
CITIES = {
    "CH": [(47.3769, 8.5417), (46.948, 7.4474), (47.5596, 7.5886), (46.2044, 6.1432), (46.5197, 6.6323), samples.GLARUS],
    "IS": [samples.REYKJAVIK, (65.6835, -18.1262), (66.0749, -23.1353), (65.2669, -14.3948), (63.9328, -21.0)],
    "DE": [(52.52, 13.405), (53.5511, 9.9937), (48.1351, 11.582), (50.9375, 6.9603), (50.1109, 8.6821)],
}


def fifty_points() -> list[tuple[float, float]]:
    """Six Swiss, five Icelandic, five German places and 34 French ones (a spread over the départements)."""
    from app.services.geoindex import default_geometries

    geometries = default_geometries()
    french = sorted(code for scheme, code in geometries._shapes if scheme == "NUTS3" and code.startswith("FR"))
    points = [(c.y, c.x) for c in (geometries.get("NUTS3", code).representative_point() for code in french[::3])][:34]
    points += CITIES["CH"] + CITIES["IS"] + CITIES["DE"]
    assert len(points) == 50
    return points


def zones() -> list[dict[str, object]]:
    return [dict(zone) for zone in DEFAULT_WATCHLIST_SEED]


async def play(
    clock: SimClock, upstream: Upstream, duration_s: int, clients_per_point: int = 0, poll_period_s: int = 1800
) -> tuple[int, int]:
    """The scheduler every 90 s over the five default zones, and the clients polling ``/v1/vigilances``; returns (answers, changes)."""
    service = make_service(upstream, zones())
    followed = [
        FollowedPoint(key=f"zone:{z['id']}", lat=float(z["lat"]), lon=float(z["lon"]), label={}, audience="all")  # type: ignore[arg-type]
        for z in zones()
    ]
    positions = fifty_points() if clients_per_point else []
    answers = changes = 0
    end = clock.now + duration_s
    next_poll = clock.now
    while clock.now < end:
        await service.refresh(followed)
        changes += len(await service.poll_changes(followed))
        if clients_per_point and clock.now >= next_poll:
            for lat, lon in positions:
                for _ in range(clients_per_point):  # the clients of a point ask within the same minute
                    await service.for_point(lat, lon)
                    answers += 1
            next_poll += poll_period_s
        clock.advance(SCHEDULER_PERIOD_S)
    return answers, changes


@pytest.fixture()
def upstream() -> Upstream:
    up = Upstream()
    up.default_cap = samples.fixture("cap_france_b77d3bc0.xml")
    return up


async def test_a_day_of_the_scheduler_on_the_five_default_zones(upstream: Upstream, sim_clock: SimClock) -> None:
    """Paris and Reykjavik have a feed; Tokyo and Pointe-à-Pitre have no source; San Francisco is the NWS answer of the hazards."""
    sim_clock.advance(samples.RECORDED_AT - SimClock.EPOCH)
    await play(sim_clock, upstream, DAY_S)
    feeds = Counter(upstream.feed_requests)
    print(f"\nscheduler, 5 default zones, 24 h: MeteoAlarm feeds {dict(feeds)}, CAP documents {len(upstream.cap_requests)}")
    assert set(feeds) == {"france", "iceland"}  # only the countries that are needed
    # A feed is asked again once its 180 s are over, at the next 90 s cycle: every 270 s.
    for count in feeds.values():
        assert count <= DAY_S // FEED_TTL_S + 1
        assert count == pytest.approx(DAY_S / 270, abs=2)
    assert upstream.cap_requests == []  # the scheduler never fetches the words of a warning that did not change
    assert upstream.nws_provider.count <= DAY_S // 180 + 1  # in the application these are the hazards' own requests


async def test_a_day_of_clients_does_not_cost_the_providers_more_than_the_clock_allows(
    upstream: Upstream, sim_clock: SimClock
) -> None:
    """500 clients on 50 positions of four countries, polling every 30 minutes for 6 hours: 6,000 answers."""
    sim_clock.advance(samples.RECORDED_AT - SimClock.EPOCH)
    hours = 6
    answers, _ = await play(sim_clock, upstream, hours * 3600, clients_per_point=10)
    feeds = Counter(upstream.feed_requests)
    documents = Counter(upstream.cap_requests)
    print(
        f"\n{answers} answers over {hours} h: MeteoAlarm feeds {dict(feeds)} "
        f"({sum(feeds.values())} requests), CAP documents {len(documents)}"
    )
    assert answers == 50 * 10 * hours * 2
    # One feed per country serves every client of the country: bounded by the clock, not by the number of clients.
    assert set(feeds) == {"france", "switzerland", "iceland", "germany"}
    assert all(count <= hours * 3600 // FEED_TTL_S + 1 for count in feeds.values())
    # A CAP document is asked once (it never changes) and only for a warning an answer returned.
    assert max(documents.values()) == 1
    assert len(documents) <= 40  # the distinct documents of the recorded feeds that cover one of the 50 positions


def test_the_countries_with_a_feed_are_few_enough_to_never_ask_for_all_of_them() -> None:
    """Thirty-nine feeds exist; the API asks only for the ones that are needed (a zone, a followed point, a recent request)."""
    assert len(COUNTRY_FEEDS) == 39
