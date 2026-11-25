"""The scheduler keeps ``GET /v1/surf/overview`` ready: the every-spot answers are read again just before they expire.

Same cache entries and same requests as the route, so a day holds at most as many requests as without it (``test_surf_sobriety.py``),
and no client waits for a request any more. Nothing is read for an overview nobody asked for. The providers are simulated.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from app.collectors.surf import SurfForecastCollector
from app.services import surf
from app.services.scheduler import BackgroundAlertScheduler
from tests.helpers import Provider, SimClock

FIXTURES = Path(__file__).parent / "fixtures" / "surf"
SWELL = json.loads((FIXTURES / "mfwam-hossegor.json").read_text(encoding="utf-8"))
WIND = json.loads((FIXTURES / "wind-hossegor.json").read_text(encoding="utf-8"))
DAY_S = 86_400
CYCLE_S = 90
SPOTS = ("a", "b", "c")
SPOT_FILE: dict[str, Any] = {
    "built_utc": "2026-10-28T22:00:00Z",
    "licence": "ODbL-1.0",
    "spots": [
        {"id": spot, "name": spot, "lat": 43.66 + i / 10, "lon": -1.44, "facing_deg": 280, "osm": f"way/{i}", "evidence": []}
        for i, spot in enumerate(SPOTS)
    ],
}


def answer(request: httpx.Request) -> httpx.Response:
    one = SWELL if "marine" in request.url.host else WIND
    lats = request.url.params["latitude"].split(",")
    if len(lats) == 1:
        return httpx.Response(200, json={**one, "latitude": float(lats[0])})
    return httpx.Response(
        200, json=[{**one, **({} if i == 0 else {"location_id": i}), "latitude": float(lat)} for i, lat in enumerate(lats)]
    )


@pytest.fixture()
def spots(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(surf, "spot_file", lambda: SPOT_FILE)
    monkeypatch.setattr(surf, "_overview_memo", None)


def make(provider: Provider) -> tuple[SurfForecastCollector, BackgroundAlertScheduler]:
    collector = SurfForecastCollector(http_client=provider.client())
    scheduler = BackgroundAlertScheduler(aggregator=SimpleNamespace(surf=collector), ws_hub=None, interval_seconds=CYCLE_S)  # type: ignore[arg-type]
    return collector, scheduler


def times(provider: Provider, sim_clock: SimClock, marine: bool, log: list[tuple[float, bool]]) -> list[float]:
    """The simulated time of each request of one kind (``log`` is ``(time, marine)`` filled by ``tick``)."""
    return [t for t, is_marine in log if is_marine is marine]


async def run(
    provider: Provider, sim_clock: SimClock, scheduler: BackgroundAlertScheduler, collector: SurfForecastCollector,
    seconds: int, clients_every: int | None,
) -> tuple[list[tuple[float, bool]], int]:
    """Advance the clock cycle by cycle: the scheduler, then (every ``clients_every`` seconds) a client's overview.

    Returns the time and kind of every request, and how many requests a client's read itself caused.
    """
    log: list[tuple[float, bool]] = []
    paid_by_clients = 0
    seen = 0
    for step in range(seconds // CYCLE_S):
        await scheduler.run_warm_cycle()
        if clients_every is not None and (step * CYCLE_S) % clients_every == 0:
            before = provider.count
            await surf.overview(collector, sim_clock.time())
            paid_by_clients += provider.count - before
        log.extend((sim_clock.now, "marine" in request.url.host) for request in provider.requests[seen:])
        seen = provider.count
        sim_clock.advance(CYCLE_S)
    return log, paid_by_clients


@pytest.mark.asyncio
async def test_no_client_waits_for_the_overview_after_the_first_and_a_day_costs_no_more_requests(sim_clock: SimClock, spots: None) -> None:
    provider = Provider(answer=answer, latency_ticks=0)
    collector, scheduler = make(provider)
    log, paid = await run(provider, sim_clock, scheduler, collector, DAY_S, clients_every=600)
    first = len(provider.requests)
    assert paid == 2  # the very first client asked for the swell and the wind itself, nobody after
    for marine in (True, False):
        asked = times(provider, sim_clock, marine, log)
        assert len(asked) <= DAY_S // SurfForecastCollector.SWELL_TTL_SECONDS + 1  # at most 9 a day, as before
        gaps = [b - a for a, b in zip(asked, asked[1:], strict=False)]
        assert all(gap < SurfForecastCollector.SWELL_TTL_SECONDS for gap in gaps)  # always before the entry expires
        assert all(gap >= SurfForecastCollector.SWELL_TTL_SECONDS - SurfForecastCollector.WARM_LEAD_SECONDS - CYCLE_S for gap in gaps)
    assert first >= 2


@pytest.mark.asyncio
async def test_it_asks_exactly_what_the_route_asks(sim_clock: SimClock, spots: None) -> None:
    """The warm-up reads the same entries by the same request: a client's read after it sends none and gives the same answer."""
    provider = Provider(answer=answer, latency_ticks=0)
    collector, scheduler = make(provider)
    await surf.overview(collector, sim_clock.time())
    asked_by_client = [dict(r.url.params) for r in provider.requests]
    sim_clock.advance(SurfForecastCollector.SWELL_TTL_SECONDS - 100)
    assert await scheduler.run_warm_cycle() is True
    warmed = [dict(r.url.params) for r in provider.requests[len(asked_by_client):]]
    assert sorted(map(json.dumps, warmed)) == sorted(map(json.dumps, asked_by_client))  # same URLs, same parameters
    before = provider.count
    sim_clock.advance(500)  # past the old expiry
    after = await surf.overview(collector, sim_clock.time())
    assert provider.count == before and after.stale is False and after.count == len(SPOTS)


