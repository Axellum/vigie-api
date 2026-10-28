"""Events instead of lines (``GET /v1/events``), the impact score and the families of phenomena (``GET /v1/families``).

The rules are in ``docs/decisions/0012-events-and-impact.md``; everything worked out here is ours and is named in ``derived``:

- **Warnings** of one country, one phenomenon (the API's own; the source's word when the phenomenon is ``other`` or unknown) and one
  level are one event when their periods overlap and their areas are close: each area within ``WARNING_LINK_KM`` of another of the
  event (the distance between the centres of the boxes around them, single linkage). An area whose shape is not known here cannot be
  placed: such areas make an event of their own, never a guessed place.
- **Earthquakes** are one sequence when each lies within ``QUAKE_LINK_KM`` and ``QUAKE_LINK_S`` of another of the sequence.
- **A tsunami** of the warning centres is one event with the official messages that carry its VTEC code.
- **Any other hazard** is an event on its own.

The impact score (0 to 100) ranks them: ``impact_of_warnings`` and ``impact_of_hazards`` hold the tables. ``WORLD_MIN_IMPACT`` (60)
keeps what counts in a world view: the official orange and red warnings, GDACS orange and red, an earthquake the API rates orange or
red (magnitude 6 or more, a PAGER orange or red, or a tsunami flag), any tropical cyclone, a volcano at watch or warning, any tsunami
message.
"""

from __future__ import annotations

import hashlib
import math
from collections import Counter
from collections.abc import Callable, Iterable
from typing import Optional

from app.attribution import merge_credits
from app.collectors.tsunami import vtec_event_key
from app.models.events import Event, EventCounts, Family
from app.models.schemas import SEVERITY_RANK, HazardAlert, HazardCategory, SeverityLevel
from app.models.vigilance import Vigilance, VigilancePhenomenon
from app.services.geodesy import haversine_km

WARNING_LINK_KM = 300.0  # areas of one event: each within this distance of another (a choice of ours, ADR 0012)
QUAKE_LINK_KM = 100.0  # earthquakes of one sequence: each within this distance...
QUAKE_LINK_S = 6 * 3600  # ... and this time of another (a choice of ours, ADR 0012)
WORLD_MIN_IMPACT = 60

FAMILIES: tuple[Family, ...] = (
    Family(id="wind", name="Wind", phenomena=["wind"]),
    Family(id="rain-flood", name="Rain and floods", phenomena=["rain", "flood"], hazard_categories=["FLOOD", "EXTREME_RAIN"]),
    Family(id="thunderstorm", name="Thunderstorms", phenomena=["thunderstorm"], hazard_categories=["TORNADO"]),
    Family(id="snow-ice", name="Snow and ice", phenomena=["snow-ice", "avalanche"]),
    Family(id="heat", name="Heat", phenomena=["heat"]),
    Family(id="cold", name="Cold", phenomena=["cold"]),
    Family(id="fire", name="Fires", phenomena=["fire"], hazard_categories=["WILDFIRE"]),
    Family(id="coastal", name="Coast and waves", phenomena=["coastal"]),
    Family(id="earthquake", name="Earthquakes", hazard_categories=["EARTHQUAKE"]),
    Family(id="volcano", name="Volcanoes", hazard_categories=["VOLCANO"]),
    Family(id="cyclone", name="Tropical cyclones", hazard_categories=["HURRICANE"]),
    Family(
        id="tsunami",
        name="Tsunamis",
        hazard_categories=["TSUNAMI"],
        note="Also the official messages of the U.S. Tsunami Warning Centers (`source: tsunami`), whatever their phenomenon.",
    ),
    Family(id="air", name="Air quality", note="No source of warnings for the air is read today: the family is empty."),
    Family(id="space", name="Space weather", hazard_categories=["SOLAR_STORM"]),
    Family(id="other", name="Other", phenomena=["fog", "other"], note="And a warning whose phenomenon is not known (`phenomenon: null`)."),
)
FAMILY_OF_PHENOMENON: dict[str, str] = {p: family.id for family in FAMILIES for p in family.phenomena}
FAMILY_OF_CATEGORY: dict[str, str] = {c: family.id for family in FAMILIES for c in family.hazard_categories}
FAMILY_IDS = tuple(family.id for family in FAMILIES)
FAMILIES_NOTE = (
    "The families are Vigie's own grouping of the API's phenomena (the tables of docs/data-sources.md) and hazard categories. "
    "A warning's family follows from its `phenomenon`; the words of the source stay in `phenomenon_original`."
)


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

