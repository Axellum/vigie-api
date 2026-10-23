"""Rebuild the geometry files the vigilances are localized with (``app/data/``).

A warning of the national services comes with a polygon, or only with codes (MeteoAlarm EMMA_ID or NUTS
codes, NWS UGC zones). To tell whether a point is inside a warning whose area is a code, the API needs the
shape of that code. This script builds the files it reads; nothing is read at run time from these sources::

    python tools/build_geometries.py                  # the files bundled in the repository
    python tools/build_geometries.py --countries-only # only the borders (after the WMO registry snapshot changed)
    python tools/build_geometries.py --emma           # an OPTIONAL local file, NOT bundled (see below)
    python tools/build_geometries.py --cache-dir DIR  # where the downloads are kept (default: a temp folder)

What the default build writes, and the licence of every input (checked on 2026-10-20, see ``SOURCES.md``):

- ``app/data/countries.geojson.gz``: the borders of **every country** of Natural Earth, to find the country of a point without a
  network call: a point in a country the instance has no source for still gets its country, and the answer says why nothing is read
  there (``app/services/uncovered.py``). Territories keep their own ISO code (Puerto Rico ``PR``, Greenland ``GL``...), and so do the
  overseas departments of France that Natural Earth draws inside France (``GF``, ``GP``, ``MQ``, ``RE``, ``YT``). Three places Natural
  Earth draws with no code are joined to the country they lie in (``MERGED_INTO``); the others with no code (the British sovereign
  bases, disputed reefs and glaciers) are not written, and the tool lists them.
  Input: Natural Earth ``ne_10m_admin_0_countries``, **public domain**
  (https://www.naturalearthdata.com/about/terms-of-use/). Simplified with a tolerance of 0.01 degree
  (about 1 km), coordinates rounded to 3 decimals.
- ``app/data/geocodes-nuts.geojson.gz``: the shapes of the NUTS regions MeteoAlarm uses as geocodes where
  the national service gives them (the French departments, NUTS3; the Belgian provinces, NUTS2).
  Input: the first-level administrative areas of Natural Earth ``ne_10m_admin_1_states_provinces``
  (**public domain**), given their NUTS codes by Wikidata (property P605, joined on the ISO 3166-2 code
  P300; Wikidata is **CC0**, https://www.wikidata.org/wiki/Wikidata:Licensing). Simplified with a tolerance
  of 0.005 degree (about 500 m), coordinates rounded to 3 decimals.

Not bundled, on purpose:

- The Eurostat/GISCO NUTS shapes: their terms (https://ec.europa.eu/eurostat/web/gisco/geodata/administrative-units)
  grant a non-exclusive, non-transferable right to use the data, not for commercial purposes, with the
  notice "(c) EuroGeographics for the administrative boundaries": a public repository cannot hand that on.
- The MeteoAlarm EMMA_ID shapes (``MeteoAlarm_Geocodes_<date>.json`` in the public GitLab repository
  https://gitlab.com/meteoalarm-pm-group/documents): the repository states no licence for them, and the
  terms of MeteoAlarm (https://meteoalarm.org/en/live/page/terms-and-conditions) give the reuse licence to
  the warnings ("Information"), not to the rest of their content. ``--emma`` builds the file for YOUR
  instance only (``app/data/local/emma_id.geojson.gz``, ignored by git, or the path in
  ``VIGIE_EMMA_GEOMETRIES``): read the terms before you share an instance that serves it.
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
import tempfile
import urllib.parse
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, Optional

import httpx
from shapely.geometry import MultiPolygon, Polygon, mapping, shape
from shapely.geometry.base import BaseGeometry

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
DATA_DIR = REPO_ROOT / "app" / "data"
LOCAL_DIR = DATA_DIR / "local"

NE_BASE = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson"
NE_COUNTRIES_URL = f"{NE_BASE}/ne_10m_admin_0_countries.geojson"
NE_ADMIN1_URL = f"{NE_BASE}/ne_10m_admin_1_states_provinces.geojson"
WIKIDATA_SPARQL_URL = "https://query.wikidata.org/sparql"
EMMA_URL = "https://gitlab.com/meteoalarm-pm-group/documents/-/raw/master/MeteoAlarm_Geocodes_2026_07_31.json"

COUNTRY_TOLERANCE_DEG = 0.01
NUTS_TOLERANCE_DEG = 0.005
EMMA_TOLERANCE_DEG = 0.005
DECIMALS = 3

# Natural Earth draws a few places with no ISO code (``-99``). Three of them lie inside a country that has one, and are joined to it
# (by their ``ADM0_A3``): a point there is in that country. The others with no code (the British sovereign base areas of Akrotiri and
# Dhekelia, the Guantanamo Bay lease, and disputed glaciers, banks and reefs) are not written: ``build_countries`` reports them.
MERGED_INTO: dict[str, str] = {
    "SOL": "SO",  # Somaliland
    "CYN": "CY",  # Northern Cyprus
    "CNM": "CY",  # the United Nations buffer zone of Cyprus
}

# Where a country's warnings apply, when it is only part of the country Natural Earth draws: France is drawn with
# its overseas departments, but the MeteoAlarm feed of France covers the metropole (and Corsica) only.
# (min lon, min lat, max lon, max lat) of the polygons to keep, by their centroid.
COUNTRY_EXTENT: dict[str, tuple[float, float, float, float]] = {"FR": (-10.0, 40.0, 12.0, 52.0)}

# The parts of such a country that are cut off, kept apart under their own ISO 3166-1 code and English name, by the bounding box
# that holds the centroid of their polygons: the overseas departments of France.
SPLIT_PARTS: dict[str, dict[str, tuple[str, tuple[float, float, float, float]]]] = {
    "FR": {
        "GF": ("French Guiana", (-55.0, 2.0, -51.0, 6.0)),
        "GP": ("Guadeloupe", (-62.0, 15.7, -60.9, 16.6)),
        "MQ": ("Martinique", (-61.3, 14.3, -60.7, 15.0)),
        "RE": ("Réunion", (55.0, -21.5, 56.0, -20.7)),
        "YT": ("Mayotte", (44.9, -13.1, 45.4, -12.5)),
    }
}

# NUTS levels to take per country, and the pattern of the code at that level.
NUTS_LEVELS: dict[str, tuple[str, re.Pattern[str]]] = {
    "FR": ("NUTS3", re.compile(r"^FR[0-9A-Z]{3}$")),  # the departments (old codes FR713 and new ones FRK26)
    "BE": ("NUTS2", re.compile(r"^BE[0-9]{2}$")),  # the provinces and Brussels
}

NUTS_QUERY = """
SELECT ?iso ?nuts WHERE {
  ?area wdt:P300 ?iso .
  ?area p:P605/ps:P605 ?nuts .
  FILTER(REGEX(?iso, "^(FR|BE)-"))
}
"""


def download(url: str, destination: Path, params: Optional[dict[str, str]] = None) -> Path:
    """Download ``url`` once into ``destination`` (an existing file is kept: delete it to download again)."""
    if destination.exists() and destination.stat().st_size > 0:
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": "vigie-api build_geometries (https://github.com/Axellum/vigie-api)"}
    print(f"downloading {url}", file=sys.stderr)
    with httpx.Client(timeout=300.0, follow_redirects=True, headers=headers) as client:
        response = client.get(url, params=params)
        response.raise_for_status()
        destination.write_bytes(response.content)
    return destination


def round_coordinates(value: Any, decimals: int = DECIMALS) -> Any:
    """Round every coordinate of a GeoJSON ``coordinates`` array."""
    if isinstance(value, (list, tuple)):
        if value and isinstance(value[0], (int, float)):
            return [round(float(number), decimals) for number in value]
        return [round_coordinates(item, decimals) for item in value]
    return value


def simplify(geometry: BaseGeometry, tolerance_deg: float) -> BaseGeometry:
    """Simplify a (Multi)Polygon, keeping its topology and dropping parts smaller than the tolerance squared."""
    simplified = geometry.simplify(tolerance_deg, preserve_topology=True)
    if isinstance(simplified, MultiPolygon):
        floor = tolerance_deg * tolerance_deg
        parts = [part for part in simplified.geoms if part.area >= floor]
        simplified = MultiPolygon(parts) if parts else simplified
    return simplified


def feature(geometry: BaseGeometry, properties: dict[str, Any]) -> dict[str, Any]:
    """A GeoJSON feature with rounded coordinates."""
    geo = mapping(geometry)
    return {
        "type": "Feature",
        "properties": properties,
        "geometry": {"type": geo["type"], "coordinates": round_coordinates(geo["coordinates"])},
    }


def write_collection(path: Path, features: list[dict[str, Any]], note: str, extra: Optional[dict[str, Any]] = None) -> None:
    """Write a gzipped FeatureCollection (compact JSON) and say how big it is.

    ``extra`` adds members to the collection (RFC 7946 allows foreign members), e.g. the licence of the data.
    """
    collection = {"type": "FeatureCollection", "note": note, **(extra or {}), "features": features}
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(collection, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    # No time and no file name in the gzip header: the same input gives the same bytes, whatever the name of the file.
    with open(path, "wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=9, mtime=0) as handle:
        handle.write(payload)
    print(f"wrote {path.relative_to(REPO_ROOT) if path.is_relative_to(REPO_ROOT) else path}: "
          f"{len(features)} features, {path.stat().st_size / 1024:.0f} KiB gzipped ({len(payload) / 1024:.0f} KiB raw)")


def country_code(properties: dict[str, Any]) -> Optional[str]:
    """The ISO 3166-1 alpha-2 code of a Natural Earth country (``ISO_A2_EH`` fixes France and Norway, which have -99)."""
    for key in ("ISO_A2_EH", "ISO_A2", "WB_A2"):
        value = properties.get(key)
        if isinstance(value, str) and re.fullmatch(r"[A-Z]{2}", value):
            return value
    return None


def country_name(properties: dict[str, Any]) -> str:
    """The English name of a Natural Earth country: ``NAME_EN`` (spelled out), else the abbreviated ``NAME``."""
    return str(properties.get("NAME_EN") or properties.get("NAME") or properties.get("ADMIN") or "")


def polygons_of(geometry: BaseGeometry) -> list[Polygon]:
    """The polygons of a (Multi)Polygon."""
    return list(geometry.geoms) if hasattr(geometry, "geoms") else [geometry]


def in_box(polygon: Polygon, box: tuple[float, float, float, float]) -> bool:
    """Whether the centroid of a polygon lies in ``(min lon, min lat, max lon, max lat)``."""
    min_lon, min_lat, max_lon, max_lat = box
    return min_lon <= polygon.centroid.x <= max_lon and min_lat <= polygon.centroid.y <= max_lat


def build_countries(source: Path) -> list[dict[str, Any]]:
    """The borders of every country of Natural Earth, by ISO 3166-1 alpha-2 code, sorted by code.

    A country that Natural Earth draws in several features (Australia and its islands, France and Clipperton...) is merged into one;
    ``MERGED_INTO`` joins the places with no code to the country they lie in; ``COUNTRY_EXTENT`` and ``SPLIT_PARTS`` cut the
    overseas departments off France. The places left with no code are named on the standard error.
    """
    data = json.loads(source.read_text(encoding="utf-8"))
    parts: dict[str, list[BaseGeometry]] = {}
    names: dict[str, tuple[float, str]] = {}  # the name of the largest feature that holds the code
    left_out: list[str] = []
    for item in data["features"]:
        props = item.get("properties") or {}
        if not item.get("geometry"):
            continue
        geometry = shape(item["geometry"])
        code = country_code(props) or MERGED_INTO.get(str(props.get("ADM0_A3")))
        if code is None:
            left_out.append(str(props.get("NAME") or props.get("ADMIN") or props.get("ADM0_A3")))
            continue
        parts.setdefault(code, []).append(geometry)
        if country_code(props) is not None and geometry.area >= names.get(code, (-1.0, ""))[0]:
            names[code] = (geometry.area, country_name(props))
    if left_out:
        print(f"places with no ISO code, not written: {', '.join(sorted(left_out))}", file=sys.stderr)
    cut_off: dict[str, list[Polygon]] = {}
    features = []
    for code in sorted(parts):
        polygons = [polygon for part in parts[code] for polygon in polygons_of(part)]
        extent = COUNTRY_EXTENT.get(code)
        if extent is not None:
            for part_code, (_, box) in SPLIT_PARTS.get(code, {}).items():
                cut_off.setdefault(part_code, []).extend(polygon for polygon in polygons if in_box(polygon, box))
            polygons = [polygon for polygon in polygons if in_box(polygon, extent)]
        merged = polygons[0] if len(polygons) == 1 else MultiPolygon(polygons)
        features.append(feature(simplify(merged, COUNTRY_TOLERANCE_DEG), {"country": code, "name": names.get(code, (0.0, code))[1]}))
    for code, (name, _) in (item for split in SPLIT_PARTS.values() for item in split.items()):
        polygons = cut_off.get(code, [])
        if not polygons:
            raise SystemExit(f"no polygon of {name} ({code}) found in Natural Earth")
        merged = polygons[0] if len(polygons) == 1 else MultiPolygon(polygons)
        features.append(feature(simplify(merged, COUNTRY_TOLERANCE_DEG), {"country": code, "name": name}))
    return sorted(features, key=lambda item: item["properties"]["country"])


def fetch_nuts_codes(cache: Path, fetch: Callable[..., Path] = download) -> list[tuple[str, str]]:
    """``(ISO 3166-2 code, NUTS code)`` pairs from Wikidata for the countries of ``NUTS_LEVELS``."""
    path = cache / "wikidata_nuts.json"
    if not path.exists():
        query = urllib.parse.urlencode({"query": NUTS_QUERY, "format": "json"})
        fetch(f"{WIKIDATA_SPARQL_URL}?{query}", path)
    rows = json.loads(path.read_text(encoding="utf-8"))["results"]["bindings"]
    return [(row["iso"]["value"], row["nuts"]["value"]) for row in rows]


def normalize_iso(iso: str) -> str:
    """``FR-75C`` (the Paris collectivity in Wikidata) -> ``FR-75``, the code Natural Earth uses."""
    return re.sub(r"^(FR-\d{2})C$", r"\1", iso)


def build_nuts(admin1: Path, pairs: Iterable[tuple[str, str]]) -> list[dict[str, Any]]:
    """The NUTS shapes: one feature per NUTS code, the geometry of the area its ISO 3166-2 code names."""
    data = json.loads(admin1.read_text(encoding="utf-8"))
    by_iso: dict[str, dict[str, Any]] = {}
    for item in data["features"]:
        props = item.get("properties") or {}
        iso = props.get("iso_3166_2")
        if isinstance(iso, str) and item.get("geometry") and props.get("adm0_a3") in ("FRA", "BEL"):
            by_iso[iso] = item
    features: list[dict[str, Any]] = []
    seen: set[str] = set()
    for iso_raw, nuts in sorted(pairs, key=lambda pair: (pair[1], pair[0])):
        country = nuts[:2]
        level = NUTS_LEVELS.get(country)
        iso = normalize_iso(iso_raw)
        if level is None or not level[1].fullmatch(nuts) or nuts in seen or iso not in by_iso:
            continue
        item = by_iso[iso]
        geometry = simplify(shape(item["geometry"]), NUTS_TOLERANCE_DEG)
        features.append(
            feature(geometry, {"scheme": level[0], "code": nuts, "name": str(item["properties"]["name"]), "country": country})
        )
        seen.add(nuts)
    return features


def build_emma(source: Path) -> list[dict[str, Any]]:
    """The EMMA_ID shapes of the official MeteoAlarm geocodes file, simplified."""
    data = json.loads(source.read_text(encoding="utf-8"))
    features = []
    for item in data["features"]:
        props = item.get("properties") or {}
        if props.get("type") != "EMMA_ID" or not item.get("geometry") or not props.get("code"):
            continue
        features.append(
            feature(
                simplify(shape(item["geometry"]), EMMA_TOLERANCE_DEG),
                {"scheme": "EMMA_ID", "code": str(props["code"]), "name": str(props.get("name") or ""), "country": str(props.get("country") or "")},
            )
        )
    return features


def run(argv: Optional[list[str]] = None, fetch: Callable[..., Path] = download) -> int:
    parser = argparse.ArgumentParser(description="Rebuild the geometry files the vigilances are localized with.")
    parser.add_argument("--cache-dir", type=Path, default=Path(tempfile.gettempdir()) / "vigie-geometries")
    parser.add_argument("--out-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--emma", action="store_true", help="build the OPTIONAL local EMMA_ID file instead (not bundled)")
    parser.add_argument("--countries-only", action="store_true", help="rebuild only the borders (countries.geojson.gz)")
    args = parser.parse_args(argv)
    cache: Path = args.cache_dir

    if args.emma:
        source = fetch(EMMA_URL, cache / "meteoalarm_geocodes.json")
        out = LOCAL_DIR if args.out_dir == DATA_DIR else args.out_dir
        write_collection(
            out / "emma_id.geojson.gz",
            build_emma(source),
            "MeteoAlarm EMMA_ID shapes, built locally from the official geocodes file; not redistributable here",
        )
        return 0

    countries = build_countries(fetch(NE_COUNTRIES_URL, cache / "ne_10m_admin_0_countries.geojson"))
    write_collection(
        args.out_dir / "countries.geojson.gz",
        countries,
        "Borders of every country of Natural Earth (public domain), simplified; to find the country of a point",
    )
    if args.countries_only:
        return 0
    admin1 = fetch(NE_ADMIN1_URL, cache / "ne_10m_admin_1_states_provinces.geojson")
    nuts = build_nuts(admin1, fetch_nuts_codes(cache, fetch))
    write_collection(
        args.out_dir / "geocodes-nuts.geojson.gz",
        nuts,
        "NUTS regions: shapes from Natural Earth (public domain), codes from Wikidata (CC0), simplified",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
