"""``GET /v1/events`` kept ready: the serialized answer is served again while nothing it was made from changed, and not a second longer.

The feeds of 2026-10-20 are played through the real collectors (their ``_collect`` is simulated, so the cache, the circuit breaker and
the freshness witness are the real ones). Nothing reaches the network.
"""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock

import pytest
from pydantic_core import to_json

from app.collectors.base import Collected, CollectorUnavailableError, Fetched
from app.collectors.meteoalarm import CAP_URL_PREFIX, CapDocument, MeteoAlarmFeed
from app.models.schemas import CircuitState, SeverityLevel
from app.models.vigilance import Vigilance
from app.services.events_cache import EventsAnswers
from tests import vigilance_samples as samples
from tests.conftest import World
from tests.helpers import SimClock

FEED_TTL_S = 180  # the cache lifetime of a MeteoAlarm feed (``MeteoAlarmCollector``)


class Upstream:
    """What the simulated MeteoAlarm answers, and how often it was asked."""

    def __init__(self) -> None:
        self.asked: Counter[str] = Counter()
        self.extra: dict[str, list[Vigilance]] = {}
        self.dropped: set[str] = set()
        self.failing: set[str] = set()
        self.fail_all = False

    def feed(self, country: str) -> MeteoAlarmFeed:
        if self.fail_all or country in self.failing:
            raise ConnectionError("simulated outage")
        base = samples.feed(country) if country in samples.FEED_FILES else MeteoAlarmFeed(country=country, updated_utc=None, vigilances=())
        kept = tuple(v for v in base.vigilances if v.id not in self.dropped) + tuple(self.extra.get(country, ()))
        return MeteoAlarmFeed(country=country, updated_utc=base.updated_utc, vigilances=kept)


@pytest.fixture()
def upstream(world: World, sim_clock: SimClock, monkeypatch: pytest.MonkeyPatch) -> Iterator[Upstream]:
    """The recorded feeds behind the real MeteoAlarm collectors; the other sources as in ``test_events.py``."""
    sim_clock.advance(samples.RECORDED_AT - SimClock.EPOCH)
    up = Upstream()
    aggregator = world.aggregator
    collectors = (aggregator.meteoalarm, aggregator.meteoalarm_cap, aggregator.usgs)

    def reset() -> None:  # the application of the tests is shared: start from nothing, and leave nothing
        for collector in collectors:
            collector._cache.clear()
            collector.consecutive_failures = 0
            collector.circuit_state = CircuitState.CLOSED
            collector.opened_at_monotonic = None

    reset()

    async def collect(client: Any, **kwargs: Any) -> Collected[MeteoAlarmFeed]:
        country = str(kwargs["country"])
        up.asked[country] += 1
        return Collected(up.feed(country))

    async def collect_document(client: Any, **kwargs: Any) -> Collected[CapDocument]:
        return Collected(CapDocument(available=False), fresh_for_s=900.0)

    monkeypatch.delattr(aggregator.meteoalarm, "fetch_country")  # the real one: the world fixture mocks it
    monkeypatch.setattr(aggregator.meteoalarm, "_collect", collect)
    monkeypatch.setattr(aggregator.meteoalarm_cap, "_collect", collect_document)
    monkeypatch.setattr(aggregator.dwd, "fetch_status", AsyncMock(return_value=Fetched(samples.dwd_status())))
    monkeypatch.setattr(aggregator.eccc, "fetch_alerts", AsyncMock(return_value=Fetched(samples.eccc_alerts())))
    monkeypatch.setattr(aggregator.metservice, "fetch_feed", AsyncMock(return_value=Fetched(samples.empty_metservice())))
    monkeypatch.setattr(aggregator.vigicrues, "fetch_flow", AsyncMock(return_value=Fetched(samples.all_green_flow())))
    monkeypatch.setattr(aggregator.swic, "fetch_source", AsyncMock(side_effect=CollectorUnavailableError("not in this test")))
    monkeypatch.setattr(aggregator, "events_answers", EventsAnswers())
    yield up
    reset()


def world_view(world: World, **params: Any) -> bytes:
    response = world.client.get("/v1/events", params=params)
    assert response.status_code == 200
    return response.content


def settle(world: World) -> bytes:
    """Ask until the answer is kept: the first calls read the CAP documents 40 at a time, and an answer that holds a part of them is not kept."""
    for _ in range(10):
        body = world_view(world)
        if world.aggregator.events_answers.hits or len(world.aggregator.events_answers):
            return body
    raise AssertionError("the world view never settled")


