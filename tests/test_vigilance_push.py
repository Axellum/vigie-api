"""The changes pushed for a followed point: a warning that appears, goes up, or ends.

A feed that a test changes between two looks stands for the provider; the clock moves past the 180 seconds the feed
is kept. The first look at a point only sets the baseline: nothing is pushed for what was already there.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.attribution import METEOALARM_DISCLAIMER
from app.collectors.disasters import NOAANWSCollector
from app.collectors.meteoalarm import MeteoAlarmCapCollector, MeteoAlarmCollector
from app.main import WebSocketHub
from app.models.schemas import SeverityLevel
from app.services.scheduler import BackgroundAlertScheduler
from app.services.vigilance import FollowedPoint, VigilanceService
from tests import vigilance_samples as samples
from tests.helpers import SimClock
from tests.helpers import make_service as make_aggregator
from tests.vigilance_upstream import Upstream, atom_entry, atom_feed, make_service

DROME_DOC = "b77d3bc0-76a2-4886-8da8-bac8d99f57fc"
VALENCE = FollowedPoint(key="point:valence", lat=samples.VALENCE[0], lon=samples.VALENCE[1], label={}, audience="followers")
PARIS = FollowedPoint(key="point:paris", lat=samples.PARIS[0], lon=samples.PARIS[1], label={}, audience="followers")


@pytest.fixture()
def upstream() -> Upstream:
    return Upstream()


@pytest.fixture()
def service(upstream: Upstream, sim_clock: SimClock) -> VigilanceService:
    return make_service(upstream)


async def look(service: VigilanceService, clock: SimClock, *points: FollowedPoint) -> list[tuple[str, str]]:
    """Let the feed expire, look at the points, and return (change, area) for each change."""
    clock.advance(181)
    return [(change.change, change.vigilance.area.description or "?") for _, change in await service.poll_changes(list(points))]


async def test_the_first_look_sets_the_baseline_and_pushes_nothing(
    service: VigilanceService, upstream: Upstream, sim_clock: SimClock
) -> None:
    upstream.feeds["france"] = atom_feed(atom_entry(DROME_DOC))
    assert await service.poll_changes([VALENCE]) == []
    assert await look(service, sim_clock, VALENCE) == []  # still the same warning: no change


async def test_a_warning_that_appears_is_pushed_with_its_words(
    service: VigilanceService, upstream: Upstream, sim_clock: SimClock
) -> None:
    upstream.feeds["france"] = atom_feed()
    assert await service.poll_changes([VALENCE]) == []
    upstream.feeds["france"] = atom_feed(atom_entry(DROME_DOC))
    sim_clock.advance(181)
    [(point, change)] = await service.poll_changes([VALENCE])
    assert point is VALENCE
    assert change.event == "vigilance_push" and change.change == "appeared" and change.previous_level is None
    assert change.point == {"lat": VALENCE.lat, "lon": VALENCE.lon}
    assert change.vigilance.level == SeverityLevel.YELLOW and change.vigilance.area.localization == "geocode"
    assert change.vigilance.issuer == "METEO-FRANCE" and len(change.vigilance.texts) == 2  # the words come with the push
    assert change.attribution is not None and change.attribution.endswith("| Issued by METEO-FRANCE")
    assert change.disclaimer == METEOALARM_DISCLAIMER
    assert change.server_utc == int(SimClock.EPOCH + sim_clock.now)


async def test_a_warning_that_goes_up_is_pushed_with_the_level_before(
    service: VigilanceService, upstream: Upstream, sim_clock: SimClock
) -> None:
    upstream.feeds["france"] = atom_feed(atom_entry("11111111-1111-1111-1111-111111111111"))
    await service.poll_changes([VALENCE])
    # An update is a new document (a new identifier) for the same phenomenon and area.
    upstream.feeds["france"] = atom_feed(atom_entry("22222222-2222-2222-2222-222222222222", level="Orange", message_type="Update"))
    sim_clock.advance(181)
    [(_, change)] = await service.poll_changes([VALENCE])
    assert (change.change, change.previous_level, change.vigilance.level) == ("upgraded", SeverityLevel.YELLOW, SeverityLevel.ORANGE)
    assert change.vigilance.status.value == "update"


async def test_a_warning_that_goes_down_or_stays_is_not_pushed(
    service: VigilanceService, upstream: Upstream, sim_clock: SimClock
) -> None:
    upstream.feeds["france"] = atom_feed(atom_entry("11111111-1111-1111-1111-111111111111", level="Red"))
    await service.poll_changes([VALENCE])
    upstream.feeds["france"] = atom_feed(atom_entry("22222222-2222-2222-2222-222222222222", level="Orange", message_type="Update"))
    assert await look(service, sim_clock, VALENCE) == []
    upstream.feeds["france"] = atom_feed(atom_entry("33333333-3333-3333-3333-333333333333", level="Orange", message_type="Update"))
    assert await look(service, sim_clock, VALENCE) == []  # a new document, same level, same area: nothing new


async def test_a_warning_that_ends_is_pushed_without_asking_for_its_words_again(
    service: VigilanceService, upstream: Upstream, sim_clock: SimClock
) -> None:
    upstream.feeds["france"] = atom_feed(atom_entry(DROME_DOC))
    await service.poll_changes([VALENCE])
    upstream.feeds["france"] = atom_feed()
    sim_clock.advance(181)
    asked = len(upstream.cap_requests)
    [(_, change)] = await service.poll_changes([VALENCE])
    assert change.change == "ended" and change.vigilance.level == SeverityLevel.YELLOW
    assert len(upstream.cap_requests) == asked
    assert await look(service, sim_clock, VALENCE) == []  # it ended once


async def test_a_warning_whose_time_is_over_ends_even_if_the_feed_still_lists_it(
    service: VigilanceService, upstream: Upstream, sim_clock: SimClock
) -> None:
    ends = "2026-10-14T19:00:00+00:00"  # 1792004400: a little after the simulated clock starts (1,792,000,000)
    upstream.feeds["france"] = atom_feed(atom_entry(DROME_DOC, expires=ends))
    await service.poll_changes([VALENCE])
    sim_clock.advance(10_000)
    assert await look(service, sim_clock, VALENCE) == [("ended", "Drôme")]


async def test_a_cancelled_warning_ends(service: VigilanceService, upstream: Upstream, sim_clock: SimClock) -> None:
    upstream.feeds["france"] = atom_feed(atom_entry("11111111-1111-1111-1111-111111111111"))
    await service.poll_changes([VALENCE])
    upstream.feeds["france"] = atom_feed(atom_entry("22222222-2222-2222-2222-222222222222", message_type="Cancel"))
    assert await look(service, sim_clock, VALENCE) == [("ended", "Drôme")]


async def test_a_provider_that_does_not_answer_never_ends_a_warning(
    service: VigilanceService, upstream: Upstream, sim_clock: SimClock
) -> None:
    upstream.feeds["france"] = atom_feed(atom_entry(DROME_DOC))
    await service.poll_changes([VALENCE])
    upstream.feeds["france"] = 503
    sim_clock.advance(100_000)  # even the cache is too old to serve: the source is unavailable
    service.meteoalarm._cache.clear()
    assert await service.poll_changes([VALENCE]) == []  # the picture is incomplete: left as it was
    upstream.feeds["france"] = atom_feed()
    sim_clock.advance(181)
    assert [(c.change, c.vigilance.area.description) for _, c in await service.poll_changes([VALENCE])] == [("ended", "Drôme")]


async def test_a_stale_feed_is_still_a_picture(service: VigilanceService, upstream: Upstream, sim_clock: SimClock) -> None:
    upstream.feeds["france"] = atom_feed(atom_entry(DROME_DOC))
    await service.poll_changes([VALENCE])
    upstream.feeds["france"] = 503
    assert await look(service, sim_clock, VALENCE) == []  # the last feed is served: the warning is still there


async def test_each_point_has_its_own_baseline_and_a_forgotten_point_starts_again(
    service: VigilanceService, upstream: Upstream, sim_clock: SimClock
) -> None:
    upstream.feeds["france"] = atom_feed()
    await service.poll_changes([VALENCE])
    upstream.feeds["france"] = atom_feed(atom_entry(DROME_DOC))
    sim_clock.advance(181)
    changes = await service.poll_changes([VALENCE, PARIS])  # Paris is new: baseline only; Valence sees the warning
    assert [(p.key, c.change) for p, c in changes] == [("point:valence", "appeared")]
    # Valence is not followed any more, then followed again: it is a new first look, nothing is pushed.
    await service.poll_changes([PARIS])
    assert await look(service, sim_clock, PARIS, VALENCE) == []


async def test_two_followers_of_the_same_country_cost_one_feed(
    service: VigilanceService, upstream: Upstream, sim_clock: SimClock
) -> None:
    await service.poll_changes([VALENCE, PARIS])
    await look(service, sim_clock, VALENCE, PARIS)
    assert upstream.feed_requests == ["france", "france"]  # two looks, 181 s apart


async def test_a_polygon_warning_is_followed_like_any_other(
    service: VigilanceService, upstream: Upstream, sim_clock: SimClock
) -> None:
    glarus = FollowedPoint(key="point:glarus", lat=samples.GLARUS[0], lon=samples.GLARUS[1], label={}, audience="followers")
    quiet = samples.fixture("atom_empty.xml")
    upstream.feeds["switzerland"] = quiet
    await service.poll_changes([glarus])
    upstream.feeds["switzerland"] = samples.fixture("atom_switzerland.xml")
    sim_clock.advance(181 + 490_800)  # the moment of the recording, so that the warnings are in force
    [(_, change)] = await service.poll_changes([glarus])
    assert (change.change, change.vigilance.area.description, change.vigilance.area.localization) == (
        "appeared",
        "Glarus Süd-Sernftal",
        "polygon",
    )


# ---------------------------------------------------------------- the scheduler and the WebSocket hub


class FakeSocket:
    """A WebSocket connection that keeps what it is sent."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_json(self, message: dict[str, Any]) -> None:
        self.sent.append(message)


