"""The catalogue of the places of the French coast: ports, slipways, anchorages, dive sites and bathing sites.

The places are ``app/data/sea-places.json.gz`` (``tools/build_places.py``: the Sandre ports, OpenStreetMap, the sanitary quality of
the bathing sites; nothing is read from them at run time). A place has **no forecast** here: the catalogue says where things are, and
what the sources say of each. ``near`` and ``distance_km`` are worked out by Vigie (``derived``).
"""

from __future__ import annotations

import gzip
import json
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from app.attribution import credit_text
from app.models.sea_places import Place, PlaceAnswer, PlaceList, PlaceType
from app.services.geodesy import haversine_km

PLACES_FILE = Path(__file__).resolve().parents[1] / "data" / "sea-places.json.gz"
MAX_RADIUS_KM = 200.0
LICENCE = "ODbL-1.0 (OpenStreetMap), Licence Ouverte (Sandre), Licence Ouverte 2.0 (bathing quality)"
NOTE = (
    "A catalogue of places, not a forecast and not a statement that a place can be used: a slipway may be closed to the public in "
    "fact, an anchorage is not an authorisation to anchor, the class of a bathing site is the class of the water over the four years "
    "2021 to 2024 and not the water of today. `near` and `distance_km` are worked out by Vigie."
)
DERIVED = ["id", "near", "distance_km"]
SOURCE_CREDITS = {"sandre": "sandre_ports", "qsb": "qsb_bathing"}


class UnknownPlace(KeyError):
    """No place has this id."""


@dataclass(frozen=True)
class Entry:
    place: Place
    text: str  # what ``q`` is looked for in: the name, the locality and the commune, without accents or capitals


def fold(text: str) -> str:
    """Lower case, no accents, hyphens and apostrophes as spaces: ``L'Île-d'Yeu`` -> ``l ile d yeu``."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    plain = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"[\s'’\-]+", " ", plain).strip()


@lru_cache(maxsize=1)
def place_file() -> dict[str, Any]:
    with gzip.open(PLACES_FILE, "rt", encoding="utf-8") as handle:
        return json.load(handle)


@lru_cache(maxsize=1)
def entries() -> tuple[Entry, ...]:
    found = []
    for raw in place_file()["places"]:
        place = Place(**raw)
        found.append(Entry(place, fold(" ".join(p for p in (place.name, place.near, place.commune) if p))))
    return tuple(found)


def parse_bbox(text: str) -> tuple[float, float, float, float]:
    """``west,south,east,north`` in degrees. Raises ``ValueError`` with the reason when it is not a box."""
    try:
        west, south, east, north = (float(part) for part in text.split(","))
    except ValueError:
        raise ValueError("bbox must be four numbers: west,south,east,north") from None
    if not (-180.0 <= west < east <= 180.0 and -90.0 <= south < north <= 90.0):
        raise ValueError("bbox must satisfy -180 <= west < east <= 180 and -90 <= south < north <= 90")
    return west, south, east, north


def _credit(places: list[Place]) -> str:
    keys = ["osm_places"] + [SOURCE_CREDITS[s] for s in SOURCE_CREDITS if any(p.source == s for p in places)]
    return credit_text(*keys)


def search(
    types: Optional[list[PlaceType]],
    near: Optional[tuple[float, float]],
    radius_km: float,
    bbox: Optional[tuple[float, float, float, float]],
    q: Optional[str],
    limit: int,
    offset: int,
) -> PlaceList:
    """The places that match every filter given. With ``near`` they come nearest first (``distance_km`` set), else by kind and name."""
    wanted = fold(q.strip()) if q and q.strip() else None
    matched: list[Place] = []
    for entry in entries():
        place = entry.place
        if types and place.type not in types:
            continue
        if wanted and wanted not in entry.text:
            continue
        if bbox and not (bbox[0] <= place.lon <= bbox[2] and bbox[1] <= place.lat <= bbox[3]):
            continue
        if near:
            distance = haversine_km(near[0], near[1], place.lat, place.lon)
            if distance > radius_km:
                continue
            place = place.model_copy(update={"distance_km": round(distance, 1)})
        matched.append(place)
    if near:
        matched.sort(key=lambda p: (p.distance_km or 0.0, p.id))
    page = matched[offset : offset + limit]
    by_type = {t: sum(1 for p in matched if p.type == t) for t in ("port", "slipway", "anchorage", "dive", "bathing")}
    return PlaceList(
        count=len(matched),
        returned=len(page),
        limit=limit,
        offset=offset,
        by_type=by_type,
        places=page,
        built_utc=place_file()["osm_data_utc"],
        note=NOTE,
        derived=DERIVED,
        licence=LICENCE,
        attribution=_credit(page),
    )


def find(place_id: str) -> PlaceAnswer:
    for entry in entries():
        if entry.place.id == place_id:
            return PlaceAnswer(
                place=entry.place,
                built_utc=place_file()["osm_data_utc"],
                note=NOTE,
                derived=DERIVED,
                licence=LICENCE,
                attribution=_credit([entry.place]),
            )
    raise UnknownPlace(place_id)