def spy_world_reads(world: World, monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """How many times the answer was really worked out: the warnings of the world are read once per computation."""
    calls: list[int] = []
    original = world.aggregator.vigilances.for_world

    async def counting(*args: Any, **kwargs: Any) -> Any:
        calls.append(1)
        return await original(*args, **kwargs)

    monkeypatch.setattr(world.aggregator.vigilances, "for_world", counting)
    return calls


def test_the_world_is_served_again_without_being_worked_out_while_nothing_changed(
    world: World, upstream: Upstream, sim_clock: SimClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = settle(world)
    worked = spy_world_reads(world, monkeypatch)
    asked = sum(upstream.asked.values())
    for _ in range(3):
        sim_clock.advance(5)
        assert world_view(world) == first  # byte for byte: nothing was worked out again
    assert worked == [] and sum(upstream.asked.values()) == asked
    assert world.aggregator.events_answers.hits >= 3


def test_what_is_served_is_what_a_fresh_computation_gives(world: World, upstream: Upstream) -> None:
    """The kept bytes are those of the answer worked out (``to_json`` of the model, as FastAPI writes it), whatever the parameters."""
    for params in ({}, {"compact": True}, {"country": "FR"}, {"country": "FR", "compact": True, "min_impact": 20}, {"families": "rain-flood,wind"}):
        served = world_view(world, **params)
        again = world_view(world, **params)
        asked = {
            "country": params.get("country"),
            "languages": None,
            "compact": bool(params.get("compact")),
            "min_impact": params.get("min_impact"),
            "families": params["families"].split(",") if "families" in params else None,
        }
        model = asyncio.run(world.aggregator.events(**asked))
        assert served == again == to_json(model, by_alias=True)
        assert json.loads(served)["query"]["compact"] is asked["compact"]


def test_a_feed_that_is_read_again_changes_the_answer_at_once(world: World, upstream: Upstream, sim_clock: SimClock) -> None:
    before = settle(world)
    dropped = json.loads(before)["events"][0]["alert_ids"][0]
    upstream.dropped.add(dropped)
    sim_clock.advance(FEED_TTL_S - 1)
    assert world_view(world) == before  # the feeds are still fresh: a new call would have sent nothing and said the same
    sim_clock.advance(2)  # now they have expired
    after = world_view(world)
    assert after != before
    assert dropped not in [alert for event in json.loads(after)["events"] for alert in event["alert_ids"]]
    assert dropped in [alert for event in json.loads(before)["events"] for alert in event["alert_ids"]]


def test_an_entry_replaced_by_another_reader_before_it_expires_changes_the_answer(world: World, upstream: Upstream, sim_clock: SimClock) -> None:
    """Another route reads a feed again before the world view would: the entry of the collector is a new one, and so is the answer."""
    before = settle(world)
    dropped = json.loads(before)["events"][0]["alert_ids"][0]
    upstream.dropped.add(dropped)
    sim_clock.advance(FEED_TTL_S // 2)  # nothing has expired
    assert world_view(world) == before
    country = next(c for c in samples.FEED_FILES if any(dropped == v.id for v in samples.feed(c).vigilances))
    world.aggregator.meteoalarm._record_success(1.0, f"atom:{country}", upstream.feed(country), FEED_TTL_S)  # what another reader's refresh does
    after = world_view(world)
    assert after != before and dropped not in [alert for event in json.loads(after)["events"] for alert in event["alert_ids"]]


def test_a_warning_that_ends_leaves_the_answer_when_it_ends_not_when_the_feeds_expire(world: World, upstream: Upstream, sim_clock: SimClock) -> None:
    ends = samples.RECORDED_AT + 60
    short = samples.warning("FR999", identifier="short", expires=ends, onset=samples.RECORDED_AT - 600)
    upstream.extra["FR"] = [short]
    settle(world)
    assert short.id in world_view(world).decode()
    sim_clock.advance(61)  # past its end, well inside the 180 s of the feeds
    assert short.id not in world_view(world).decode()


def test_a_failed_read_that_is_tried_again_is_not_kept(
    world: World, upstream: Upstream, sim_clock: SimClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One feed fails while the others answer (they close the circuit they share): every call tries it again, as without the cache."""
    settle(world)
    worked = spy_world_reads(world, monkeypatch)
    upstream.failing.add("CH")
    sim_clock.advance(FEED_TTL_S + 1)
    for _ in range(3):
        world_view(world)
    assert len(worked) == 3 and upstream.asked["CH"] >= 3
    assert world.aggregator.meteoalarm.circuit_state == CircuitState.CLOSED
    upstream.failing.clear()
    sim_clock.advance(1)
    recovered = world_view(world)  # the source is back: its warnings are in the very next answer
    assert len(worked) == 4 and json.loads(recovered)["events"]


def test_an_open_circuit_is_kept_for_what_it_lasts(
    world: World, upstream: Upstream, sim_clock: SimClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    settle(world)
    worked = spy_world_reads(world, monkeypatch)
    upstream.fail_all = True  # every feed fails from now on: the third failure opens the circuit
    sim_clock.advance(FEED_TTL_S + 1)
    world_view(world)
    collector = world.aggregator.meteoalarm
    assert collector.circuit_state == CircuitState.OPEN
    asked = sum(upstream.asked.values())
    kept = world_view(world)  # no request is sent while the circuit is open, so the answer holds until it would try again
    sim_clock.advance(1)
    assert world_view(world) == kept and sum(upstream.asked.values()) == asked and len(worked) == 1
    collector._record_success(1.0, "atom:XX", MeteoAlarmFeed(country="XX", updated_utc=None, vigilances=()), FEED_TTL_S)  # another feed answers
    assert collector.circuit_state == CircuitState.CLOSED
    world_view(world)
    assert len(worked) == 2 and sum(upstream.asked.values()) > asked  # the failing feeds are tried again at once, not at the end of the kept answer
    upstream.fail_all = False


def test_every_set_of_parameters_has_its_own_answer_and_their_number_is_bounded(world: World, upstream: Upstream) -> None:
    answers = world.aggregator.events_answers = EventsAnswers(max_entries=3)
    settle(world)
    bodies = {impact: world_view(world, min_impact=impact, compact=True) for impact in (0, 10, 20, 30, 40, 50)}
    assert len(answers) <= 3
    assert len({json.dumps(json.loads(body)["query"], sort_keys=True) for body in bodies.values()}) == 6
    assert json.loads(bodies[40])["query"]["min_impact"] == 40 and all(e["impact"] >= 40 for e in json.loads(bodies[40])["events"])


def test_the_total_weight_is_bounded(world: World, upstream: Upstream) -> None:
    answers = world.aggregator.events_answers = EventsAnswers(max_entries=50, max_bytes=100_000)
    for impact in range(0, 60, 5):
        world_view(world, min_impact=impact)
    assert answers.size_bytes() <= 100_000 or len(answers) == 1


@pytest.mark.asyncio
async def test_callers_that_arrive_together_share_one_computation(world: World, upstream: Upstream, monkeypatch: pytest.MonkeyPatch) -> None:
    aggregator = world.aggregator
    for _ in range(3):  # fill the documents, as ``settle`` does
        await aggregator.events_body(None, None, False, None, None)
    aggregator.events_answers = EventsAnswers()
    worked = spy_world_reads(world, monkeypatch)
    bodies = await asyncio.gather(*(aggregator.events_body(None, None, False, None, None) for _ in range(20)))
    assert len(worked) == 1 and len({*bodies}) == 1


def test_a_change_of_state_outside_the_collectors_is_not_served_old(world: World, upstream: Upstream, monkeypatch: pytest.MonkeyPatch) -> None:
    settle(world)
    answers = world.aggregator.events_answers
    hits = answers.hits
    world_view(world)
    assert answers.hits == hits + 1
    monkeypatch.setattr(world.aggregator.vigilances, "_registry", ())  # the registry of the WMO services was read again
    world_view(world)
    assert answers.hits == hits + 1  # worked out again, not served from what was kept


def test_documents_still_to_be_read_hold_the_answer_back(world: World, upstream: Upstream, monkeypatch: pytest.MonkeyPatch) -> None:
    """At most 40 new CAP documents are read per request and the others come with the next one: an answer made while some wait is not kept."""
    upstream.extra["FR"] = [
        samples.warning(f"FR{900 + n}", identifier=f"doc{n}", link=f"{CAP_URL_PREFIX}doc-{n}", onset=samples.RECORDED_AT - 60, expires=samples.RECORDED_AT + 90_000)
        for n in range(60)
    ]  # sixty warnings whose CAP documents are not held yet
    worked = spy_world_reads(world, monkeypatch)
    world_view(world)
    answers = world.aggregator.events_answers
    assert len(answers) == 0  # 40 documents were read, 20 wait for the next request: this answer is not kept
    for _ in range(10):
        if len(answers):
            break
        world_view(world)
    assert len(answers) == 1 and len(worked) >= 2  # every document is held at last: the answer is kept
    count = len(worked)
    world_view(world)
    assert len(worked) == count


def test_the_hazards_are_read_through_the_same_witness(world: World, upstream: Upstream, sim_clock: SimClock, monkeypatch: pytest.MonkeyPatch) -> None:
    """USGS answers through its real cache: while that entry is fresh the answer is kept, and a new reading replaces it at once."""
    from tests import samples as hazard_samples

    usgs = world.aggregator.usgs
    magnitude = {"value": 5.1}
    asked: list[int] = []

    async def collect(client: Any, **kwargs: Any) -> list[Any]:
        asked.append(1)
        return [hazard_samples.earthquake("usgs_ca1", SeverityLevel.YELLOW, value=magnitude["value"], lat=36.0, lon=-120.0, tsunami=False)]

    monkeypatch.delattr(usgs, "fetch")  # the real one: the world fixture mocks it
    monkeypatch.setattr(usgs, "_collect", collect)
    first = settle(world)
    assert "usgs_ca1" in first.decode() and len(asked) == 1
    magnitude["value"] = 6.4
    sim_clock.advance(usgs.ttl_seconds - 1)
    assert world_view(world) == first and len(asked) == 1  # still fresh: nothing was asked, the answer is the same
    sim_clock.advance(2)
    changed = world_view(world)
    assert len(asked) == 2 and changed != first and "6.4" in changed.decode()


def test_health_says_how_the_answers_were_served(world: World, upstream: Upstream) -> None:
    settle(world)
    world_view(world)
    health = world.client.get("/v1/health").json()["events_cache"]
    assert set(health) == {"kept", "bytes", "hits", "misses", "not_kept"}
    assert health["kept"] == 1 and health["hits"] >= 1 and health["misses"] >= 1 and health["bytes"] > 0
