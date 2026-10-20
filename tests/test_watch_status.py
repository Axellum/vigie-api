"""``GET /v1/watched-points/status``: what happened near a point, and when "nothing happened" may be said.

The principle (ADR 0003 point 6, ``docs/decisions/0008-sensitive-points.md``): a source that did not answer says nothing. A point
with no event is ``quiet`` only if every hazard source answered at the last reading **and** has been read without a gap for as long
as the claim goes back. The hazards are the real earthquake of Panama (``tests/fixtures/places``), with its event time moved into
the window where a test needs it.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Optional
from unittest.mock import AsyncMock

import pytest

from app.models.places import WatchedPoint
from app.models.schemas import HazardAlert, HazardCategory, SeverityLevel, SimulationRequest, SimulationScenario
from app.models.watch import WatchedSource
from app.services.storage_wal import SQLiteWALStorage
from app.services.watch import (
    DEFAULT_WINDOW_DAYS,
    MAX_EVENTS_PER_POINT,
    MAX_WINDOW_DAYS,
    PERSIST_EVERY_S,
    SourceReads,
    build_status,
    candidate_ids,
    parse_window_days,
)
from tests import places_samples as real
from tests.conftest import World

pytestmark = pytest.mark.usefixtures("watched_points_write_on")  # the points are written through the routes

DAY = 86400
NOW = 1_792_500_000  # a fixed "now" for the pure tests


def quake_at(timestamp_utc: int, event_id: str = "usgs_us6000u18k") -> HazardAlert:
    """The real M 7.7 earthquake of Panama (id, position, magnitude, title), its event time moved to ``timestamp_utc``."""
    return real.usgs_earthquake(real.PANAMA_M77).model_copy(update={"timestamp_utc": timestamp_utc, "id": event_id})


def point(name: str = "Camp", radius_km: float = 50.0, lat: float = 7.62, lon: float = -80.80, point_id: str = "p1") -> WatchedPoint:
    return WatchedPoint(id=point_id, name=name, kind="place", lat=lat, lon=lon, radius_km=radius_km)  # 5 km from the epicentre


def sources(*, observed_since: Optional[int] = NOW - 10 * DAY, down: tuple[str, ...] = ()) -> list[WatchedSource]:
    names = ["usgs_eq", "emsc_seismic", "gdacs_global", "noaa_nws", "usgs_volcano_gvp", "nhc_storms", "tsunami_warning_centers"]
    return [
        WatchedSource(provider_id=n, name=n, answered=n not in down, observed_since_utc=observed_since if n not in down else observed_since)
        for n in names
    ]


# ============================================================================
# 1. The runs of readings
# ============================================================================


def test_a_run_starts_at_the_first_good_read_and_goes_on_while_the_reads_are_close() -> None:
    reads = SourceReads(max_gap_s=900)
    reads.record({"usgs_eq": True, "emsc_seismic": False}, NOW)
    assert reads.run_since("usgs_eq") == NOW and reads.run_since("emsc_seismic") is None  # a source never read has no run
    assert reads.answered == {"usgs_eq": True, "emsc_seismic": False}
    for step in range(1, 40):  # every 90 seconds for an hour: the scheduler's rhythm
        reads.record({"usgs_eq": True}, NOW + 90 * step)
    assert reads.run_since("usgs_eq") == NOW and reads.runs["usgs_eq"][1] == NOW + 90 * 39


def test_a_silence_longer_than_the_gap_ends_the_run() -> None:
    reads = SourceReads(max_gap_s=900)
    reads.record({"usgs_eq": True}, NOW)
    reads.record({"usgs_eq": True}, NOW + 900)  # exactly the gap: still the same run
    assert reads.run_since("usgs_eq") == NOW
    reads.record({"usgs_eq": True}, NOW + 900 + 901)  # one second more: what happened in between was not seen
    assert reads.run_since("usgs_eq") == NOW + 1801


def test_a_failed_read_does_not_extend_the_run_and_a_long_failure_ends_it() -> None:
    reads = SourceReads(max_gap_s=900)
    reads.record({"usgs_eq": True}, NOW)
    reads.record({"usgs_eq": False}, NOW + 300)
    assert reads.answered["usgs_eq"] is False and reads.runs["usgs_eq"] == (NOW, NOW)  # the last GOOD read stays
    reads.record({"usgs_eq": True}, NOW + 600)  # back within the gap: the run goes on
    assert reads.run_since("usgs_eq") == NOW
    reads.record({"usgs_eq": False}, NOW + 700)
    reads.record({"usgs_eq": True}, NOW + 600 + 3600)  # an hour after the last good read
    assert reads.run_since("usgs_eq") == NOW + 4200


def test_the_runs_are_written_when_one_starts_and_then_every_half_minute() -> None:
    reads = SourceReads()
    reads.record({"usgs_eq": True}, NOW)
    assert reads.dirty(NOW)  # a run started
    reads.persisted(NOW)
    reads.record({"usgs_eq": True}, NOW + 10)
    assert not reads.dirty(NOW + 10)
    assert reads.dirty(NOW + PERSIST_EVERY_S)  # the last good read is written now and then, so a restart finds it
    reads.persisted(NOW + PERSIST_EVERY_S)
    reads.record({"usgs_eq": True}, NOW + 7200)  # the run is broken: written at once
    assert reads.dirty(NOW + 7200)


def test_a_restart_goes_on_with_the_run_it_finds_and_a_long_stop_breaks_it(tmp_path: Path) -> None:
    storage = SQLiteWALStorage(db_path=tmp_path / "runs.db")
    reads = SourceReads(max_gap_s=900)
    reads.record({"usgs_eq": True}, NOW)
    reads.record({"usgs_eq": True}, NOW + 600)
    storage.save_source_runs(reads.runs)

    quick_restart = SourceReads(storage.load_source_runs(), 900)
    quick_restart.record({"usgs_eq": True}, NOW + 1000)  # the API was down for 400 s
    assert quick_restart.run_since("usgs_eq") == NOW

    long_stop = SourceReads(storage.load_source_runs(), 900)
    long_stop.record({"usgs_eq": True}, NOW + 3 * DAY)  # the API was down for three days
    assert long_stop.run_since("usgs_eq") == NOW + 3 * DAY


# ============================================================================
# 2. The status, from the history, the active hazards and the sources
# ============================================================================


def test_the_window_is_seven_days_unless_asked_and_never_more_than_thirty() -> None:
    assert parse_window_days(None) == DEFAULT_WINDOW_DAYS == 7
    assert [parse_window_days(d) for d in (1, 3, 30, 31, 400, 0, -2)] == [1, 3, 30, MAX_WINDOW_DAYS, MAX_WINDOW_DAYS, 1, 1]


def test_a_point_with_an_event_in_the_window_lists_it() -> None:
    status = build_status([point()], [quake_at(NOW - 2 * DAY)], [], sources(), NOW)
    [result] = status.points
    assert result.state == "events" and result.events_count == 1
    [event] = result.events
    assert event.id == "usgs_us6000u18k" and event.title.startswith("M 7.7") and event.ongoing is False
    assert event.distance_km == pytest.approx(5.0, abs=0.3) and event.value_primary == 7.7
    assert result.quiet_since_utc is None and result.window_covered is None
    assert status.window_days == 7 and status.window_start_utc == NOW - 7 * DAY


def test_an_earthquake_is_an_instant_the_one_of_nine_days_ago_is_not_in_the_window() -> None:
    old = quake_at(NOW - 9 * DAY)
    status = build_status([point()], [old], [old], sources(), NOW)  # even when a feed still lists it as active
    [result] = status.points
    assert result.state == "quiet" and result.events_count == 0


def test_a_lasting_hazard_counts_when_it_was_seen_in_the_window_whenever_it_began() -> None:
    storm = quake_at(NOW - 20 * DAY, "gdacs_tc_1").model_copy(update={"category": HazardCategory.HURRICANE})
    status = build_status([point()], [storm], [], sources(), NOW)
    assert status.points[0].state == "events"  # the history only holds what was seen in the window (see storage.hazard_positions_since)
    ongoing = build_status([point()], [], [storm], sources(), NOW)
    assert ongoing.points[0].events[0].ongoing is True


def test_an_event_outside_the_radius_is_not_listed() -> None:
    inside, outside = point("Inside", 50.0), point("Outside", 4.0, point_id="p2")  # the epicentre is 5 km from both
    status = build_status([inside, outside], [quake_at(NOW - DAY)], [], sources(), NOW)
    assert [(p.point.name, p.state, p.events_count) for p in status.points] == [("Inside", "events", 1), ("Outside", "quiet", 0)]


def test_the_events_are_the_most_recent_first_and_capped_with_the_full_count() -> None:
    events = [quake_at(NOW - 3600 * n, f"usgs_{n}") for n in range(1, MAX_EVENTS_PER_POINT + 6)]
    [result] = build_status([point()], events, [], sources(), NOW).points
    assert result.events_count == len(events) and len(result.events) == MAX_EVENTS_PER_POINT
    assert [e.timestamp_utc for e in result.events] == sorted((e.timestamp_utc for e in result.events), reverse=True)


def test_what_is_active_now_is_the_freshest_account_of_an_event() -> None:
    old = quake_at(NOW - DAY).model_copy(update={"severity": SeverityLevel.ORANGE})
    now = quake_at(NOW - DAY).model_copy(update={"severity": SeverityLevel.RED})
    [result] = build_status([point()], [old], [now], sources(), NOW).points
    assert result.events_count == 1 and result.events[0].severity == SeverityLevel.RED and result.events[0].ongoing is True


def test_quiet_needs_every_source_answering_and_read_without_a_gap_over_the_whole_window() -> None:
    status = build_status([point()], [], [], sources(observed_since=NOW - 10 * DAY), NOW)
    [result] = status.points
    assert result.state == "quiet" and result.quiet_since_utc == NOW - 7 * DAY and result.window_covered is True
    assert result.unreachable_sources == [] and status.observed_since_utc == NOW - 10 * DAY


def test_an_instance_watching_for_two_days_cannot_say_seven() -> None:
    [result] = build_status([point()], [], [], sources(observed_since=NOW - 2 * DAY), NOW).points
    assert result.state == "quiet" and result.quiet_since_utc == NOW - 2 * DAY and result.window_covered is False


def test_the_shortest_run_of_the_sources_decides() -> None:
    uneven = sources(observed_since=NOW - 10 * DAY)
    uneven[3] = uneven[3].model_copy(update={"observed_since_utc": NOW - 3600})  # one source was out for a day and came back an hour ago
    [result] = build_status([point()], [], [], uneven, NOW).points
    assert result.quiet_since_utc == NOW - 3600 and result.window_covered is False


def test_a_source_that_did_not_answer_makes_the_point_unknown_not_quiet() -> None:
    status = build_status([point()], [], [], sources(down=("emsc_seismic",)), NOW)
    [result] = status.points
    assert result.state == "unknown" and result.unreachable_sources == ["emsc_seismic"]
    assert result.quiet_since_utc is None and result.window_covered is None


def test_a_source_never_read_makes_the_point_unknown() -> None:
    never = sources(observed_since=None)
    [result] = build_status([point()], [], [], never, NOW).points
    assert result.state == "unknown" and status_observed(never) is None


def status_observed(listed: list[WatchedSource]) -> Optional[int]:
    return build_status([], [], [], listed, NOW).observed_since_utc


def test_with_an_event_and_a_source_down_the_event_is_still_told_and_the_gap_too() -> None:
    [result] = build_status([point()], [quake_at(NOW - DAY)], [], sources(down=("gdacs_global",)), NOW).points
    assert result.state == "events" and result.unreachable_sources == ["gdacs_global"]  # the list may be incomplete: the client can say so


def test_without_a_point_there_is_only_the_state_of_the_sources() -> None:
    status = build_status([], [quake_at(NOW - DAY)], [], sources(down=("nhc_storms",)), NOW)
    assert status.points == [] and [s.provider_id for s in status.sources if not s.answered] == ["nhc_storms"]


def test_the_credit_of_the_events_listed_goes_with_the_status() -> None:
    status = build_status([point()], [quake_at(NOW - DAY)], [], sources(), NOW)
    assert status.attribution is not None and "U.S. Geological Survey" in status.attribution
    assert build_status([point()], [], [], sources(), NOW).attribution is None  # nothing listed: nothing to credit


def test_the_candidates_are_the_history_entries_near_a_point() -> None:
    positions = [("near", 7.6, -80.8), ("far", 40.0, 10.0), ("edge", 7.6 + 60.0 / 111.195, -80.8)]
    assert candidate_ids([point(radius_km=50.0)], positions) == {"near"}
    assert candidate_ids([point(radius_km=50.0), point(radius_km=100.0, point_id="p2")], positions) == {"near", "edge"}
    assert candidate_ids([], positions) == set()


# ============================================================================
# 3. The route, with the application
# ============================================================================


def ask(world: World, **params: Any) -> dict[str, Any]:
    response = world.client.get("/v1/watched-points/status", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def watch_camp(world: World) -> None:
    response = world.client.post("/v1/watched-points", json={"name": "Camp", "kind": "place", "lat": 7.62, "lon": -80.80, "radius_km": 50.0})
    assert response.status_code == 200


def record(world: World, hazards: list[HazardAlert]) -> None:
    """Put hazards in the history, as a past collection did (they are not active now: the providers answer nothing)."""
    world.aggregator.storage.record_hazards(hazards)


def prove_the_sources_for(world: World, observed_since: int) -> None:
    """The runs of readings as a long-running instance has them: every source read without a gap since ``observed_since``."""
    now = int(time.time())
    reads = world.aggregator.source_reads()
    reads.runs = {c.provider_id: (observed_since, now - 30) for c in world.aggregator.hazard_collectors()}


def test_a_new_instance_cannot_say_nothing_happened_for_a_week(world: World) -> None:
    watch_camp(world)
    started = int(time.time())
    status = ask(world)
    [camp] = status["points"]
    assert camp["state"] == "quiet" and camp["window_covered"] is False  # it has watched for a moment, and says so
    assert started - 5 <= camp["quiet_since_utc"] <= int(time.time()) + 1
    assert camp["quiet_since_utc"] > status["window_start_utc"] + 6 * DAY


def test_a_point_nobody_reported_near_is_quiet_for_the_whole_window_when_the_sources_prove_it(world: World) -> None:
    watch_camp(world)
    prove_the_sources_for(world, int(time.time()) - 10 * DAY)
    status = ask(world)
    [camp] = status["points"]
    assert camp["state"] == "quiet" and camp["window_covered"] is True and camp["quiet_since_utc"] == status["window_start_utc"]
    assert camp["events"] == [] and camp["unreachable_sources"] == []
    assert [s["answered"] for s in status["sources"]] == [True] * 7
    assert status["derived"] and status["window_days"] == 7


def test_an_event_in_the_history_is_listed_with_its_distance(world: World) -> None:
    watch_camp(world)
    record(world, [quake_at(int(time.time()) - 2 * DAY)])
    prove_the_sources_for(world, int(time.time()) - 10 * DAY)
    [camp] = ask(world)["points"]
    assert camp["state"] == "events" and camp["events_count"] == 1
    assert camp["events"][0]["title"].startswith("M 7.7") and camp["events"][0]["distance_km"] == pytest.approx(5.0, abs=0.3)
    assert camp["events"][0]["ongoing"] is False and camp["quiet_since_utc"] is None


def test_the_window_can_be_shortened_and_must_be_sane(world: World) -> None:
    watch_camp(world)
    record(world, [quake_at(int(time.time()) - 2 * DAY)])
    assert ask(world, days=1)["points"][0]["state"] != "events"
    assert ask(world, days=3)["points"][0]["state"] == "events" and ask(world, days=3)["window_days"] == 3
    for days in (0, 31, -1):
        assert world.client.get("/v1/watched-points/status", params={"days": days}).status_code == 422
    assert world.client.get("/v1/watched-points/status", params={"days": "week"}).status_code == 422


def test_an_old_earthquake_and_an_old_storm_are_not_in_the_window(world: World) -> None:
    watch_camp(world)
    now = int(time.time())
    storm = quake_at(now - 12 * DAY, "gdacs_tc_old").model_copy(update={"category": HazardCategory.HURRICANE})
    record(world, [quake_at(now - 9 * DAY, "usgs_old_quake"), storm])
    with world.aggregator.storage._connect() as conn:  # the storm was last seen active 20 days ago
        conn.execute("UPDATE hazards_history SET updated_utc = ? WHERE id = ?", (now - 20 * DAY, "gdacs_tc_old"))
        conn.commit()
    prove_the_sources_for(world, now - 10 * DAY)
    assert ask(world)["points"][0]["state"] == "quiet"
    with world.aggregator.storage._connect() as conn:  # seen active two days ago: it counts, whenever it began
        conn.execute("UPDATE hazards_history SET updated_utc = ? WHERE id = ?", (now - 2 * DAY, "gdacs_tc_old"))
        conn.commit()
    [camp] = ask(world)["points"]
    assert camp["state"] == "events" and [e["id"] for e in camp["events"]] == ["gdacs_tc_old"]


def test_a_source_down_makes_the_point_unknown_and_names_it(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    watch_camp(world)
    prove_the_sources_for(world, int(time.time()) - 10 * DAY)
    monkeypatch.setattr(world.aggregator.emsc, "fetch", AsyncMock(side_effect=RuntimeError("EMSC is down")))
    status = ask(world)
    [camp] = status["points"]
    assert camp["state"] == "unknown" and camp["unreachable_sources"] == ["emsc_seismic"] and camp["quiet_since_utc"] is None
    assert [s["provider_id"] for s in status["sources"] if not s["answered"]] == ["emsc_seismic"]


def test_an_answer_served_from_the_cache_after_a_failure_is_not_an_answer(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    """A collector that fails keeps giving its last value: the list comes back, but the source did not answer."""
    watch_camp(world)
    prove_the_sources_for(world, int(time.time()) - 10 * DAY)
    monkeypatch.setattr(world.aggregator.gdacs, "consecutive_failures", 2)  # the collector has failed twice since its last good read
    [camp] = ask(world)["points"]
    assert camp["state"] == "unknown" and camp["unreachable_sources"] == ["gdacs_global"]


def test_the_source_comes_back_and_the_point_is_quiet_again_with_the_run_unbroken(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    watch_camp(world)
    prove_the_sources_for(world, int(time.time()) - 10 * DAY)
    monkeypatch.setattr(world.aggregator.gdacs, "consecutive_failures", 1)
    assert ask(world)["points"][0]["state"] == "unknown"
    monkeypatch.setattr(world.aggregator.gdacs, "consecutive_failures", 0)
    [camp] = ask(world)["points"]
    assert camp["state"] == "quiet" and camp["window_covered"] is True  # it was away for seconds: the run goes on


def test_an_event_is_told_even_when_a_source_is_down(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    watch_camp(world)
    record(world, [quake_at(int(time.time()) - DAY)])
    monkeypatch.setattr(world.aggregator.nhc, "fetch", AsyncMock(side_effect=RuntimeError("NHC is down")))
    [camp] = ask(world)["points"]
    assert camp["state"] == "events" and camp["unreachable_sources"] == ["nhc_storms"]


def test_an_active_hazard_is_ongoing(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    watch_camp(world)
    monkeypatch.setattr(world.aggregator.usgs, "fetch", AsyncMock(return_value=[quake_at(int(time.time()) - 3600)]))
    [camp] = ask(world)["points"]
    assert camp["state"] == "events" and camp["events"][0]["ongoing"] is True


def test_a_simulated_scenario_is_never_an_event(world: World) -> None:
    sendai = world.client.post("/v1/watched-points", json={"name": "Sendai", "lat": 38.1, "lon": 142.85, "radius_km": 100.0}).json()
    world.aggregator.trigger_simulation(SimulationRequest(scenario=SimulationScenario.MEGA_TSUNAMI_JAPAN))  # an earthquake and a tsunami at Sendai
    status = ask(world)
    [result] = [p for p in status["points"] if p["point"]["id"] == sendai["id"]]
    assert result["events_count"] == 0 and result["state"] in ("quiet", "unknown")


def test_the_runs_are_kept_in_the_database(world: World) -> None:
    watch_camp(world)
    ask(world)
    runs = world.aggregator.storage.load_source_runs()
    assert sorted(runs) == sorted(c.provider_id for c in world.aggregator.hazard_collectors())
    assert all(since <= last for since, last in runs.values())


def test_the_status_of_an_instance_with_no_point_is_the_state_of_its_sources(world: World) -> None:
    status = ask(world)
    assert status["points"] == [] and len(status["sources"]) == 7 and status["attribution"] is None
