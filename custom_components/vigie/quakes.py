"""The answer of ``GET /v1/earthquakes`` as the integration reads it, and the rule that announces each earthquake once.

A layer of its own: an earthquake is what the seismic sources (USGS, EMSC) reported, never an official warning, and nothing here
reads or changes the warnings of ``models.py``. Nothing here needs Home Assistant, so the rules can be read (and tested) on their own.

The principle is the same as for the warnings: **a value nobody supplied is unknown**. The strongest magnitude is ``None`` when no
earthquake matches (the count then says 0 if a source answered, and is unknown if none did).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any

from .const import MAX_LISTED_QUAKES, QUAKE_ATTRIBUTES_BUDGET_BYTES
from .models import _int, _iso, _number, _shorten, _text

# Two reports of one earthquake, as the API merges them (``app/services/geodesy.py``): within two minutes and 100 km.
SAME_QUAKE_S = 120
SAME_QUAKE_KM = 100.0


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance (haversine, mean Earth radius)."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi, dlmb = phi2 - phi1, math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    return 2 * 6371.0088 * math.asin(min(1.0, math.sqrt(a)))


@dataclass(frozen=True, slots=True)
class Quake:
    """One earthquake, as far as the integration needs it."""

    id: str
    title: str | None
    magnitude: float
    magnitude_type: str | None
    time_utc: int
    lat: float
    lon: float
    depth_km: float | None
    distance_km: float | None
    bearing_cardinal: str | None
    tsunami_warning: bool
    sources: tuple[str, ...]
    attribution: str | None

    def same_event(self, other: Quake) -> bool:
        """The same earthquake: the same id, or (two sources, or a source that changed its id) close in time and place."""
        if self.id == other.id:
            return True
        return abs(self.time_utc - other.time_utc) <= SAME_QUAKE_S and distance_km(self.lat, self.lon, other.lat, other.lon) <= SAME_QUAKE_KM

    def summary(self) -> dict[str, Any]:
        """A small, JSON-ready description (an item of the sensor's list, the data of an event). Absent values are left out."""
        item: dict[str, Any] = {
            "id": self.id,
            "title": _shorten(self.title, 120),
            "magnitude": self.magnitude,
            "magnitude_type": self.magnitude_type,
            "time": _iso(self.time_utc),
            "distance_km": self.distance_km,
            "bearing": self.bearing_cardinal,
            "depth_km": self.depth_km,
            "latitude": self.lat,
            "longitude": self.lon,
            "sources": list(self.sources),
        }
        if self.tsunami_warning:
            item["tsunami_flag"] = True
        return {key: value for key, value in item.items() if value is not None}


def parse_quake(raw: Any) -> Quake | None:
    """One earthquake of the answer; ``None`` when its id, magnitude, time or position is missing (it is skipped, and counted)."""
    if not isinstance(raw, dict):
        return None
    identifier, magnitude, time_utc = _text(raw.get("id")), _number(raw.get("magnitude")), _int(raw.get("time_utc"))
    lat, lon = _number(raw.get("lat")), _number(raw.get("lon"))
    if identifier is None or magnitude is None or time_utc is None or lat is None or lon is None:
        return None
    sources = tuple(s for item in raw.get("sources") or [] if (s := _text(item)) is not None)
    return Quake(
        id=identifier,
        title=_text(raw.get("title")),
        magnitude=magnitude,
        magnitude_type=_text(raw.get("magnitude_type")),
        time_utc=time_utc,
        lat=lat,
        lon=lon,
        depth_km=_number(raw.get("depth_km")),
        distance_km=_number(raw.get("distance_km")),
        bearing_cardinal=_text(raw.get("bearing_cardinal")),
        tsunami_warning=raw.get("tsunami_warning") is True,
        sources=sources,
        attribution=_text(raw.get("attribution")),
    )


@dataclass(frozen=True, slots=True)
class QuakeSource:
    """Whether a seismic source answered at the instance's last reading."""

    provider_id: str
    name: str | None
    answered: bool


@dataclass(frozen=True, slots=True)
class QuakeData:
    """What one read of ``/v1/earthquakes`` gave, and the earthquakes it brought that were not announced before."""

    generated_utc: int
    quakes: tuple[Quake, ...]  # the strongest first, as the API sorts them
    count: int  # matches before the API's limit
    sources: tuple[QuakeSource, ...]
    attribution: str | None
    skipped: int = 0
    new: tuple[Quake, ...] = ()
    sequence: int = 0

    @property
    def any_source_answered(self) -> bool:
        return any(source.answered for source in self.sources)

    @property
    def strongest(self) -> Quake | None:
        return max(self.quakes, key=lambda q: (q.magnitude, q.time_utc), default=None)


def parse_earthquakes(payload: Any, min_magnitude: float) -> QuakeData:
    """The answer of ``GET /v1/earthquakes``. Raises ``ValueError`` when it is not one (the caller treats it as a failed read).

    An earthquake under ``min_magnitude`` is left out even if the instance sent it (an instance that ignores the parameter).
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("earthquakes"), list):
        raise ValueError("not an answer of /v1/earthquakes")
    generated_utc = _int(payload.get("generated_utc"))
    if generated_utc is None:
        raise ValueError("the answer has no generated_utc")
    parsed = [parse_quake(raw) for raw in payload["earthquakes"]]
    quakes = tuple(q for q in parsed if q is not None and q.magnitude >= min_magnitude)
    sources = tuple(
        QuakeSource(provider_id=pid, name=_text(raw.get("name")), answered=raw.get("answered") is True)
        for raw in payload.get("sources") or []
        if isinstance(raw, dict) and (pid := _text(raw.get("provider_id"))) is not None
    )
    count = _int(payload.get("count"))
    return QuakeData(
        generated_utc=generated_utc,
        quakes=quakes,
        count=len(quakes) if count is None else max(count, len(quakes)),
        sources=sources,
        attribution=_text(payload.get("attribution")),
        skipped=parsed.count(None),
    )


class QuakeTracker:
    """Says which earthquakes of an answer were not announced before: each earthquake is announced once.

    - the first answer only sets the baseline: what had already happened when Home Assistant started is not announced;
    - an earthquake is "the same" by its id, or close in time and place (two sources, or a source that changed its id);
    - an earthquake is remembered as long as the window of the reads can still bring it back, so it is never announced twice.
    """

    def __init__(self, window_s: int) -> None:
        self._known: list[Quake] | None = None
        self._window_s = window_s

    def update(self, data: QuakeData) -> tuple[Quake, ...]:
        """Take a new answer and return the earthquakes to announce."""
        horizon = data.generated_utc - self._window_s - 3600  # an hour of margin past the window
        if self._known is None:
            self._known = list(data.quakes)
            return ()
        known = [q for q in self._known if q.time_utc >= horizon]
        new: list[Quake] = []
        for quake in sorted(data.quakes, key=lambda q: q.time_utc):
            if not any(quake.same_event(seen) for seen in known):
                new.append(quake)
            known.append(quake)
        self._known = known
        return tuple(new)


def _json_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8"))


def build_quake_attributes(
    data: QuakeData,
    *,
    radius_km: float,
    min_magnitude: float,
    hours: float,
    max_listed: int = MAX_LISTED_QUAKES,
    budget_bytes: int = QUAKE_ATTRIBUTES_BUDGET_BYTES,
) -> dict[str, Any]:
    """The attributes of the strongest-earthquake sensor, bounded: the list is cut, and says how many it left out."""
    strongest = data.strongest
    attributes: dict[str, Any] = {
        "count": data.count if data.count or data.any_source_answered else None,
        "radius_km": radius_km,
        "min_magnitude": min_magnitude,
        "hours": hours,
        "sources": [{"source": s.provider_id, "answered": s.answered} for s in data.sources],
        "server_time": _iso(data.generated_utc),
    }
    if strongest is not None:  # the strongest one (the state is its magnitude); `latitude` and `longitude` put it on the map
        summary = strongest.summary()
        summary.pop("magnitude")
        summary["reported_by"] = summary.pop("sources")
        attributes |= summary
    if data.skipped:
        attributes["unreadable"] = data.skipped
    items = [quake.summary() for quake in data.quakes]
    listed = items[:max_listed]
    while listed and _json_size({**attributes, "earthquakes": listed}) > budget_bytes:
        listed.pop()
    attributes["earthquakes"] = listed
    attributes["omitted"] = data.count - len(listed)
    return attributes
