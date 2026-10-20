"""The status of the watched points: what happened near them, and whether "nothing" can be said (``/v1/watched-points/status``).

**Events.** An event is near a point when it lies within the point's radius. The window is the last ``days`` days. An earthquake
counts by the time it happened; any other hazard (a storm, a flood, a volcano in alert, a tsunami warning) lasts, so it counts when
the API saw it active in the window or sees it now. The events come from the history of hazards the API keeps
(``hazards_history``) and from the hazards active now. A hazard that is a point is compared by its position: the extent of a storm
or of a flood is not known here, so a large phenomenon whose centre is farther than the radius is not listed.

**"Nothing happened" is a claim that needs proof** (the principle of [ADR 0003](../../docs/decisions/0003-national-source-preferred-rivers-are-lines.md),
point 6: a source that did not answer says nothing). A point with no event is ``quiet`` only if

1. every hazard source answered at the last reading (a stale answer served after a failure does not count), and
2. each of them has been read **without a gap** longer than ``quiet_max_gap_s`` for as long as the claim goes back: the history
   holds only what the API saw, so an instance started an hour ago cannot say "nothing in the last 7 days".

``quiet_since_utc`` is then the start of the window, or the start of the shortest run of readings when that is later.
Anything else is ``unknown``, with the sources that did not answer. No part of the world is excluded for a source (the National
Weather Service covers the United States, the Hurricane Center some oceans): the API keeps no verified map of what each source
could report where, so a source that is down stops the claim everywhere, the safe side.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Optional

from app.attribution import merge_credits
from app.models.places import WatchedPoint
from app.models.schemas import HazardAlert, HazardCategory
from app.models.watch import (
    WatchedEvent,
    WatchedPointsStatus,
    WatchedPointStatus,
    WatchedSource,
)
from app.services.geodesy import haversine_km

SECONDS_PER_DAY = 86400
MAX_WINDOW_DAYS = 30
DEFAULT_WINDOW_DAYS = 7
MAX_EVENTS_PER_POINT = 20
PERSIST_EVERY_S = 30  # the last good read of a source is written to the database at most this often


class SourceReads:
    """For each hazard source: did it answer at the last reading, and since when has it been read without a gap.

    A *run* starts at the first good read, and goes on while two good reads are at most ``max_gap_s`` apart. A longer silence ends it:
    the next good read starts a new one, because what happened in the silence was not seen. Kept in memory and written by the caller
    (``dirty``): a restart of the API does not forget a run, and a long stop breaks it, as it should.
    """

    def __init__(self, runs: Optional[Mapping[str, tuple[int, int]]] = None, max_gap_s: int = 900) -> None:
        self.max_gap_s = max_gap_s
        self.runs: dict[str, tuple[int, int]] = dict(runs or {})  # source -> (start of the run, last good read)
        self.answered: dict[str, bool] = {}
        self._persisted_utc = 0
        self._restarted = False

    def record(self, answered: Mapping[str, bool], now_utc: int) -> None:
        """Note the outcome of a reading of every source at ``now_utc``."""
        for source, ok in answered.items():
            self.answered[source] = ok
            if not ok:
                continue
            since, last = self.runs.get(source, (now_utc, now_utc))
            if source not in self.runs or now_utc - last > self.max_gap_s:
                since = now_utc
                self._restarted = True
            self.runs[source] = (since, max(last, now_utc))

    def dirty(self, now_utc: int) -> bool:
        """True when the runs should be written: one started again, or the last write is old."""
        return self._restarted or now_utc - self._persisted_utc >= PERSIST_EVERY_S

    def persisted(self, now_utc: int) -> None:
        self._persisted_utc = now_utc
        self._restarted = False

    def run_since(self, source: str) -> Optional[int]:
        run = self.runs.get(source)
        return None if run is None else run[0]


def parse_window_days(days: Optional[int]) -> int:
    """The window in days: 7 unless asked, never more than ``MAX_WINDOW_DAYS``."""
    if days is None:
        return DEFAULT_WINDOW_DAYS
    return max(1, min(MAX_WINDOW_DAYS, int(days)))


def candidate_ids(points: Iterable[WatchedPoint], positions: Iterable[tuple[str, float, float]]) -> set[str]:
    """The identifiers of the hazards of the history that lie within the radius of at least one point."""
    wanted = list(points)
    return {
        hazard_id
        for hazard_id, lat, lon in positions
        if any(haversine_km(point.lat, point.lon, lat, lon) <= point.radius_km for point in wanted)
    }


def _event(hazard: HazardAlert, distance_km: float, ongoing: bool) -> WatchedEvent:
    return WatchedEvent(
        id=hazard.id,
        category=hazard.category,
        severity=hazard.severity,
        title=hazard.title,
        source=hazard.source,
        sources=list(hazard.sources),
        timestamp_utc=hazard.timestamp_utc,
        lat=hazard.lat,
        lon=hazard.lon,
        distance_km=round(distance_km, 1),
        value_primary=hazard.value_primary,
        value_unit=hazard.value_unit,
        ongoing=ongoing,
        attribution=hazard.attribution,
    )


def build_status(
    points: Iterable[WatchedPoint],
    history: Iterable[HazardAlert],
    active: Iterable[HazardAlert],
    sources: Iterable[WatchedSource],
    now_utc: int,
    days: int = DEFAULT_WINDOW_DAYS,
) -> WatchedPointsStatus:
    """The status of every point, from the history, the hazards active now and the state of the sources."""
    window_start = now_utc - days * SECONDS_PER_DAY
    source_list = list(sources)
    unreachable = [s.provider_id for s in source_list if not s.answered]
    run_starts = [s.observed_since_utc for s in source_list]
    observed_since = None if any(start is None for start in run_starts) or not run_starts else max(start for start in run_starts if start is not None)

    active_by_id = {h.id: h for h in active}
    pool: dict[str, HazardAlert] = {}
    for hazard in history:
        if hazard.id not in active_by_id:
            pool[hazard.id] = hazard
    pool.update(active_by_id)  # what is active now is the freshest account of an event

    statuses: list[WatchedPointStatus] = []
    credits: list[Optional[str]] = []
    for point in points:
        found: list[WatchedEvent] = []
        for hazard in pool.values():
            ongoing = hazard.id in active_by_id
            if hazard.category == HazardCategory.EARTHQUAKE and hazard.timestamp_utc < window_start:
                continue  # an earthquake is an instant: the one of last month is not in this window, whatever feed still lists it
            distance = haversine_km(point.lat, point.lon, hazard.lat, hazard.lon)
            if distance <= point.radius_km:
                found.append(_event(hazard, distance, ongoing))
        found.sort(key=lambda e: (e.timestamp_utc, e.id), reverse=True)
        if found:
            state = "events"
            quiet_since = None
        elif unreachable or observed_since is None:
            state = "unknown"
            quiet_since = None
        else:
            state = "quiet"
            quiet_since = max(window_start, observed_since)
        credits.extend(event.attribution for event in found[:MAX_EVENTS_PER_POINT])
        statuses.append(
            WatchedPointStatus(
                point=point,
                state=state,  # type: ignore[arg-type]
                events_count=len(found),
                events=found[:MAX_EVENTS_PER_POINT],
                quiet_since_utc=quiet_since,
                window_covered=None if quiet_since is None else quiet_since <= window_start,
                unreachable_sources=unreachable,
            )
        )
    return WatchedPointsStatus(
        generated_utc=now_utc,
        window_days=days,
        window_start_utc=window_start,
        observed_since_utc=observed_since,
        sources=source_list,
        points=statuses,
        attribution=merge_credits(*credits),
    )
