"""Build the list of surf spots of the French Atlantic and Channel coasts (``app/data/surf-spots.json``) from OpenStreetMap.

The surf forecast (``GET /v1/surf/spots/{spot_id}``) is given for a spot, and needs to know which way its beach faces. Nothing is read from
OpenStreetMap at run time: this script asks the Overpass API once, when it is run, and writes the file the API reads::

    python tools/build_spots.py                    # ask Overpass, write app/data/surf-spots.json
    python tools/build_spots.py --cache-dir DIR    # keep the answers of Overpass in DIR and read them again on the next run

What makes a spot (each one carries the elements that make it one, by their OpenStreetMap id):

- an element tagged ``sport=surfing`` that is a place to surf, not a business (no shop, address, telephone, website, opening
  hours or building), named by itself or by the beach it is on;
- or a **named beach** (``natural=beach``) within ``EVIDENCE_KM`` of a surf school or a surf club tagged ``sport=surfing``:
  a school teaches on a beach where people surf. A shop is not evidence. The nearest named beach is taken.

Each spot must have the OpenStreetMap coastline (``natural=coastline``) within ``COAST_KM``: a beach on a lake, a river or a
lagoon behind the dunes is dropped (the "lac marin" of Vieux-Boucau is 690 m from the coastline; the farthest sea spot, the surf
zone of the Truc Vert, 600 m). Its **facing** is the direction from the beach to the open sea, perpendicular to the coastline: OpenStreetMap
draws the coastline with the land on its left and the water on its right, so the facing is the direction of the coastline
near the beach, turned 90 degrees to the right. It is computed by Vigie (``derived``), over ``FACING_SPAN_M`` of coastline on
each side of the nearest point. Two spots closer than ``MERGE_KM`` are one.

What is **not** in the file, because no open source gives it: the bottom (sand, reef, rocks) under the waves, the tide and the
swell a spot works best with. ``beach_surface`` is the ``surface`` tag of the beach (what the beach is made of), not the
bottom. These fields stay ``null`` until someone who knows the spot gives them with a source.

Licence: the file is a database made from OpenStreetMap, **ODbL 1.0** (https://opendatacommons.org/licenses/odbl/1-0/),
"© OpenStreetMap contributors"; the same text is in ``app/data/surf-spots.NOTICE.md``. The code stays MIT.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT = REPO_ROOT / "app" / "data" / "surf-spots.json"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
USER_AGENT = "vigie-api build_spots (https://github.com/Axellum/vigie-api)"

# South, west, north, east: the French coasts of the Atlantic, the Channel and the North Sea (the Mediterranean is east of it).
BBOX = (43.3, -5.3, 51.2, 2.7)
FRANCE = 'area["ISO3166-1"="FR"][admin_level=2]->.fr;'

EVIDENCE_KM = 1.0
COAST_KM = 0.65
FACING_SPAN_M = 400.0
MERGE_KM = 0.6
PLACE_KM = 8.0
SUBURB_KM = 2.0

# The tags of a business: an element that has one is a shop, a school or a club, not a place to surf.
BUSINESS_TAGS = (
    "shop",
    "amenity",
    "building",
    "phone",
    "contact:phone",
    "website",
    "contact:website",
    "email",
    "contact:email",
    "opening_hours",
    "operator",
    "office",
    "craft",
    "tourism",
    "rental",
)
SCHOOL_WORDS = re.compile(r"(?i)(\bécole|\becole|school|\bclub\b|surf ?camp|eskola|association|académie|academy)")

Point = tuple[float, float]


def overpass(query: str, cache_dir: Optional[Path]) -> dict[str, Any]:
    """The answer of Overpass to ``query``, kept in ``cache_dir`` (by the hash of the query) when one is given."""
    key = hashlib.sha256(query.encode("utf-8")).hexdigest()[:16]
    cached = cache_dir / f"overpass-{key}.json" if cache_dir else None
    if cached and cached.exists():
        return json.loads(cached.read_text(encoding="utf-8"))
    with httpx.Client(timeout=300.0, headers={"User-Agent": USER_AGENT}) as client:
        # Overpass answers 429 or 50x when it is busy: wait and ask again, a few times, then give up.
        for wait_s in (15, 60, 120, 0):
            response = client.post(OVERPASS_URL, data={"data": query})
            if response.status_code not in (429, 502, 503, 504) or not wait_s:
                break
            print(f"overpass: {response.status_code}, again in {wait_s} s", file=sys.stderr)
            time.sleep(wait_s)
        response.raise_for_status()
        answer = response.json()
    if cached:
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps(answer), encoding="utf-8")
    time.sleep(1.0)  # one request at a time, with a pause: Overpass is a shared service
    return answer


def position(element: dict[str, Any]) -> Optional[Point]:
    """``(lat, lon)`` of a node, or the centre Overpass gives for a way or a relation."""
    centre = element.get("center") or element
    lat, lon = centre.get("lat"), centre.get("lon")
    return (float(lat), float(lon)) if lat is not None and lon is not None else None


def distance_km(a: Point, b: Point) -> float:
    """Distance on a local flat projection: enough under a few kilometres."""
    cos = math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot((a[0] - b[0]) * 111.195, (a[1] - b[1]) * 111.195 * cos)


def kind_of(tags: dict[str, str]) -> Optional[str]:
    """``"spot"`` (a place to surf), ``"school"`` (a school or a club: evidence for the beach near it) or ``None`` (a shop)."""
    if tags.get("club") or tags.get("leisure") == "sports_centre" or SCHOOL_WORDS.search(tags.get("name", "")):
        return "school"
    if any(tag in tags for tag in BUSINESS_TAGS) or any(tag.startswith("addr:") for tag in tags):
        return None
    return "spot"


def osm_ref(element: dict[str, Any]) -> str:
    return f"{element['type']}/{element['id']}"


def bearing_deg(a: Point, b: Point) -> float:
    """Initial bearing from ``a`` to ``b`` (``(lat, lon)``), degrees clockwise from north."""
    lat1, lat2 = math.radians(a[0]), math.radians(b[0])
    dlon = math.radians(b[1] - a[1])
    y = math.sin(dlon) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return math.degrees(math.atan2(y, x)) % 360.0


def facing_from_coastline(ways: list[list[Point]], at: Point) -> Optional[int]:
    """The direction from ``at`` to the open sea: the coastline near it, turned 90 degrees to the right (water on the right).

    ``ways`` are coastline ways as lists of ``(lat, lon)``, in their OpenStreetMap order. The coastline is followed for
    ``FACING_SPAN_M`` on each side of its vertex nearest to ``at``; ``None`` when no way has a vertex within ``COAST_KM``.
    """
    best: Optional[tuple[float, int, int]] = None  # distance, way, index of the nearest vertex
    for w, points in enumerate(ways):
        for i, point in enumerate(points):
            d = distance_km(at, point)
            if best is None or d < best[0]:
                best = (d, w, i)
    if best is None or best[0] > COAST_KM:
        return None
    points = ways[best[1]]

    def walk(step: int) -> Point:
        travelled, i = 0.0, best[2]
        while 0 <= i + step < len(points) and travelled < FACING_SPAN_M / 1000:
            travelled += distance_km(points[i], points[i + step])
            i += step
        return points[i]

    start, end = walk(-1), walk(+1)
    if distance_km(start, end) < 0.05:
        return None
    return round((bearing_deg(start, end) + 90.0) % 360.0) % 360


def slug(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")


def candidates(surf: list[dict[str, Any]], beaches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The spots before the coastline check: OpenStreetMap spots, and the named beaches near a school or a club."""
    named_beaches: list[tuple[Point, dict[str, Any]]] = []
    for beach in beaches:
        where = position(beach)
        if where is not None and beach.get("tags", {}).get("name"):
            named_beaches.append((where, beach))
    found: dict[str, dict[str, Any]] = {}
    for element in surf:
        tags = element.get("tags", {})
        where = position(element)
        kind = kind_of(tags)
        if where is None or kind is None:
            continue
        here: Point = where
        near = min(named_beaches, key=lambda nb: distance_km(here, nb[0]), default=None)
        beach = near[1] if near is not None and distance_km(here, near[0]) <= EVIDENCE_KM else None
        evidence = {"osm": osm_ref(element), "name": tags.get("name"), "kind": kind}
        if kind == "spot" and (tags.get("name") or beach is not None):
            # The spot itself, at its own position; named after its beach when one is close (a spot is often tagged with the
            # name of a school), else by its own name.
            key = osm_ref(element)
            close = beach is not None and near is not None and distance_km(here, near[0]) <= MERGE_KM
            entry = found.setdefault(key, {
                "name": beach["tags"]["name"] if close else (tags.get("name") or beach["tags"]["name"]),  # type: ignore[index]
                "at": where,
                "osm": key,
                "beach_surface": tags.get("surface") or (beach or {}).get("tags", {}).get("surface"),
                "evidence": [],
            })
            entry["evidence"].append(evidence)
        elif kind == "school" and beach is not None and near is not None:
            key = osm_ref(beach)
            entry = found.setdefault(key, {
                "name": beach["tags"]["name"],
                "at": near[0],
                "osm": key,
                "beach_surface": beach["tags"].get("surface"),
                "evidence": [],
            })
            entry["evidence"].append(evidence)
    return list(found.values())


