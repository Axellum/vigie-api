"""Events instead of lines (``GET /v1/events``).

The rules are in ``docs/decisions/0012-events-and-impact.md``; everything worked out here is ours and is named in ``derived``:

- **Warnings** of one country, one phenomenon (the API's own; the source's word when the phenomenon is ``other`` or unknown) and one
  level are one event when their periods overlap and their areas are close: each area within ``WARNING_LINK_KM`` of another of the
  event (the distance between the centres of the boxes around them, single linkage). An area whose shape is not known here cannot be
  placed: such areas make an event of their own, never a guessed place.
- **Earthquakes** are one sequence when each lies within ``QUAKE_LINK_KM`` and ``QUAKE_LINK_S`` of another of the sequence.
- **A tsunami** of the warning centres is one event with the official messages that carry its VTEC code.
- **Any other hazard** is an event on its own.
"""

from __future__ import annotations

import hashlib
import math
from collections import Counter
from collections.abc import Callable, Iterable
from typing import Optional

from app.attribution import merge_credits
from app.collectors.tsunami import vtec_event_key
from app.models.events import Event, EventCounts
from app.models.schemas import SEVERITY_RANK, HazardAlert, HazardCategory, SeverityLevel
from app.models.vigilance import Vigilance, VigilancePhenomenon
from app.services.geodesy import haversine_km

WARNING_LINK_KM = 300.0  # areas of one event: each within this distance of another (a choice of ours, ADR 0012)
QUAKE_LINK_KM = 100.0  # earthquakes of one sequence: each within this distance...
QUAKE_LINK_S = 6 * 3600  # ... and this time of another (a choice of ours, ADR 0012)

PHENOMENON_LABEL: dict[VigilancePhenomenon, str] = {
    VigilancePhenomenon.WIND: "Wind",
    VigilancePhenomenon.RAIN: "Rain",
    VigilancePhenomenon.THUNDERSTORM: "Thunderstorms",
    VigilancePhenomenon.SNOW_ICE: "Snow and ice",
    VigilancePhenomenon.HEAT: "Heat",
    VigilancePhenomenon.COLD: "Cold",
    VigilancePhenomenon.FLOOD: "Floods",
    VigilancePhenomenon.AVALANCHE: "Avalanches",
    VigilancePhenomenon.COASTAL: "Coast and waves",
    VigilancePhenomenon.FIRE: "Fires",
    VigilancePhenomenon.FOG: "Fog",
}

Box = tuple[float, float, float, float]  # west, south, east, north


# ---------------------------------------------------------------------------------------------- warnings


def _start(vigilance: Vigilance) -> Optional[int]:
    return next((t for t in (vigilance.onset_utc, vigilance.effective_utc, vigilance.sent_utc) if t is not None), None)


def _end(vigilance: Vigilance) -> Optional[int]:
    """The later of the end and the expiry; ``None`` when the source gives neither (it lasts)."""
    times = [t for t in (vigilance.ends_utc, vigilance.expires_utc) if t is not None]
    return max(times) if times else None


def _area_key(vigilance: Vigilance) -> str:
    codes = ",".join(sorted(f"{c.scheme}:{c.value}" for c in vigilance.area.geocodes))
    return codes or (vigilance.area.description or vigilance.id)


def _phenomenon_key(vigilance: Vigilance) -> str:
    """What "the same phenomenon" is: the API's phenomenon, the source's word for ``other``; a tsunami message is never a coastal warning."""
    if vigilance.source == "tsunami":
        return "tsunami"
    if vigilance.phenomenon is not None and vigilance.phenomenon != VigilancePhenomenon.OTHER:
        return vigilance.phenomenon.value
    word = vigilance.phenomenon_original or vigilance.event or ""
    return "word:" + " ".join(word.casefold().split())


def _state_of(vigilance: Vigilance) -> Optional[str]:
    """The state of a warning whose area is a set of UGC zones of the NWS (``CAZ006`` is in California), else ``None``.

    A UGC code is the two letters of the state, ``C`` (county) or ``Z`` (zone) and three digits: the state splits the warnings of the
    United States that have no shape here, which cannot be placed otherwise.
    """
    states = {code.value[:2] for code in vigilance.area.geocodes if code.scheme == "UGC" and len(code.value) == 6 and code.value[:2].isalpha()}
    return states.pop() if len(states) == 1 else None


def _by_period(vigilances: list[Vigilance]) -> list[list[Vigilance]]:
    """Chains of warnings whose periods overlap (a warning without an end overlaps everything after its start)."""
    ordered = sorted(vigilances, key=lambda v: (_start(v) if _start(v) is not None else -1, v.id))
    chains: list[list[Vigilance]] = []
    chain_end: Optional[float] = None
    for vigilance in ordered:
        start = _start(vigilance)
        if chains and (chain_end is None or start is None or start <= chain_end):
            chains[-1].append(vigilance)
        else:
            chains.append([vigilance])
            chain_end = -math.inf
        end = _end(vigilance)
        chain_end = None if (end is None or chain_end is None) else max(chain_end, end)
    return chains


