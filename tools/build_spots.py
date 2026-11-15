"""Build the list of surf spots of the French Atlantic, Channel and North Sea coasts (``app/data/surf-spots.json``).

Nothing is fetched from OpenStreetMap, GIP Littoral or SHOM at run time: this script builds the bundled file once and saves
provider answers in ``--cache-dir`` when passed::

    python tools/build_spots.py                    # ask providers, write app/data/surf-spots.json
    python tools/build_spots.py --cache-dir DIR    # reuse cached answers from DIR

What makes a spot (each one carries the elements that make it one in ``evidence``):

- an OpenStreetMap element tagged ``sport=surfing`` that is a place to surf (not a shop, school or club), named by itself
  or by the beach it is on;
- or a named OpenStreetMap beach (``natural=beach``) within ``EVIDENCE_KM`` of a surf school or surf club tagged ``sport=surfing``
  (measuring to the beach boundary when Overpass returns its geometry, so long beaches match schools at either end);
- or an oceanic supervised beach from GIP Littoral (*Plages de Nouvelle-Aquitaine*, Licence Ouverte 2.0, ``nature_baignade=Océanique``)
  matching the coastline criteria.

Derived fields computed by Vigie (named in ``derived``):

- ``facing_deg``: direction from the beach to the open sea, perpendicular to the OpenStreetMap coastline over ``FACING_SPAN_M``
  on each side (water on the right).
- ``open_water_sectors`` & ``open_water_width_deg``: unobstructed angular sectors (5-degree step, rays clear to
  ``OPEN_WATER_KM`` km) cast from ``OFFSHORE_PROBE_KM`` (80 m offshore, or 150 m when locally enclosed) against the coastline
  (including nearby cross-border coasts/islands, ignoring tiny islets smaller than ``MIN_ISLET_DIAMETER_KM``) and harbour
  breakwaters/groins (``man_made=breakwater|groin|groyne``).
- ``bottom`` & ``seafloor_shom``: nearshore seafloor category (``sand``, ``rock``, ``gravel``, ``mixed``, ``mud``, or ``null``
  when unmapped at 1/50 000) queried at ``OFFSHORE_PROBE_KM`` offshore (within ``SEAFLOOR_KM``) from SHOM *Natures de fond au
  1/50 000* (Licence Ouverte 2.0, ``https://dx.doi.org/10.17183/HOM_GEOL_NATURES_FOND_50``; not for navigation).
- ``place`` & ``id``: nearest OpenStreetMap locality and stable slug (preserving any legacy spot id matched by OSM reference).

What is **not** guessed because no open source supplies it: ``best_tide`` and ``best_swell_from_deg`` remain ``null``.

Licences: derived from OpenStreetMap (**ODbL 1.0**), GIP Littoral (**Licence Ouverte 2.0**) and SHOM (**Licence Ouverte 2.0**).
Notice: ``app/data/surf-spots.NOTICE.md``. The code stays MIT.
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
CORRECTIONS = Path(__file__).resolve().parent / "surf-spot-corrections.json"
OVERPASS_URLS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
)
GIP_PLAGES_URL = "https://www.pigma.org/fr/dataset/datasets/6870/resource/13842/download/"
SHOM_WFS_URL = "https://services.data.shom.fr/INSPIRE/wfs"
USER_AGENT = "vigie-api build_spots (https://github.com/Axellum/vigie-api)"

# South, west, north, east: the French coasts of the Atlantic, the Channel and the North Sea (the Mediterranean is east of it).
BBOX = (43.3, -5.3, 51.2, 2.7)
# Coastline shelter bands include nearby Spanish Basque coast, Channel Islands and Belgian coast.
SHELTER_BANDS = (
    (43.1, -2.6, 45.8, -0.8),
    (45.7, -2.5, 47.5, -0.8),
    (47.3, -5.5, 49.0, -1.3),
    (48.8, -5.2, 50.1, 0.5),
    (49.5, -0.2, 51.3, 2.8),
)
FRANCE = 'area["ISO3166-1"="FR"][admin_level=2]->.fr;'

EVIDENCE_KM = 1.0
COAST_KM = 0.65
FACING_SPAN_M = 400.0
MERGE_KM = 0.75
MERGE_DISTINCT_KM = 0.25
PLACE_KM = 8.0
SUBURB_KM = 2.0
GIP_MATCH_KM = 0.75
OPEN_WATER_STEP_DEG = 5
OPEN_WATER_KM = 20.0
OFFSHORE_PROBE_KM = 0.08
MIN_ISLET_DIAMETER_KM = 0.15
SEAFLOOR_KM = 0.35

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

_SHOM_ROCK = {"NFRoche"}
_SHOM_SAND = {"NFS", "NFSF", "NFSV", "NFSFV"}
_SHOM_GRAVEL = {"NFG", "NFGC", "NFGS", "NFC", "NFCG", "NFSG"}
_SHOM_MUD = {"NFV"}

Point = tuple[float, float]


def overpass(query: str, cache_dir: Optional[Path]) -> dict[str, Any]:
    """The answer of Overpass to ``query``, kept in ``cache_dir`` (by the hash of the query) when one is given."""
    key = hashlib.sha256(query.encode("utf-8")).hexdigest()[:16]
    cached = cache_dir / f"overpass-{key}.json" if cache_dir else None
    if cached and cached.exists():
        return json.loads(cached.read_text(encoding="utf-8"))
    with httpx.Client(timeout=180.0, headers={"User-Agent": USER_AGENT}) as client:
        last_error: Optional[Exception] = None
        for url in OVERPASS_URLS:
            for wait_s in (10, 30, 0):
                try:
                    response = client.post(url, data={"data": query})
                    if response.status_code not in (406, 429, 502, 503, 504):
                        response.raise_for_status()
                        answer = response.json()
                        if cached:
                            cached.parent.mkdir(parents=True, exist_ok=True)
                            cached.write_text(json.dumps(answer), encoding="utf-8")
                        time.sleep(1.0)
                        return answer
                    print(f"overpass {url}: {response.status_code}, retry/next", file=sys.stderr)
                except Exception as exc:
                    last_error = exc
                    print(f"overpass {url}: {exc}", file=sys.stderr)
                if wait_s:
                    time.sleep(wait_s)
        if last_error is not None:
            raise last_error
        raise RuntimeError("Overpass query failed on all endpoints")


def fetch_json_cached(url: str, cache_name: str, cache_dir: Optional[Path], params: Optional[dict[str, str]] = None) -> Any:
    """Download JSON/GeoJSON with optional disk caching in ``cache_dir``."""
    cached = cache_dir / cache_name if cache_dir else None
    if cached and cached.exists():
        return json.loads(cached.read_text(encoding="utf-8"))
    with httpx.Client(timeout=90.0, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
        response = client.get(url, params=params)
        response.raise_for_status()
        data = response.json()
    if cached:
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps(data), encoding="utf-8")
    return data


def position(element: dict[str, Any]) -> Optional[Point]:
    """``(lat, lon)`` of a node, an Overpass ``center``, ``bounds`` midpoint, or geometry midpoint."""
    centre = element.get("center") or element
    lat, lon = centre.get("lat"), centre.get("lon")
    if lat is not None and lon is not None:
        return (float(lat), float(lon))
    bounds = element.get("bounds")
    if isinstance(bounds, dict) and {"minlat", "maxlat", "minlon", "maxlon"} <= bounds.keys():
        return (
            (float(bounds["minlat"]) + float(bounds["maxlat"])) / 2.0,
            (float(bounds["minlon"]) + float(bounds["maxlon"])) / 2.0,
        )
    geom = element.get("geometry")
    if isinstance(geom, list) and geom:
        pts = [(float(p["lat"]), float(p["lon"])) for p in geom if "lat" in p and "lon" in p]
        if pts:
            return pts[len(pts) // 2]
    return None


def distance_km(a: Point, b: Point) -> float:
    """Distance on a local flat projection: enough under a few kilometres."""
    cos = math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot((a[0] - b[0]) * 111.195, (a[1] - b[1]) * 111.195 * cos)


def element_distance_km(point: Point, element: dict[str, Any]) -> Optional[float]:
    """Minimum distance from ``point`` to an element's geometry edges/vertices (or its position when absent)."""
    geom = element.get("geometry")
    if isinstance(geom, list) and geom:
        pts = [(float(p["lat"]), float(p["lon"])) for p in geom if "lat" in p and "lon" in p]
        if len(pts) >= 2:
            return min(_seg_point_dist_km(point, a, b) for a, b in zip(pts[:-1], pts[1:], strict=True))
        if pts:
            return distance_km(point, pts[0])
    pos = position(element)
    return distance_km(point, pos) if pos is not None else None