def merge_close(spots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Two spots closer than ``MERGE_KM`` are one: an OpenStreetMap spot wins over a beach, then the most evidence."""
    ordered = sorted(
        spots, key=lambda s: (not any(e["kind"] == "spot" for e in s["evidence"]), -len(s["evidence"]), s["osm"])
    )
    kept: list[dict[str, Any]] = []
    for spot in ordered:
        twin = next((k for k in kept if distance_km(k["at"], spot["at"]) < MERGE_KM), None)
        if twin is None:
            kept.append(spot)
        else:
            twin["evidence"].extend(e for e in spot["evidence"] if e not in twin["evidence"])
    return kept


def nearest_place(places: list[dict[str, Any]], at: Point, within_km: float = PLACE_KM) -> Optional[str]:
    """The name of the nearest place of ``places`` OpenStreetMap names, within ``within_km``."""
    best: Optional[tuple[float, str]] = None
    for place in places:
        where, name = position(place), place.get("tags", {}).get("name")
        if where is None or not name:
            continue
        d = distance_km(at, where)
        if best is None or d < best[0]:
            best = (d, name)
    return best[1] if best is not None and best[0] <= within_km else None


def coastline_ways(answer: dict[str, Any]) -> list[list[Point]]:
    return [
        [(float(p["lat"]), float(p["lon"])) for p in element.get("geometry", [])]
        for element in answer.get("elements", [])
        if element.get("type") == "way"
    ]


def build(cache_dir: Optional[Path]) -> dict[str, Any]:
    south, west, north, east = BBOX
    box = f"({south},{west},{north},{east})"
    surf = overpass(
        f'[out:json][timeout:120];{FRANCE}(nwr["sport"~"(^|;)surfing(;|$)"](area.fr){box};);out center tags;', cache_dir
    )["elements"]
    beaches = overpass(
        f'[out:json][timeout:180];{FRANCE}(way["natural"="beach"]["name"](area.fr){box};'
        f'relation["natural"="beach"]["name"](area.fr){box};);out center tags;',
        cache_dir,
    )["elements"]
    places = overpass(
        f'[out:json][timeout:180];{FRANCE}(node["place"~"^(city|town|village)$"]["name"](area.fr){box};);out;',
        cache_dir,
    )["elements"]
    # A beach resort far from its town is often a suburb of it (Lacanau-Océan, Biscarrosse-Plage, Le Truc Vert): named only
    # when no city, town or village is within PLACE_KM.
    suburbs = overpass(
        f'[out:json][timeout:180];{FRANCE}(node["place"="suburb"]["name"](area.fr){box};);out;',
        cache_dir,
    )["elements"]

    # The coastline near every candidate, in one request (one "around" per candidate): Overpass is a shared service.
    found = candidates(surf, beaches)
    radius = int(COAST_KM * 1000)
    arounds = "".join(f'way["natural"="coastline"](around:{radius},{s["at"][0]},{s["at"][1]});' for s in found)
    coast = coastline_ways(overpass(f"[out:json][timeout:600];({arounds});out geom;", cache_dir))
    spots: list[dict[str, Any]] = []
    for spot in found:
        facing = facing_from_coastline(coast, spot["at"])
        if facing is None:
            print(f"dropped: {spot['name']} ({spot['osm']}), no coastline within {COAST_KM} km", file=sys.stderr)
            continue
        spot["facing_deg"] = facing
        spots.append(spot)

    out: list[dict[str, Any]] = []
    used: set[str] = set()
    for spot in sorted(merge_close(spots), key=lambda s: (-round(s["at"][0], 3), s["at"][1])):
        place = nearest_place(places, spot["at"]) or nearest_place(suburbs, spot["at"], SUBURB_KM)
        base = slug(f"{place or ''} {spot['name']}") or slug(spot["osm"])
        ident, n = base, 2
        while ident in used:
            ident, n = f"{base}-{n}", n + 1
        used.add(ident)
        out.append({
            "id": ident,
            "name": spot["name"],
            "place": place,
            "lat": round(spot["at"][0], 4),
            "lon": round(spot["at"][1], 4),
            "facing_deg": spot["facing_deg"],
            "beach_surface": spot["beach_surface"],
            "bottom": None,
            "best_tide": None,
            "best_swell_from_deg": None,
            "osm": spot["osm"],
            "evidence": sorted(spot["evidence"], key=lambda e: e["osm"]),
        })
    return {
        "built_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "OpenStreetMap, through the Overpass API (tools/build_spots.py)",
        "licence": "ODbL-1.0",
        "attribution": "© OpenStreetMap contributors",
        "derived": ["facing_deg", "place", "id"],
        "spots": out,
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--cache-dir", type=Path, default=None, help="keep the answers of Overpass here")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args(argv)
    data = build(args.cache_dir)
    args.output.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{len(data['spots'])} spots -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