def _linked(points: list[tuple[float, float]], link_km: float) -> list[list[int]]:
    """Single-linkage clusters of points (lat, lon) within ``link_km`` of each other; a grid keeps it near linear."""
    parent = list(range(len(points)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    cell_deg = link_km / 111.0
    grid: dict[tuple[int, int], list[int]] = {}
    for index, (lat, lon) in enumerate(points):
        scale = max(0.1, math.cos(math.radians(lat)))
        cell = (int(math.floor(lat / cell_deg)), int(math.floor(lon * scale / cell_deg)))
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                for other in grid.get((cell[0] + dy, cell[1] + dx), ()):
                    if root(other) != root(index) and haversine_km(lat, lon, points[other][0], points[other][1]) <= link_km:
                        parent[root(other)] = root(index)
        grid.setdefault(cell, []).append(index)
    clusters: dict[int, list[int]] = {}
    for index in range(len(points)):
        clusters.setdefault(root(index), []).append(index)
    return list(clusters.values())


def _union(boxes: Iterable[Box]) -> Optional[list[float]]:
    boxes = list(boxes)
    if not boxes:
        return None
    return [round(min(b[0] for b in boxes), 4), round(min(b[1] for b in boxes), 4), round(max(b[2] for b in boxes), 4), round(max(b[3] for b in boxes), 4)]


def _event_id(kind: str, key: str, anchor: str) -> str:
    return f"{kind}:{hashlib.sha1(f'{key}|{anchor}'.encode()).hexdigest()[:16]}"


def group_warnings(
    vigilances: list[Vigilance],
    box_of: Callable[[Vigilance], Optional[Box]],
    country_name: Callable[[str], str],
) -> list[Event]:
    """The official warnings as events (see the module docstring)."""
    groups: dict[tuple[Optional[str], str, Optional[SeverityLevel]], list[Vigilance]] = {}
    for vigilance in vigilances:
        groups.setdefault((vigilance.country, _phenomenon_key(vigilance), vigilance.level), []).append(vigilance)
    events: list[Event] = []
    for (country, phenomenon_key, level), members in groups.items():
        for chain in _by_period(members):
            boxes = {v.id: box_of(v) for v in chain}
            placed = [v for v in chain if boxes[v.id] is not None]
            unplaced = [v for v in chain if boxes[v.id] is None]
            centres = [((b[1] + b[3]) / 2, (b[0] + b[2]) / 2) for b in (boxes[v.id] for v in placed) if b is not None]
            clusters = [[placed[i] for i in cluster] for cluster in _linked(centres, WARNING_LINK_KM)] if placed else []
            by_state: dict[Optional[str], list[Vigilance]] = {}
            for vigilance in unplaced:  # no place here: kept apart, by state when the codes name one (UGC), never guessed
                by_state.setdefault(_state_of(vigilance), []).append(vigilance)
            clusters.extend(by_state.values())
            for cluster in clusters:
                events.append(_warnings_event(cluster, country, phenomenon_key, level, boxes, country_name))
    return events


def _warnings_event(
    cluster: list[Vigilance],
    country: Optional[str],
    phenomenon_key: str,
    level: Optional[SeverityLevel],
    boxes: dict[str, Optional[Box]],
    country_name: Callable[[str], str],
) -> Event:
    cluster = sorted(cluster, key=lambda v: (_start(v) if _start(v) is not None else -1, v.id))
    areas = list(dict.fromkeys(_area_key(v) for v in cluster))
    names = list(dict.fromkeys(v.area.description for v in cluster if v.area.description))
    words = list(dict.fromkeys(v.phenomenon_original for v in cluster if v.phenomenon_original))
    first = cluster[0]
    label = PHENOMENON_LABEL.get(first.phenomenon) if first.phenomenon else None  # type: ignore[arg-type]
    label = label or (words[0] if words else (first.event or "Warning"))
    where = country_name(country) if country else None
    if len(areas) == 1:
        title = " · ".join(part for part in (label, names[0] if names else where) if part)
    else:
        title = " · ".join(part for part in (label, where, f"{len(areas)} areas") if part)
    starts = [s for s in (_start(v) for v in cluster) if s is not None]
    ends = [_end(v) for v in cluster]
    return Event(
        id=_event_id("warnings", f"{country}|{phenomenon_key}|{level}", first.id),
        kind="warnings",
        phenomenon=first.phenomenon,
        level=level,
        title=title,
        country=country,
        areas_count=len(areas),
        area_names=names,
        phenomenon_words=words,
        start_utc=min(starts) if starts else None,
        end_utc=None if not ends or any(e is None for e in ends) else max(e for e in ends if e is not None),
        bbox=_union(b for b in (boxes[v.id] for v in cluster) if b is not None),
        sources=list(dict.fromkeys(v.source for v in cluster)),
        alert_ids=[v.id for v in cluster],
        alerts=cluster,
        attribution=merge_credits(*(v.attribution for v in cluster)),
        derived=["level", "title", "areas_count", "start_utc", "end_utc", "bbox"],
    )


# ---------------------------------------------------------------------------------------------- hazards


def _hazard_box(hazards: list[HazardAlert]) -> list[float]:
    return [round(min(h.lon for h in hazards), 4), round(min(h.lat for h in hazards), 4), round(max(h.lon for h in hazards), 4), round(max(h.lat for h in hazards), 4)]


def _hazard_event(kind: str, hazards: list[HazardAlert], country: Optional[str], alerts: Optional[list[Vigilance]] = None) -> Event:
    hazards = sorted(hazards, key=lambda h: (h.timestamp_utc, h.id))
    strongest = max(hazards, key=lambda h: (SEVERITY_RANK[h.severity], h.value_primary or 0.0, -h.timestamp_utc))
    alerts = alerts or []
    level = strongest.severity
    message_level = max((v.level for v in alerts if v.level is not None), key=lambda lv: SEVERITY_RANK[lv], default=None)
    if message_level is not None and SEVERITY_RANK[message_level] > SEVERITY_RANK[level]:
        level = message_level  # a tsunami: the official messages say more than the centre's hazard
    if kind == "earthquakes" and len(hazards) > 1:
        title = f"{len(hazards)} earthquakes · strongest: {strongest.title}"
    else:
        title = strongest.title
    return Event(
        id=_event_id(kind, kind, hazards[0].id),
        kind=kind,  # type: ignore[arg-type]
        category=strongest.category,
        level=level,
        title=title,
        country=country,
        start_utc=hazards[0].timestamp_utc,
        end_utc=hazards[-1].timestamp_utc if kind == "earthquakes" else None,
        bbox=_hazard_box(hazards),
        sources=list(dict.fromkeys([*(s for h in hazards for s in (h.sources or [h.source])), *(v.source for v in alerts)])),
        alert_ids=[v.id for v in alerts],
        alerts=alerts,
        hazards=hazards,
        attribution=merge_credits(*(h.attribution for h in hazards), *(v.attribution for v in alerts)),
        derived=["level", "title", "start_utc", "end_utc", "bbox"],
    )


def group_hazards(
    hazards: list[HazardAlert], country_of: Callable[[HazardAlert], Optional[str]], tsunami_messages: Optional[list[Vigilance]] = None
) -> tuple[list[Event], set[str]]:
    """The hazards as events; returns them and the ids of the tsunami messages joined to a hazard (they leave the warnings)."""
    quakes = [h for h in hazards if h.category == HazardCategory.EARTHQUAKE]
    others = [h for h in hazards if h.category != HazardCategory.EARTHQUAKE]
    events: list[Event] = []
    for sequence in _quake_sequences(quakes):
        countries = {country_of(h) for h in sequence}
        events.append(_hazard_event("earthquakes", sequence, countries.pop() if len(countries) == 1 else None))
    messages_by_key: dict[str, list[Vigilance]] = {}
    for message in tsunami_messages or []:
        for key in {k for text in message.texts for p in text.parameters if p.name == "VTEC" and (k := vtec_event_key(p.value))}:
            messages_by_key.setdefault(key, []).append(message)
    joined: set[str] = set()
    for hazard in others:
        if hazard.category == HazardCategory.TSUNAMI:
            messages = messages_by_key.get(hazard.event_key or "", []) if hazard.event_key else []
            joined.update(m.id for m in messages)
            unique = list({message.id: message for message in messages}.values())
            events.append(_hazard_event("tsunami", [hazard], country_of(hazard), unique))
        else:
            events.append(_hazard_event("hazard", [hazard], country_of(hazard)))
    return events, joined


def _quake_sequences(quakes: list[HazardAlert]) -> list[list[HazardAlert]]:
    """Earthquakes within ``QUAKE_LINK_KM`` and ``QUAKE_LINK_S`` of another of the sequence (single linkage, in time order)."""
    ordered = sorted(quakes, key=lambda h: (h.timestamp_utc, h.id))
    parent = list(range(len(ordered)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    start = 0
    for index, quake in enumerate(ordered):
        while ordered[start].timestamp_utc < quake.timestamp_utc - QUAKE_LINK_S:
            start += 1
        for other in range(start, index):
            if root(other) != root(index) and haversine_km(quake.lat, quake.lon, ordered[other].lat, ordered[other].lon) <= QUAKE_LINK_KM:
                parent[root(other)] = root(index)
    sequences: dict[int, list[HazardAlert]] = {}
    for index, quake in enumerate(ordered):
        sequences.setdefault(root(index), []).append(quake)
    return list(sequences.values())


# ---------------------------------------------------------------------------------------------- the answer


def sorted_events(events: list[Event]) -> list[Event]:
    """The highest level first, then the broadest, then the earliest."""
    return sorted(
        events, key=lambda e: (-(SEVERITY_RANK[e.level] if e.level else -1), -(e.areas_count or len(e.hazards) or 0), e.start_utc or 0, e.id)
    )


def count(events: list[Event]) -> EventCounts:
    return EventCounts(events=len(events), by_level=dict(Counter(e.level.value if e.level else "unknown" for e in events)))