# The impact tables (ADR 0012). An official warning by its level; the breadth adds up to 9 (one more per doubling of the areas).
WARNING_IMPACT: dict[Optional[SeverityLevel], int] = {
    SeverityLevel.RED: 90,
    SeverityLevel.ORANGE: 60,
    SeverityLevel.YELLOW: 20,
    SeverityLevel.GREEN: 0,
    None: 10,  # an official warning whose level no table covers: above green, below yellow (never a guessed level)
}
TSUNAMI_IMPACT: dict[SeverityLevel, int] = {SeverityLevel.RED: 100, SeverityLevel.ORANGE: 80, SeverityLevel.YELLOW: 60, SeverityLevel.GREEN: 30}
# A hazard by the API's severity: a table per category where the category says how much it weighs (a tropical cyclone always counts),
# the GDACS table for the other GDACS alerts (flood, fire, drought), the official table for anything else.
HAZARD_IMPACT: dict[str, dict[SeverityLevel, int]] = {
    "EARTHQUAKE": {SeverityLevel.RED: 85, SeverityLevel.ORANGE: 60, SeverityLevel.YELLOW: 30, SeverityLevel.GREEN: 5},
    "HURRICANE": {SeverityLevel.RED: 90, SeverityLevel.ORANGE: 75, SeverityLevel.YELLOW: 60, SeverityLevel.GREEN: 60},
    "VOLCANO": {SeverityLevel.RED: 85, SeverityLevel.ORANGE: 60, SeverityLevel.YELLOW: 25, SeverityLevel.GREEN: 0},
    "SOLAR_STORM": {SeverityLevel.RED: 70, SeverityLevel.ORANGE: 50, SeverityLevel.YELLOW: 20, SeverityLevel.GREEN: 0},
    "TSUNAMI": TSUNAMI_IMPACT,
}
GDACS_IMPACT: dict[SeverityLevel, int] = {SeverityLevel.RED: 90, SeverityLevel.ORANGE: 65, SeverityLevel.YELLOW: 30, SeverityLevel.GREEN: 10}
OTHER_IMPACT: dict[SeverityLevel, int] = {SeverityLevel.RED: 90, SeverityLevel.ORANGE: 60, SeverityLevel.YELLOW: 20, SeverityLevel.GREEN: 0}
HAZARD_WORDS: dict[str, str] = {"HURRICANE": "tropical cyclone", "SOLAR_STORM": "solar storm", "EXTREME_RAIN": "extreme rain"}

Box = tuple[float, float, float, float]  # west, south, east, north


# ---------------------------------------------------------------------------------------------- families


def family_of_warning(vigilance: Vigilance) -> str:
    """The family of a warning: ``tsunami`` for the messages of the tsunami centres, else the family of its phenomenon."""
    if vigilance.source == "tsunami":
        return "tsunami"
    if vigilance.phenomenon is None:
        return "other"
    return FAMILY_OF_PHENOMENON.get(vigilance.phenomenon.value, "other")


def family_of_hazard(hazard: HazardAlert) -> str:
    return FAMILY_OF_CATEGORY.get(hazard.category.value, "other")


# ---------------------------------------------------------------------------------------------- impact


def impact_of_warnings(level: Optional[SeverityLevel], areas: int, tsunami: bool = False) -> tuple[int, list[str]]:
    """The impact of a group of official warnings: its level, and its breadth."""
    if tsunami:
        base = TSUNAMI_IMPACT[level] if level is not None else 60
        reasons = [f"official tsunami message ({level.value.lower() if level else 'level unknown'})"]
    else:
        base = WARNING_IMPACT[level]
        reasons = [f"official {level.value.lower()} warning" if level else "official warning of unknown level"]
    breadth = min(9, int(math.log2(areas))) if areas > 1 and base > 0 else 0
    if breadth:
        reasons.append(f"{areas} areas")
    return min(100, base + breadth), reasons


