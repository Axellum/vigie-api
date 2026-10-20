"""Near places: what an event is close to, computed by Vigie (``near_places`` of a hazard).

An earthquake is "6 km from the Azuero Fault Zone (active fault)", or "12 km from Etna (volcano)", or "35 km from <a point the user
watches>". Three sets of named places are compared with each hazard that has a position:

- **Active faults**: the traces of the GEM Global Active Faults Database that have a name, bundled in
  ``app/data/gem-active-faults.geojson.gz`` (built by ``tools/build_faults.py``; CC BY-SA 4.0, see ``SOURCES.md``). Only for the
  hazards a fault can explain: earthquakes and volcanic events. An earthquake deeper than ``fault_max_depth_km`` is left out:
  the distance to a surface trace says nothing about it.
- **Volcanoes**: only those the hazard sources name themselves (a GDACS eruption, a USGS elevated alert), never a bundled list
  (the Smithsonian list has no licence we could read yet), and only for earthquakes. A volcano that is not in alert is not here.
- **Watched points**: the points the user added (``/v1/watched-points``), for every kind of hazard, within the radius of each point.

**The radii are settings, not sourced facts** (``PlaceSettings``, the "near" variables of the README): nobody publishes
the distance at which an epicentre is "near" a fault, and the catalogues' positions are uncertain by several kilometres. They say
how far this instance looks; ``docs/decisions/0008-sensitive-points.md`` gives the reasoning. The result never changes the
severity of a hazard, and it is named in the ``derived`` list of the hazard.

``near_places`` is ``None`` when it could not be worked out (the bundled file is missing or damaged, so the faults of an
earthquake are unknown) and ``[]`` when it was and nothing is near: an empty list is an answer, not a gap.
"""

from __future__ import annotations

import gzip
import json
import logging
import math
import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from shapely import STRtree
from shapely.affinity import translate
from shapely.geometry import LineString, box

from app.attribution import SIMULATED_ID_PREFIX
from app.models.places import NearPlace, WatchedPoint
from app.models.schemas import HazardAlert, HazardCategory
from app.services.geodesy import EARTH_RADIUS_KM, haversine_km
from app.services.geoindex import DATA_DIR, line_distance_km

log = logging.getLogger(__name__)

FAULTS_FILE = "gem-active-faults.geojson.gz"
KM_PER_DEGREE = math.pi * EARTH_RADIUS_KM / 180.0

# The hazards a fault can explain, and the hazards that are compared with the volcanoes in alert.
FAULT_CATEGORIES = frozenset({HazardCategory.EARTHQUAKE, HazardCategory.VOLCANO})
VOLCANO_NEIGHBOUR_CATEGORIES = frozenset({HazardCategory.EARTHQUAKE})