def scheduler_for(upstream: Upstream, tmp_path: Path, hub: WebSocketHub) -> BackgroundAlertScheduler:
    aggregator = make_aggregator(
        tmp_path / "scheduler.db",
        meteoalarm=MeteoAlarmCollector(http_client=upstream.meteoalarm.client()),
        meteoalarm_cap=MeteoAlarmCapCollector(http_client=upstream.meteoalarm.client()),
        noaa_nws=NOAANWSCollector(http_client=upstream.nws_provider.client()),
    )
    return BackgroundAlertScheduler(aggregator=aggregator, ws_hub=hub, interval_seconds=90)


async def test_the_scheduler_pushes_a_change_to_the_clients_that_follow_the_point(
    upstream: Upstream, sim_clock: SimClock, tmp_path: Path
) -> None:
    hub = WebSocketHub()
    follower, bystander = FakeSocket(), FakeSocket()
    for socket in (follower, bystander):
        hub.active_connections.append(socket)  # type: ignore[arg-type]
    hub.follow(follower, *samples.VALENCE)  # type: ignore[arg-type]
    scheduler = scheduler_for(upstream, tmp_path, hub)
    upstream.feeds["france"] = atom_feed()

    assert await scheduler.run_vigilance_cycle() == 0  # the baseline of the five watched zones and of the followed point
    upstream.feeds["france"] = atom_feed(atom_entry(DROME_DOC))
    sim_clock.advance(181)
    assert await scheduler.run_vigilance_cycle() == 1

    [message] = follower.sent
    assert message["event"] == "vigilance_push" and message["change"] == "appeared"
    assert message["point"] == {"lat": VALENCE.lat, "lon": VALENCE.lon}
    assert message["vigilance"]["area"]["description"] == "Drôme" and message["vigilance"]["level"] == "YELLOW"
    assert message["disclaimer"] == METEOALARM_DISCLAIMER
    assert bystander.sent == []  # the watch is for the clients that asked for this point
    status = scheduler.get_status()
    assert status["last_vigilance_changes_count"] == 1 and status["last_vigilance_error"] is None