def _hazard_table(hazard: HazardAlert) -> tuple[dict[SeverityLevel, int], str]:
    """The table a hazard is scored with, and its name in the reasons."""
    word = HAZARD_WORDS.get(hazard.category.value, hazard.category.value.lower())
    if hazard.category.value in HAZARD_IMPACT:
        return HAZARD_IMPACT[hazard.category.value], word
    if hazard.id.startswith("gdacs_") or "GDACS" in hazard.sources:
        return GDACS_IMPACT, f"GDACS {word} alert"
    return OTHER_IMPACT, word


def impact_of_hazards(hazards: list[HazardAlert]) -> tuple[int, list[str]]:
    """The impact of one hazard or of a sequence of earthquakes: the strongest, and the number of events of a sequence."""
    best = max(hazards, key=lambda h: (SEVERITY_RANK[h.severity], h.value_primary or 0.0))
    table, what = _hazard_table(best)
    score = table[best.severity]
    reasons = [f"{what} rated {best.severity.value.lower()} by the API"]
    if best.category == HazardCategory.EARTHQUAKE and best.value_primary is not None:
        reasons[0] = f"earthquake of magnitude {best.value_primary:g} ({best.severity.value.lower()})"
    if any(h.tsunami_warning for h in hazards) and best.category == HazardCategory.EARTHQUAKE:
        reasons.append("tsunami flag of the source")
    if len(hazards) > 1 and score > 0:
        score += min(5, len(hazards) - 1)
        reasons.append(f"{len(hazards)} earthquakes in the sequence")
    return min(100, score), reasons


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
    family = family_of_warning(first)
    impact, reasons = impact_of_warnings(level, len(areas), tsunami=family == "tsunami")
    return Event(
        id=_event_id("warnings", f"{country}|{phenomenon_key}|{level}", first.id),
        kind="warnings",
        phenomenon=first.phenomenon,
        family=family,  # type: ignore[arg-type]
        impact=impact,
        impact_reasons=reasons,
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
        derived=["family", "impact", "impact_reasons", "level", "title", "areas_count", "start_utc", "end_utc", "bbox"],
    )


# ---------------------------------------------------------------------------------------------- hazards


def _hazard_box(hazards: list[HazardAlert]) -> list[float]:
    return [round(min(h.lon for h in hazards), 4), round(min(h.lat for h in hazards), 4), round(max(h.lon for h in hazards), 4), round(max(h.lat for h in hazards), 4)]


def _hazard_event(kind: str, hazards: list[HazardAlert], country: Optional[str], alerts: Optional[list[Vigilance]] = None) -> Event:
    hazards = sorted(hazards, key=lambda h: (h.timestamp_utc, h.id))
    strongest = max(hazards, key=lambda h: (SEVERITY_RANK[h.severity], h.value_primary or 0.0, -h.timestamp_utc))
    alerts = alerts or []
    level = strongest.severity
    impact, reasons = impact_of_hazards(hazards)
    message_level = max((v.level for v in alerts if v.level is not None), key=lambda lv: SEVERITY_RANK[lv], default=None)
    if message_level is not None and SEVERITY_RANK[message_level] > SEVERITY_RANK[level]:
        level = message_level  # a tsunami: the official messages say more than the centre's hazard
    if alerts:
        message_impact, message_reasons = impact_of_warnings(message_level, 1, tsunami=True)
        if message_impact > impact:
            impact, reasons = message_impact, message_reasons + reasons
    if kind == "earthquakes" and len(hazards) > 1:
        title = f"{len(hazards)} earthquakes · strongest: {strongest.title}"
    else:
        title = strongest.title
    return Event(
        id=_event_id(kind, kind, hazards[0].id),
        kind=kind,  # type: ignore[arg-type]
        category=strongest.category,
        family="tsunami" if kind == "tsunami" else family_of_hazard(strongest),  # type: ignore[arg-type]
        impact=impact,
        impact_reasons=reasons,
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
        derived=["family", "impact", "impact_reasons", "level", "title", "start_utc", "end_utc", "bbox"],
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
    """The strongest impact first, then the highest level, then the broadest, then the earliest."""
    return sorted(
        events,
        key=lambda e: (-e.impact, -(SEVERITY_RANK[e.level] if e.level else -1), -(e.areas_count or len(e.hazards) or 0), e.start_utc or 0, e.id),
    )


def count(events: list[Event]) -> EventCounts:
    return EventCounts(
        events=len(events),
        by_family=dict(Counter(e.family for e in events)),
        by_level=dict(Counter(e.level.value if e.level else "unknown" for e in events)),
    )