def anchor_point(element: dict[str, Any], near_point: Point) -> Optional[Point]:
    """Position of a beach spot: its center if compact, or nearest point along the beach if center > EVIDENCE_KM."""
    center = position(element)
    geom = element.get("geometry")
    if isinstance(geom, list) and len(geom) >= 2:
        pts = [(float(p["lat"]), float(p["lon"])) for p in geom if "lat" in p and "lon" in p]
        if pts:
            if center is not None and distance_km(center, near_point) <= EVIDENCE_KM:
                return center
            return min(pts, key=lambda p: distance_km(p, near_point))
    return center


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


def offset_point(at: Point, bearing: float, dist_km: float) -> Point:
    """Shift ``at`` by ``dist_km`` along clockwise bearing ``bearing`` degrees."""
    rad = math.radians(bearing)
    dlat = (dist_km * math.cos(rad)) / 111.195
    dlon = (dist_km * math.sin(rad)) / (111.195 * math.cos(math.radians(at[0])))
    return (at[0] + dlat, at[1] + dlon)


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


def _ray_segment_intersect(ox: float, oy: float, dx: float, dy: float, ax: float, ay: float, bx: float, by: float) -> Optional[float]:
    sx, sy = bx - ax, by - ay
    denom = dx * sy - dy * sx
    if abs(denom) < 1e-12:
        return None
    qpx, qpy = ax - ox, ay - oy
    t = (qpx * sy - qpy * sx) / denom
    u = (qpx * dy - qpy * dx) / denom
    if t > 0.0 and 0.0 <= u <= 1.0:
        return t
    return None