async def test_the_scheduler_tells_everyone_about_the_watched_zones(
    upstream: Upstream, sim_clock: SimClock, tmp_path: Path
) -> None:
    hub = WebSocketHub()
    one, two = FakeSocket(), FakeSocket()
    hub.active_connections.extend([one, two])  # type: ignore[list-item]
    scheduler = scheduler_for(upstream, tmp_path, hub)  # the default zones: Paris, Tokyo, Pointe-a-Pitre, San Francisco, Reykjavik
    upstream.feeds["france"] = atom_feed()
    await scheduler.run_vigilance_cycle()
    sim_clock.advance(181)
    # Paris is département 75, NUTS3 code FR101.
    upstream.feeds["france"] = atom_feed(atom_entry("11111111-1111-1111-1111-111111111111", code="FR101", name="Paris"))
    changes = await scheduler.run_vigilance_cycle()
    assert changes == 1
    for socket in (one, two):
        [message] = socket.sent
        assert message["event"] == "vigilance_push" and message["point"]["zone_id"] == "z0" and message["point"]["zone_name"] == "Paris, FR"


async def test_the_scheduler_keeps_going_when_a_look_fails(
    upstream: Upstream, sim_clock: SimClock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scheduler = scheduler_for(upstream, tmp_path, WebSocketHub())

    async def boom(points: Any) -> None:
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(scheduler.aggregator.vigilances, "refresh", boom)
    assert await scheduler.run_vigilance_cycle() == 0
    assert scheduler.get_status()["last_vigilance_error"] == "RuntimeError: disk on fire"


async def test_the_followed_points_are_the_zones_then_the_clients(upstream: Upstream, tmp_path: Path) -> None:
    hub = WebSocketHub()
    socket = FakeSocket()
    hub.active_connections.append(socket)  # type: ignore[arg-type]
    hub.follow(socket, 44.93341, 4.89241)  # type: ignore[arg-type]
    points = scheduler_for(upstream, tmp_path, hub).followed_points()
    assert [p.audience for p in points] == ["all"] * 5 + ["followers"]
    assert points[0].key == "zone:z0" and points[0].label == {"zone_id": "z0", "zone_name": "Paris, FR"}
    assert (points[-1].lat, points[-1].lon) == (44.9334, 4.8924)


async def test_a_vigilance_cycle_runs_with_every_cycle_of_the_scheduler(
    upstream: Upstream, sim_clock: SimClock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scheduler = scheduler_for(upstream, tmp_path, WebSocketHub())
    seen: list[int] = []

    async def cycle() -> int:
        seen.append(1)
        return 7

    monkeypatch.setattr(scheduler, "run_vigilance_cycle", cycle)
    result = await scheduler.run_single_cycle()
    assert seen == [1] and result["vigilance_changes_count"] == 7