def _number_from_env(name: str, default: float, low: float, high: float) -> float:
    """A number from the environment; the default when it is missing, not a number or outside ``low`` .. ``high``."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if math.isfinite(value) and low <= value <= high else default


@dataclass(frozen=True)
class PlaceSettings:
    """How far this instance looks. Chosen, not measured nor published by any source (``docs/decisions/0008-sensitive-points.md``)."""

    fault_km: float = 10.0  # an epicentre within this distance of a mapped trace is "near" it
    fault_max_depth_km: float = 70.0  # deeper earthquakes (intermediate and deep focus) are not compared with surface traces
    volcano_km: float = 20.0  # an earthquake within this distance of a volcano in alert is "near" it
    max_places: int = 5  # at most this many places under a hazard, the nearest first
    watched_points_max: int = 50  # at most this many watched points on an instance
    quiet_max_gap_s: int = 900  # a source not read for longer than this breaks the run that "nothing happened" rests on

    @classmethod
    def from_env(cls) -> "PlaceSettings":
        """The settings that the README lists (distances, depth, count, limits); an unreadable value means the default."""
        default = cls()
        return cls(
            fault_km=_number_from_env("VIGIE_NEAR_FAULT_KM", default.fault_km, 0.1, 100.0),
            fault_max_depth_km=_number_from_env("VIGIE_NEAR_FAULT_MAX_DEPTH_KM", default.fault_max_depth_km, 1.0, 1000.0),
            volcano_km=_number_from_env("VIGIE_NEAR_VOLCANO_KM", default.volcano_km, 0.1, 500.0),
            max_places=int(_number_from_env("VIGIE_NEAR_MAX_PLACES", default.max_places, 1, 20)),
            watched_points_max=int(_number_from_env("VIGIE_WATCHED_POINTS_MAX", default.watched_points_max, 1, 500)),
            quiet_max_gap_s=int(_number_from_env("VIGIE_QUIET_MAX_GAP_S", default.quiet_max_gap_s, 60, 86400)),
        )


class FaultIndex:
    """The named active fault traces, searched with a spatial index (``shapely.STRtree``)."""

    def __init__(self, labels: list[str], lines: list[LineString], info: Optional[dict[str, Any]] = None) -> None:
        if len(labels) != len(lines):
            raise ValueError("one label for each line")
        self._labels = labels
        self._lines = lines
        self._tree = STRtree(lines) if lines else None
        self.info = info or {}

    def __len__(self) -> int:
        return len(self._lines)

    @classmethod
    def from_features(cls, features: Iterable[dict[str, Any]], info: Optional[dict[str, Any]] = None) -> "FaultIndex":
        labels: list[str] = []
        lines: list[LineString] = []
        for item in features:
            label = (item.get("properties") or {}).get("n")
            coordinates = (item.get("geometry") or {}).get("coordinates")
            if isinstance(label, str) and label and isinstance(coordinates, list) and len(coordinates) >= 2:
                labels.append(label)
                lines.append(LineString(coordinates))
        return cls(labels, lines, info)

    @classmethod
    def from_file(cls, path: Path = DATA_DIR / FAULTS_FILE) -> "FaultIndex":
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            collection = json.load(handle)
        info = {key: value for key, value in collection.items() if key != "features"}
        return cls.from_features(collection["features"], info)

    def near(self, lat: float, lon: float, radius_km: float, limit: int) -> list[tuple[str, float]]:
        """``(name, distance_km)`` of the faults whose trace passes within ``radius_km`` of a position, nearest first.

        A fault is counted once, at its nearest trace (a fault has many traces). The index finds the traces whose box
        meets the box of the search (longitudes widened by the latitude, and wrapped across the date line); the distance to each
        is then worked out on the plane of the position (``line_distance_km``).
        """
        if self._tree is None:
            return []
        half_lat = radius_km / KM_PER_DEGREE
        half_lon = min(180.0, half_lat / max(math.cos(math.radians(lat)), 0.01))
        best: dict[str, float] = {}
        for shift in (0.0, 360.0, -360.0):  # the date line: a trace at 179.9 east is near a position at 179.9 west
            west, east = lon - half_lon - shift, lon + half_lon - shift
            if east < -180.0 or west > 180.0:
                continue
            window = box(max(west, -180.0), max(lat - half_lat, -90.0), min(east, 180.0), min(lat + half_lat, 90.0))
            for index in self._tree.query(window):
                line = self._lines[index] if shift == 0.0 else translate(self._lines[index], xoff=shift)
                distance = line_distance_km(lat, lon, line)
                if distance <= radius_km:
                    label = self._labels[index]
                    if distance < best.get(label, math.inf):
                        best[label] = distance
        return sorted(best.items(), key=lambda item: (item[1], item[0]))[:limit]


@lru_cache(maxsize=1)
def default_faults() -> Optional[FaultIndex]:
    """The bundled faults, loaded once; ``None`` (and a warning) when the file cannot be read: the faults are then unknown."""
    try:
        return FaultIndex.from_file()
    except (OSError, ValueError, KeyError, TypeError, EOFError) as exc:
        log.warning("The bundled active faults (%s) cannot be read: %s: %s", FAULTS_FILE, type(exc).__name__, exc)
        return None


class NearPlacesService:
    """Works out the ``near_places`` of hazards from the faults, the volcanoes in alert and the watched points."""

    def __init__(
        self,
        settings: Optional[PlaceSettings] = None,
        faults: Optional[Callable[[], Optional[FaultIndex]]] = None,
    ) -> None:
        self.settings = settings or PlaceSettings()
        self._faults = faults or default_faults

    def faults(self) -> Optional[FaultIndex]:
        """The fault index, ``None`` when it could not be loaded."""
        return self._faults()

    def near_places(
        self,
        hazard: HazardAlert,
        volcanoes: Iterable[HazardAlert],
        points: Iterable[WatchedPoint],
    ) -> Optional[list[NearPlace]]:
        """The places near one hazard, nearest first; ``None`` when the faults it needs are unknown."""
        if hazard.id.startswith(SIMULATED_ID_PREFIX):
            return None  # a simulated alert is nowhere
        found: list[NearPlace] = []
        if hazard.category in FAULT_CATEGORIES and (hazard.depth_km is None or hazard.depth_km <= self.settings.fault_max_depth_km):
            index = self.faults()
            if index is None:
                return None
            found.extend(
                NearPlace(name=name, kind="fault", distance_km=round(distance, 1), sources=["GEM"])
                for name, distance in index.near(hazard.lat, hazard.lon, self.settings.fault_km, self.settings.max_places)
            )
        if hazard.category in VOLCANO_NEIGHBOUR_CATEGORIES:
            for volcano in volcanoes:
                if volcano.id == hazard.id or volcano.place_name is None:
                    continue
                distance = haversine_km(hazard.lat, hazard.lon, volcano.lat, volcano.lon)
                if distance <= self.settings.volcano_km:
                    found.append(
                        NearPlace(name=volcano.place_name, kind="volcano", distance_km=round(distance, 1), sources=list(volcano.sources or [volcano.source]))
                    )
        for point in points:
            distance = haversine_km(hazard.lat, hazard.lon, point.lat, point.lon)
            if distance <= point.radius_km:
                found.append(NearPlace(name=point.name, kind=point.kind, distance_km=round(distance, 1)))
        # The nearest first; a name is said once (a volcano the user also watches, a fault with several traces).
        found.sort(key=lambda place: (place.distance_km, place.kind, place.name))
        kept: list[NearPlace] = []
        seen: set[tuple[str, str]] = set()
        for place in found:
            key = (place.kind, place.name.casefold())
            if key not in seen:
                seen.add(key)
                kept.append(place)
        return kept[: self.settings.max_places]

    def annotate(self, hazards: list[HazardAlert], points: Iterable[WatchedPoint]) -> list[HazardAlert]:
        """The hazards with their ``near_places`` (new objects: the collectors' cached alerts are not touched)."""
        volcanoes = [h for h in hazards if h.category == HazardCategory.VOLCANO and h.place_name is not None]
        watched = list(points)
        return [h.model_copy(update={"near_places": self.near_places(h, volcanoes, watched)}) for h in hazards]