def _way_diameter_km(points: list[Point]) -> float:
    if len(points) < 2:
        return 0.0
    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    return distance_km((min(lats), min(lons)), (max(lats), max(lons)))


def summarize_open_sectors(open_bearings: list[int], step_deg: int = OPEN_WATER_STEP_DEG) -> list[list[int]]:
    """Collapse sorted unobstructed azimuths into contiguous ``[start_deg, end_deg]`` intervals wrapping through 0."""
    if not open_bearings:
        return []
    present = set(open_bearings)
    runs: list[list[int]] = []
    visited: set[int] = set()
    for deg in open_bearings:
        if deg in visited:
            continue
        start = deg
        while ((start - step_deg) % 360) in present:
            cand = (start - step_deg) % 360
            if cand == deg:
                break
            start = cand
        end = deg
        while ((end + step_deg) % 360) in present:
            cand = (end + step_deg) % 360
            if cand == start:
                break
            end = cand
        cur = start
        while True:
            visited.add(cur)
            if cur == end:
                break
            cur = (cur + step_deg) % 360
        runs.append([start, end])
    runs.sort(key=lambda r: r[0])
    return runs


def open_water_from_obstacles(
    coast_ways: list[list[Point]],
    barrier_ways: list[list[Point]],
    at: Point,
    facing_deg: int,
) -> tuple[list[list[int]], int]:
    """Compute unobstructed 360-deg open-water sectors (>= ``OPEN_WATER_KM`` km clear) from offshore of ``at``.

    Tiny closed coastline loops smaller than ``MIN_ISLET_DIAMETER_KM`` (isolated rocks) are ignored, while all mainland
    coastlines, islands and harbour breakwaters/groins are treated as opaque wave barriers.
    """
    kx = 111.195 * math.cos(math.radians(at[0]))
    ky = 111.195

    def to_xy(pt: Point) -> tuple[float, float]:
        return (pt[1] - at[1]) * kx, (pt[0] - at[0]) * ky

    # Find nearest coastline point near ``at`` to avoid placing ray origin landward of a concave beach.
    nearest_pt = at
    best_d = COAST_KM + 1.0
    for pts in coast_ways:
        for pt in pts:
            if abs(pt[0] - at[0]) > 0.03 or abs(pt[1] - at[1]) > 0.04:
                continue
            d = distance_km(at, pt)
            if d < best_d:
                best_d = d
                nearest_pt = pt

    # Filter segments within OPEN_WATER_KM + 5 km
    deg_lat_lim = (OPEN_WATER_KM + 5.0) / 111.195
    deg_lon_lim = (OPEN_WATER_KM + 5.0) / max(kx, 1e-6)
    local_segs: list[tuple[tuple[float, float], tuple[float, float], float, float, float, float]] = []

    def add_ways(ways: list[list[Point]], filter_islets: bool) -> None:
        for pts in ways:
            if len(pts) < 2:
                continue
            if not any(abs(p[0] - at[0]) <= deg_lat_lim and abs(p[1] - at[1]) <= deg_lon_lim for p in pts):
                continue
            if filter_islets and pts[0] == pts[-1] and _way_diameter_km(pts) < MIN_ISLET_DIAMETER_KM:
                continue
            xys = [to_xy(p) for p in pts]
            for (ax, ay), (bx, by) in zip(xys[:-1], xys[1:], strict=True):
                local_segs.append(((ax, ay), (bx, by), min(ax, bx), max(ax, bx), min(ay, by), max(ay, by)))

    add_ways(coast_ways, filter_islets=True)
    add_ways(barrier_ways, filter_islets=False)

    def cast_from(probe_km: float) -> list[int]:
        ox, oy = to_xy(offset_point(nearest_pt, facing_deg, probe_km))
        open_bearings: list[int] = []
        for deg in range(0, 360, OPEN_WATER_STEP_DEG):
            rad = math.radians(deg)
            dx, dy = math.sin(rad), math.cos(rad)
            min_hit = OPEN_WATER_KM + 1.0
            for (ax, ay), (bx, by), minx, maxx, miny, maxy in local_segs:
                if dx > 0 and maxx < ox:
                    continue
                if dx < 0 and minx > ox:
                    continue
                if dy > 0 and maxy < oy:
                    continue
                if dy < 0 and miny > oy:
                    continue
                t = _ray_segment_intersect(ox, oy, dx, dy, ax, ay, bx, by)
                if t is not None and t < min_hit:
                    min_hit = t
                    if min_hit < OPEN_WATER_KM:
                        break
            if min_hit >= OPEN_WATER_KM:
                open_bearings.append(deg)
        return open_bearings

    open_bearings = cast_from(OFFSHORE_PROBE_KM)
    if not open_bearings and OFFSHORE_PROBE_KM < 0.15:
        open_bearings = cast_from(0.15)
    return summarize_open_sectors(open_bearings), len(open_bearings) * OPEN_WATER_STEP_DEG