@pytest.mark.asyncio
async def test_nothing_is_read_for_an_overview_nobody_asked_for(sim_clock: SimClock, spots: None) -> None:
    provider = Provider(answer=answer, latency_ticks=0)
    collector, scheduler = make(provider)
    log, _ = await run(provider, sim_clock, scheduler, collector, 6 * 3600, clients_every=None)
    assert provider.count == 0 and log == []
    assert scheduler.get_status()["last_warm_run_utc"] is not None and scheduler.get_status()["last_warm_error"] is None


@pytest.mark.asyncio
async def test_it_stops_a_day_after_the_last_client(sim_clock: SimClock, spots: None) -> None:
    provider = Provider(answer=answer, latency_ticks=0)
    collector, scheduler = make(provider)
    await surf.overview(collector, sim_clock.time())
    log, _ = await run(provider, sim_clock, scheduler, collector, 3 * DAY_S, clients_every=None)
    asked = times(provider, sim_clock, True, log)
    assert asked and max(asked) <= SurfForecastCollector.WARM_DEMAND_SECONDS + 3 * 3600  # the last read the day allowed, no later one
    assert len(asked) <= DAY_S // SurfForecastCollector.SWELL_TTL_SECONDS + 1


@pytest.mark.asyncio
async def test_a_failing_provider_is_not_hammered_and_the_client_still_gets_the_old_answer(sim_clock: SimClock, spots: None) -> None:
    up = {"on": True}

    def flaky(request: httpx.Request) -> httpx.Response:
        return answer(request) if up["on"] else httpx.Response(503)

    provider = Provider(answer=flaky, latency_ticks=0)
    collector, scheduler = make(provider)
    await surf.overview(collector, sim_clock.time())
    up["on"] = False
    sim_clock.advance(SurfForecastCollector.SWELL_TTL_SECONDS - 100)
    before = provider.count
    for _ in range(40):  # an hour of cycles while the entry is about to expire and the provider is down
        await scheduler.run_warm_cycle()
        sim_clock.advance(CYCLE_S)
    tries = provider.count - before
    assert 0 < tries <= 2 * (3600 // SurfForecastCollector.WARM_RETRY_SECONDS + 1)  # a few tries (two kinds), not one per cycle
    served = await surf.overview(collector, sim_clock.time())  # the route's own policy is untouched: the stale copy, flagged
    assert served.count == len(SPOTS) and served.stale is True
    assert scheduler.get_status()["last_warm_error"] is None  # a provider that is down is not an error of the scheduler


@pytest.mark.asyncio
async def test_the_overview_of_the_new_hour_is_ready_before_the_first_visitor_of_the_hour(sim_clock: SimClock, spots: None) -> None:
    provider = Provider(answer=answer, latency_ticks=0)
    collector, scheduler = make(provider)
    first = await surf.overview(collector, sim_clock.time())
    sim_clock.advance(3600 - (sim_clock.time() % 3600) + 1)  # the next hour has begun
    assert await scheduler.run_warm_cycle() is False  # the answers are not due: only the overview is worked out again
    assert surf._overview_memo is not None and surf._overview_memo[2] == int(sim_clock.time() // 3600)
    before = provider.count
    again = await surf.overview(collector, sim_clock.time())
    assert provider.count == before and again is surf._overview_memo[3] and again.generated_utc != first.generated_utc


def test_the_loop_wakes_up_just_after_the_turn_of_the_hour(sim_clock: SimClock) -> None:
    _, scheduler = make(Provider(answer=answer, latency_ticks=0))
    sim_clock.advance(3600 - (sim_clock.time() % 3600) + 100)  # 100 s into an hour
    assert scheduler.warm_delay() == CYCLE_S
    sim_clock.advance(3600 - 100 - 30)  # 30 s before the next one
    assert scheduler.warm_delay() == pytest.approx(31.0)
    sim_clock.advance(29.5)
    assert 1.0 <= scheduler.warm_delay() <= 2.5


@pytest.mark.asyncio
async def test_start_and_stop_run_the_warm_loop(sim_clock: SimClock, spots: None) -> None:
    provider = Provider(answer=answer, latency_ticks=0)
    collector, scheduler = make(provider)
    scheduler.aggregator.build_snapshot = _never  # type: ignore[attr-defined]
    scheduler.aggregator.travel = SimpleNamespace(refresh=_never)  # type: ignore[attr-defined]
    scheduler.start()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert scheduler._warm_task is not None and not scheduler._warm_task.done()
    await scheduler.stop()
    assert scheduler._warm_task is None


async def _never(*_: Any) -> None:
    await asyncio.sleep(3600)