def slug(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")


def _gip_beach_surface(props: dict[str, Any]) -> Optional[str]:
    car = (props.get("plage_caracteristiques") or "").lower()
    has_sand = "sable" in car
    has_pebble = "galet" in car
    has_rock = "rocher" in car
    if has_sand and has_pebble:
        return "sand;pebbles"
    if has_sand and has_rock:
        return "sand;rock"
    if has_pebble:
        return "pebbles"
    if has_sand:
        return "sand"
    return None


def _gip_lifeguard_info(props: dict[str, Any]) -> Optional[bool]:
    val = (props.get("plage_surveillee") or "").strip().lower()
    if val == "oui":
        return True
    if val == "non":
        return False
    return None


def _safe_el_dist(point: Point, element: dict[str, Any]) -> float:
    d = element_distance_km(point, element)
    return d if d is not None else 1e9


def _nearest_named_beach(here: Point, named_beaches: list[tuple[Point, dict[str, Any]]]) -> tuple[Optional[dict[str, Any]], float]:
    """Match nearest beach by center within ``EVIDENCE_KM`` first (preserving compact beach choices), else by boundary edge."""
    best_center: Optional[tuple[float, dict[str, Any]]] = None
    best_edge: Optional[tuple[float, dict[str, Any]]] = None
    for center, beach in named_beaches:
        cd = distance_km(here, center)
        if best_center is None or cd < best_center[0]:
            best_center = (cd, beach)
        ed = _safe_el_dist(here, beach)
        if best_edge is None or ed < best_edge[0]:
            best_edge = (ed, beach)
    if best_center is not None and best_center[0] <= EVIDENCE_KM:
        return best_center[1], best_center[0]
    if best_edge is not None and best_edge[0] <= EVIDENCE_KM:
        return best_edge[1], best_edge[0]
    return None, 1e9


def candidates(
    surf: list[dict[str, Any]],
    beaches: list[dict[str, Any]],
    gip_features: Optional[list[dict[str, Any]]] = None,
) -> list[dict[str, Any]]:
    """The spots before the coastline check: OSM spots, beaches near surf schools, and GIP Littoral oceanic beaches."""
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
        beach, near_d = _nearest_named_beach(here, named_beaches)
        evidence = {"osm": osm_ref(element), "name": tags.get("name"), "kind": kind}
        if kind == "spot" and (tags.get("name") or beach is not None):
            key = osm_ref(element)
            close = beach is not None and near_d <= 0.60
            entry = found.setdefault(
                key,
                {
                    "name": beach["tags"]["name"] if close else (tags.get("name") or beach["tags"]["name"]),  # type: ignore[index]
                    "at": where,
                    "osm": key,
                    "beach_surface": tags.get("surface") or (beach or {}).get("tags", {}).get("surface"),
                    "lifeguarded": None,
                    "evidence": [],
                },
            )
            entry["evidence"].append(evidence)
        elif kind == "school" and beach is not None:
            key = osm_ref(beach)
            spot_at = anchor_point(beach, here) or position(beach) or here
            entry = found.setdefault(
                key,
                {
                    "name": beach["tags"]["name"],
                    "at": spot_at,
                    "osm": key,
                    "beach_surface": beach["tags"].get("surface"),
                    "lifeguarded": None,
                    "evidence": [],
                },
            )
            entry["evidence"].append(evidence)

    # Enrich existing spots and add missing oceanic beaches from GIP Littoral (Licence Ouverte 2.0)
    south, west, north, east = BBOX
    for ft in gip_features or []:
        props = ft.get("properties") or {}
        if props.get("nature_baignade") != "Océanique":
            continue
        coords = (ft.get("geometry") or {}).get("coordinates")
        if not coords or len(coords) < 2:
            continue
        at_gip: Point = (float(coords[1]), float(coords[0]))
        if not (south <= at_gip[0] <= north and west <= at_gip[1] <= east):
            continue
        gip_id = str(props.get("id_plage_sit") or props.get("id_plage_gip") or "").strip()
        gip_name = (props.get("nom_plage") or "").strip()
        if not gip_id or not gip_name:
            continue
        gip_slug = slug(gip_name)
        gip_ev = {"source": "gip_littoral", "id": gip_id, "name": gip_name, "kind": "beach"}
        gip_surf = _gip_beach_surface(props)
        gip_lg = _gip_lifeguard_info(props)

        # Attach to an existing OSM candidate when it is clearly the same beach (same name <= GIP_MATCH_KM or <= 0.20 km).
        same_spots = [
            (distance_km(at_gip, s["at"]), s)
            for s in found.values()
            if (slug(s["name"]) == gip_slug and distance_km(at_gip, s["at"]) <= GIP_MATCH_KM)
            or distance_km(at_gip, s["at"]) <= 0.20
        ]
        if same_spots:
            _, close_spot = min(same_spots, key=lambda pair: pair[0])
            if not close_spot["beach_surface"] and gip_surf:
                close_spot["beach_surface"] = gip_surf
            if close_spot["lifeguarded"] is None and gip_lg is not None:
                close_spot["lifeguarded"] = gip_lg
            if gip_ev not in close_spot["evidence"]:
                close_spot["evidence"].append(gip_ev)
            continue

        # Reuse an OSM beach ref only when it carries the same beach name and isn't already taken by a distinct point.
        same_named_osm = [
            (_safe_el_dist(at_gip, nb[1]), nb)
            for nb in named_beaches
            if slug(nb[1]["tags"]["name"]) == gip_slug and _safe_el_dist(at_gip, nb[1]) <= 0.40
        ]
        if same_named_osm:
            _, near_b = min(same_named_osm, key=lambda pair: pair[0])
            key = osm_ref(near_b[1])
            if key not in found:
                found[key] = {
                    "name": near_b[1]["tags"]["name"],
                    "at": at_gip,
                    "osm": key,
                    "beach_surface": near_b[1]["tags"].get("surface") or gip_surf,
                    "lifeguarded": gip_lg,
                    "evidence": [gip_ev],
                }
                continue

        key = f"gip/{gip_id}"
        found[key] = {
            "name": gip_name,
            "at": at_gip,
            "osm": key,
            "beach_surface": gip_surf,
            "lifeguarded": gip_lg,
            "evidence": [gip_ev],
        }

    return list(found.values())


def merge_close(spots: list[dict[str, Any]], legacy_osm: Optional[set[str]] = None) -> list[dict[str, Any]]:
    """Merge duplicate elements: legacy OSM spots and `sport=surfing` win, distinct names keep down to ``MERGE_DISTINCT_KM``."""
    legacy_set = legacy_osm or set()
    ordered = sorted(
        spots,
        key=lambda s: (
            s["osm"] not in legacy_set,
            not any(e["kind"] == "spot" for e in s["evidence"]),
            -len(s["evidence"]),
            s["osm"],
        ),
    )
    kept: list[dict[str, Any]] = []
    for spot in ordered:
        spot_slug = slug(spot["name"])
        twin = None
        for k in kept:
            d = distance_km(k["at"], spot["at"])
            threshold = MERGE_KM if slug(k["name"]) == spot_slug else MERGE_DISTINCT_KM
            if d < threshold:
                twin = k
                break
        if twin is None:
            kept.append(spot)
        else:
            twin["evidence"].extend(e for e in spot["evidence"] if e not in twin["evidence"])
            if not twin.get("beach_surface") and spot.get("beach_surface"):
                twin["beach_surface"] = spot["beach_surface"]
            if twin.get("lifeguarded") is None and spot.get("lifeguarded") is not None:
                twin["lifeguarded"] = spot["lifeguarded"]
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


def load_corrections(path: Path = CORRECTIONS) -> dict[str, dict[str, str]]:
    """The corrected names, by OpenStreetMap element."""
    return {c["osm"]: c for c in json.loads(path.read_text(encoding="utf-8"))["corrections"]}


def corrected(name: str, osm: str, corrections: dict[str, dict[str, str]]) -> tuple[str, Optional[str]]:
    """The name of a spot, and the name OpenStreetMap gives it when the table corrects it (else ``None``)."""
    fix = corrections.get(osm)
    if fix is None:
        return name, None
    if fix["osm_name"] != name:
        print(f"correction not applied: {osm} is now {name!r} in OpenStreetMap, not {fix['osm_name']!r}", file=sys.stderr)
        return name, None
    return fix["name"], name


def coastline_ways(answer: dict[str, Any]) -> list[list[Point]]:
    return [
        [(float(p["lat"]), float(p["lon"])) for p in element.get("geometry", [])]
        for element in answer.get("elements", [])
        if element.get("type") == "way"
    ]


def _seg_point_dist_km(at: Point, a: Point, b: Point) -> float:
    kx = 111.195 * math.cos(math.radians(at[0]))
    ky = 111.195
    ax, ay = (a[1] - at[1]) * kx, (a[0] - at[0]) * ky
    bx, by = (b[1] - at[1]) * kx, (b[0] - at[0]) * ky
    vx, vy = bx - ax, by - ay
    l2 = vx * vx + vy * vy
    if l2 == 0:
        return math.hypot(ax, ay)
    t = max(0.0, min(1.0, (-ax * vx - ay * vy) / l2))
    return math.hypot(ax + t * vx, ay + t * vy)


def _point_in_ring(lon: float, lat: float, ring: list[list[float]]) -> bool:
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if ((yi > lat) != (yj > lat)) and (lon < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi):
            inside = not inside
        j = i
    return inside


def _polygon_dist_km(at: Point, geom: dict[str, Any]) -> float:
    lat, lon = at
    polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
    best = 999.0
    for poly in polys:
        outer = poly[0]
        if _point_in_ring(lon, lat, outer):
            return 0.0
        pts = [(p[1], p[0]) for p in outer]
        for a, b in zip(pts[:-1], pts[1:], strict=True):
            d = _seg_point_dist_km(at, a, b)
            if d < best:
                best = d
    return best


def _shom_ndf_tiles(spots: list[dict[str, Any]], cache_dir: Optional[Path]) -> list[dict[str, Any]]:
    """Fetch SHOM 1/50 000 seafloor polygons once per 0.4 deg coastal cell covering spots."""
    cells: set[tuple[int, int]] = set()
    for s in spots:
        cells.add((math.floor(s["at"][0] * 2.5), math.floor(s["at"][1] * 2.5)))
    feats: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for clat, clon in sorted(cells):
        s_lat, w_lon = clat / 2.5 - 0.02, clon / 2.5 - 0.02
        n_lat, e_lon = (clat + 1) / 2.5 + 0.02, (clon + 1) / 2.5 + 0.02
        bbox_str = f"{w_lon:.3f},{s_lat:.3f},{e_lon:.3f},{n_lat:.3f},urn:ogc:def:crs:OGC:1.3:CRS84"
        cache_name = f"shom-ndf-{clat}-{clon}.json"
        data = fetch_json_cached(
            SHOM_WFS_URL,
            cache_name,
            cache_dir,
            params={
                "service": "WFS",
                "version": "2.0.0",
                "request": "GetFeature",
                "typeNames": "NDF_BDD_WLD_WGS84G_WFS:natures_fond_50000",
                "srsName": "urn:ogc:def:crs:OGC:1.3:CRS84",
                "outputFormat": "application/json",
                "count": "2000",
                "bbox": bbox_str,
            },
        )
        for ft in data.get("features", []):
            fid = str(ft.get("id") or len(feats))
            if fid not in seen_ids:
                seen_ids.add(fid)
                feats.append(ft)
    return feats


def classify_seafloor(at: Point, facing_deg: int, shom_feats: list[tuple[tuple[float, float, float, float], dict[str, Any], str]]) -> tuple[Optional[str], Optional[list[str]]]:
    """Classify nearshore seafloor at 200 m offshore along ``facing_deg`` from SHOM Natures de fond."""
    probe = offset_point(at, facing_deg, OFFSHORE_PROBE_KM)
    codes: set[str] = set()
    for (mnx, mny, mxx, mxy), geom, code in shom_feats:
        if probe[1] < mnx - 0.01 or probe[1] > mxx + 0.01 or probe[0] < mny - 0.01 or probe[0] > mxy + 0.01:
            continue
        if _polygon_dist_km(probe, geom) <= SEAFLOOR_KM:
            codes.add(code)
    if not codes:
        return None, None
    sorted_codes = sorted(codes)
    has_rock = bool(codes & _SHOM_ROCK)
    has_sand = bool(codes & _SHOM_SAND)
    has_gravel = bool(codes & _SHOM_GRAVEL)
    has_mud = bool(codes & _SHOM_MUD)
    if has_rock and (has_sand or has_gravel or has_mud):
        return "mixed", sorted_codes
    if has_rock:
        return "rock", sorted_codes
    if has_gravel and not has_sand:
        return "gravel", sorted_codes
    if has_sand:
        return "sand", sorted_codes
    if has_mud:
        return "mud", sorted_codes
    return None, sorted_codes


def _index_shom(feats: list[dict[str, Any]]) -> list[tuple[tuple[float, float, float, float], dict[str, Any], str]]:
    indexed: list[tuple[tuple[float, float, float, float], dict[str, Any], str]] = []
    for ft in feats:
        geom = ft.get("geometry")
        code = (ft.get("properties") or {}).get("typelem")
        if not geom or not code:
            continue
        polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
        xs = [p[0] for poly in polys for p in poly[0]]
        ys = [p[1] for poly in polys for p in poly[0]]
        if xs and ys:
            indexed.append(((min(xs), min(ys), max(xs), max(ys)), geom, str(code)))
    return indexed


def _legacy_ids(existing_output: Path = OUTPUT) -> dict[str, str]:
    """Read existing spot ``osm -> id`` mapping so previously published spot ids remain stable."""
    if not existing_output.exists():
        return {}
    try:
        data = json.loads(existing_output.read_text(encoding="utf-8"))
        return {str(s["osm"]): str(s["id"]) for s in data.get("spots", []) if s.get("osm") and s.get("id")}
    except Exception:
        return {}


def build(cache_dir: Optional[Path]) -> dict[str, Any]:
    south, west, north, east = BBOX
    box = f"({south},{west},{north},{east})"
    surf = overpass(
        f'[out:json][timeout:120];{FRANCE}(nwr["sport"~"(^|;)surfing(;|$)"](area.fr){box};);out center tags;', cache_dir
    )["elements"]
    beaches: list[dict[str, Any]] = []
    # Query named beach geometries in 2 latitude bands so Overpass never hits timeout/502.
    mid_lat = round((south + north) / 2, 2)
    for bs, bn in ((south, mid_lat), (mid_lat, north)):
        bbox_b = f"({bs},{west},{bn},{east})"
        beaches.extend(
            overpass(
                f'[out:json][timeout:180];{FRANCE}(way["natural"="beach"]["name"](area.fr){bbox_b};'
                f'relation["natural"="beach"]["name"](area.fr){bbox_b};);out center geom tags;',
                cache_dir,
            )["elements"]
        )
    places = overpass(
        f'[out:json][timeout:180];{FRANCE}(node["place"~"^(city|town|village)$"]["name"](area.fr){box};);out;',
        cache_dir,
    )["elements"]
    suburbs = overpass(
        f'[out:json][timeout:180];{FRANCE}(node["place"="suburb"]["name"](area.fr){box};);out;',
        cache_dir,
    )["elements"]

    gip_data = fetch_json_cached(GIP_PLAGES_URL, "gip-littoral-plages-na.geojson", cache_dir)
    gip_features = gip_data.get("features", [])

    found = candidates(surf, beaches, gip_features=gip_features)

    # Fetch coastline and marine barriers across coastal bands (including Spanish Basque coast / islands) for facing & shelter.
    coast_ways_all: list[list[Point]] = []
    barrier_ways_all: list[list[Point]] = []
    for bs, bw, bn, be in SHELTER_BANDS:
        band_box = f"({bs},{bw},{bn},{be})"
        band_res = overpass(
            f'[out:json][timeout:180];(way["natural"="coastline"]{band_box};way["man_made"~"^(breakwater|groin|groyne)$"]{band_box};);out geom;',
            cache_dir,
        )
        for el in band_res.get("elements", []):
            if el.get("type") != "way":
                continue
            pts = [(float(p["lat"]), float(p["lon"])) for p in el.get("geometry", [])]
            if len(pts) < 2:
                continue
            tags = el.get("tags", {})
            if tags.get("natural") == "coastline":
                coast_ways_all.append(pts)
            if tags.get("man_made") in ("breakwater", "groin", "groyne"):
                barrier_ways_all.append(pts)

    spots: list[dict[str, Any]] = []
    for spot in found:
        facing = facing_from_coastline(coast_ways_all, spot["at"])
        if facing is None:
            print(f"dropped: {spot['name']} ({spot['osm']}), no coastline within {COAST_KM} km", file=sys.stderr)
            continue
        spot["facing_deg"] = facing
        spots.append(spot)

    legacy = _legacy_ids()
    merged = merge_close(spots, set(legacy.keys()))
    shom_feats = _index_shom(_shom_ndf_tiles(merged, cache_dir))

    corrections = load_corrections()
    out: list[dict[str, Any]] = []
    # Reserve legacy IDs first so no existing spot ID changes or shifts when neighbouring beaches are added.
    used: set[str] = {legacy[s["osm"]] for s in merged if s["osm"] in legacy}
    for spot in sorted(merged, key=lambda s: (-round(s["at"][0], 3), s["at"][1])):
        sectors, width_deg = open_water_from_obstacles(coast_ways_all, barrier_ways_all, spot["at"], spot["facing_deg"])
        has_osm_surf = any(e["kind"] in ("spot", "school") for e in spot["evidence"])
        if not has_osm_surf and spot["osm"] not in legacy:
            if width_deg == 0:
                print(f"dropped: {spot['name']} ({spot['osm']}), no open-water sector within {OPEN_WATER_KM} km", file=sys.stderr)
                continue
            facing = spot["facing_deg"]
            aligned = False
            for a, b in sectors:
                step = a
                while True:
                    diff = abs((step - facing + 180) % 360 - 180)
                    if diff <= 90:
                        aligned = True
                        break
                    if step == b:
                        break
                    step = (step + OPEN_WATER_STEP_DEG) % 360
                if aligned:
                    break
            if not aligned:
                print(f"dropped: {spot['name']} ({spot['osm']}), open sector opposite to facing_deg={facing}", file=sys.stderr)
                continue
        place = nearest_place(places, spot["at"]) or nearest_place(suburbs, spot["at"], SUBURB_KM)
        name, osm_name = corrected(spot["name"], spot["osm"], corrections)
        if spot["osm"] in legacy:
            ident = legacy[spot["osm"]]
        else:
            base = slug(f"{place or ''} {name}") or slug(spot["osm"])
            ident, n = base, 2
            while ident in used:
                ident, n = f"{base}-{n}", n + 1
            used.add(ident)
        bottom, shom_codes = classify_seafloor(spot["at"], spot["facing_deg"], shom_feats)
        out.append({
            "id": ident,
            "name": name,
            "osm_name": osm_name,
            "place": place,
            "lat": round(spot["at"][0], 4),
            "lon": round(spot["at"][1], 4),
            "facing_deg": spot["facing_deg"],
            "open_water_sectors": sectors,
            "open_water_width_deg": width_deg,
            "beach_surface": spot["beach_surface"],
            "bottom": bottom,
            "seafloor_shom": shom_codes,
            "lifeguarded": spot.get("lifeguarded"),
            "best_tide": None,
            "best_swell_from_deg": None,
            "osm": spot["osm"],
            "evidence": sorted(spot["evidence"], key=lambda e: (e.get("osm") or e.get("id") or "")),
        })
    return {
        "built_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": (
            "OpenStreetMap (Overpass API), GIP Littoral Plages de Nouvelle-Aquitaine (pigma.org, Licence Ouverte 2.0), "
            "SHOM Natures de fond au 1/50 000 (services.data.shom.fr, Licence Ouverte 2.0) (tools/build_spots.py)"
        ),
        "licence": "ODbL-1.0 / Licence Ouverte 2.0",
        "attribution": (
            "© OpenStreetMap contributors | "
            "Beaches: GIP Littoral, Plages de Nouvelle-Aquitaine (Licence Ouverte 2.0) | "
            "Shom, 2025. Natures de fond au 1/50 000, https://dx.doi.org/10.17183/HOM_GEOL_NATURES_FOND_50 (not for navigation)"
        ),
        "derived": ["facing_deg", "open_water_sectors", "open_water_width_deg", "bottom", "place", "id"],
        "spots": out,
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--cache-dir", type=Path, default=None, help="keep the answers of Overpass and SHOM here")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args(argv)
    data = build(args.cache_dir)
    args.output.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"{len(data['spots'])} spots -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
