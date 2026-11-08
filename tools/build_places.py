"""Build the catalogue of the places of the French coast (``app/data/sea-places.json.gz``) from open data.

The sea pages of Vigie answer "can I go in the water here, for my activity?" for a *place*. The 51 surf spots were the only places;
this catalogue adds the places a swimmer, a diver or a boater goes to: ports, slipways, anchorages, dive sites and bathing sites.
Nothing is read from the Internet by the API: this script downloads the sources once, when it is run, and writes the file the API
reads (``GET /v1/sea/places``)::

    python tools/build_places.py --cache-dir DIR     # download into DIR (kept for the next run), write app/data/sea-places.json.gz
    python tools/build_places.py --cache-dir DIR --offline   # build from what DIR already holds, download nothing

Sources (the licences and the credits to give are in ``app/data/sea-places.NOTICE.md`` and ``app/attribution.py``):

- **ports**: the Sandre "Ports - Espace maritime français" (Licence Ouverte), the ports with a pleasure-boat activity; the
  ``leisure=marina`` of OpenStreetMap (ODbL 1.0) that are not within ``PORT_MERGE_KM`` of one of those ports are added;
- **slipways** (``leisure=slipway``), **anchorages** (``seamark:type=anchorage``) and **declared moorings**
  (``seamark:type=mooring`` with ``mooring=declaration``), **dive sites** (``scuba_diving:divespot=yes``): OpenStreetMap;
- **bathing sites**: "Qualité des sites de baignade" (ministry in charge of health, via data.gouv.fr, Licence Ouverte 2.0), the
  coastal sites of metropolitan France, with the class of the water over the four years 2021 to 2024.

What makes a place of the catalogue: it is in metropolitan France (``METRO_BBOX``) and **within ``COAST_KM`` of the OpenStreetMap
coastline** (``natural=coastline``), so a lake, a river or a quarry is not a sea place. A slipway marked private, closed, for
customers or for destination only is dropped. Two places of the same kind closer than a few hundred metres are one (the
first of the sources wins: the Sandre before OpenStreetMap, a named element before an unnamed one), and so are two ports of
the same name closer than ``SAME_NAME_KM``. An OpenStreetMap marina whose name says it is a school, a beach or a mooring is not
a port. Of the localities that name the places, the "arrondissements" of the big cities are left out (``16e Arrondissement``
names nothing by itself).

What is **not** in the file, because no open source gives it: the depth of a port, the fee of a slipway, the state of a dive
site, the water of today. A place has **no forecast** of its own here; ``near`` is the nearest named locality of OpenStreetMap
(within ``LOCALITY_KM``) and is what an unnamed place is called after.

The code stays MIT; the file is a database made from OpenStreetMap, **ODbL 1.0**, which is why it carries its own notice.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import math
import re
import sys
import zipfile
from itertools import pairwise
from pathlib import Path
from typing import Any, Iterable, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from tools.build_geometries import download  # noqa: E402
from tools.build_spots import FRANCE, distance_km, osm_ref, overpass, position, slug  # noqa: E402

OUTPUT = REPO_ROOT / "app" / "data" / "sea-places.json.gz"

# The files read, pinned: the file the catalogue was built from and the SHA-256 of its bytes. A source that changes under the same
# address is reported (the build goes on: it says so, and the NOTICE must then be rewritten with the new date).
QSB_URL = (
    "https://static.data.gouv.fr/resources/qualite-des-sites-de-baignade-1/20260709-081710/qualite-sites-baignade.csv"
)
QSB_SHA256 = "ecd3f920634b0d9e3631888f1a4cd2548aac44c8a17001705357d6203a313494"
PORTS_URL = "https://services.sandre.eaufrance.fr/telechargement/geo/PTS/PortMaritime_FRA/Port_Maritime_FRA.geojson.zip"
PORTS_SHA256 = "e6dfa72fa5abf5931b0c99136ef305421596c400123ee17d4d2051be154b6820"

# South, west, north, east: metropolitan France with Corsica (the overseas collectivities are not in the catalogue).
METRO_BBOX = (41.0, -5.6, 51.3, 9.8)
COAST_KM = 2.0
PORT_MERGE_KM = 1.5
SAME_NAME_KM = 1.0  # two ports of the same name closer than this are one
OSM_PORT_MERGE_KM = 0.6
SLIPWAY_MERGE_KM = 0.15
ANCHORAGE_MERGE_KM = 0.3
DIVE_MERGE_KM = 0.3
LOCALITY_KM = 6.0
DENSIFY_KM = (
    0.4  # the coastline is cut into steps of at most this length, so a point near a long segment is still found
)

TYPES = ("port", "slipway", "anchorage", "dive", "bathing")
PORT_USES = {
    "Plaisance": "leisure",
    "Pêche": "fishing",
    "Transport de passagers": "passengers",
    "Commerce": "trade",
    "Terminal d’hydrocarbures": "oil",
    "Militaire": "military",
}
QUALITY = {
    "Excellent": "excellent",
    "Bon": "good",
    "Suffisant": "sufficient",
    "Insuffisant": "poor",
    "Non classé": "unclassified",
}
# A slipway the public cannot use.
CLOSED_ACCESS = {"private", "no", "customers", "destination"}
# A mooring of an authorised zone (declared, or open to all); the ones tagged private, or without a word, are not listed.
OPEN_MOORING = {"declaration", "yes", "authorization", "authorisation", "permit"}
PLACE_CLASSES = "city|town|village|suburb|hamlet"
# A marina of OpenStreetMap that is a school, a beach or a mooring by the first words of its name is not a port ("Port de Valras-Plage" is); a locality "16e Arrondissement" names no
# place by itself (every big city has one).
NOT_A_PORT = re.compile(
    r"(?i)^(école|ecole|centre nautique|base nautique|espace nautique|(la |le |l')?plage|mouillage)\b"
)
NOT_A_LOCALITY = re.compile(r"(?i)arrondissement")

LOWER_WORDS = {
    "de",
    "du",
    "des",
    "la",
    "le",
    "les",
    "sur",
    "sous",
    "sans",
    "en",
    "et",
    "aux",
    "au",
    "à",
    "d",
    "l",
    "lès",
    "lez",
    "ès",
}


def overpass_queries() -> dict[str, str]:
    """The questions asked to Overpass, by name (their answers are kept in the cache directory by the hash of the question)."""

    def q(body: str, output: str = " center tags", timeout: int = 300) -> str:
        return f"[out:json][timeout:{timeout}];{FRANCE}{body}out{output};"

    return {
        "marina": q('(nwr["leisure"="marina"](area.fr););'),
        "anchorage": q('(nwr["seamark:type"="anchorage"](area.fr);nwr["seamark:type"="mooring"]["name"](area.fr););'),
        "divespot": q(
            '(nwr["scuba_diving:divespot"="yes"](area.fr);nwr["sport"="scuba_diving"]["natural"](area.fr););'
        ),
        "slipway": q('(nwr["leisure"="slipway"](area.fr););'),
        "places": q(f'(node["place"~"^({PLACE_CLASSES})$"]["name"](area.fr););', output=""),
        "coast": q('(way["natural"="coastline"](area.fr););', output=" geom", timeout=600),
    }


# --- geometry -----------------------------------------------------------------------------------------------------------------


class Grid:
    """Points in square cells of ``cell`` degrees: the nearest point within a few kilometres without comparing them all."""

    def __init__(self, cell: float = 0.02) -> None:
        self.cell = cell
        self.cells: dict[tuple[int, int], list[tuple[float, float, Any]]] = {}

    def key(self, lat: float, lon: float) -> tuple[int, int]:
        return (math.floor(lat / self.cell), math.floor(lon / self.cell))

    def add(self, lat: float, lon: float, payload: Any = None) -> None:
        self.cells.setdefault(self.key(lat, lon), []).append((lat, lon, payload))

    def nearest(self, at: tuple[float, float], max_km: float) -> Optional[tuple[float, Any]]:
        """``(distance_km, payload)`` of the nearest point within ``max_km``, or ``None``."""
        span_lat = math.ceil(max_km / (self.cell * 111.195))
        span_lon = math.ceil(max_km / (self.cell * 111.195 * max(math.cos(math.radians(at[0])), 0.2)))
        ci, cj = self.key(*at)
        best: Optional[tuple[float, Any]] = None
        for i in range(ci - span_lat, ci + span_lat + 1):
            for j in range(cj - span_lon, cj + span_lon + 1):
                for lat, lon, payload in self.cells.get((i, j), ()):
                    d = distance_km(at, (lat, lon))
                    if d <= max_km and (best is None or d < best[0]):
                        best = (d, payload)
        return best


def coastline_grid(ways: Iterable[dict[str, Any]]) -> Grid:
    """The OpenStreetMap coastline, each segment cut into steps of at most ``DENSIFY_KM``."""
    grid = Grid()
    for way in ways:
        points = [(float(p["lat"]), float(p["lon"])) for p in way.get("geometry", ())]
        for a, b in pairwise(points):
            if not in_metropolitan_france(a) and not in_metropolitan_france(b):
                continue  # the answer holds the overseas coasts too: they would only take memory
            steps = max(1, math.ceil(distance_km(a, b) / DENSIFY_KM))
            for s in range(steps):
                t = s / steps
                grid.add(a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
        if points and in_metropolitan_france(points[-1]):
            grid.add(*points[-1])
    return grid


def in_metropolitan_france(at: tuple[float, float]) -> bool:
    south, west, north, east = METRO_BBOX
    return south <= at[0] <= north and west <= at[1] <= east


def merge_close(items: list[dict[str, Any]], within_km: float) -> list[dict[str, Any]]:
    """``items`` in order of preference; one that is within ``within_km`` of an earlier one is dropped."""
    kept: list[dict[str, Any]] = []
    grid = Grid()
    for item in items:
        if grid.nearest(item["at"], within_km) is None:
            kept.append(item)
            grid.add(item["at"][0], item["at"][1])
    return kept


# --- names --------------------------------------------------------------------------------------------------------------------


def clean(text: Optional[str]) -> Optional[str]:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text or None


def tidy_name(name: str) -> str:
    """``PLAGE DU MOULLEAU`` -> ``Plage du Moulleau``; a name that already has lower-case letters is left as it is."""
    if any(c.islower() for c in name):
        return name

    def word(match: re.Match[str]) -> str:
        w = match.group(0).lower()
        return w if w in LOWER_WORDS and match.start() > 0 else w[:1].upper() + w[1:]

    return re.sub(r"[^\W\d_]+", word, name)


def sandre_name(name: str) -> str:
    """``port du Chenal Vieux`` -> ``Port du Chenal Vieux``."""
    name = clean(name) or ""
    return name[:1].upper() + name[1:]


# --- sources ------------------------------------------------------------------------------------------------------------------


def read_ports(geojson: dict[str, Any]) -> list[dict[str, Any]]:
    """The Sandre ports that have a pleasure-boat activity, in metropolitan France."""
    found = []
    for feature in geojson["features"]:
        props = feature["properties"]
        lon, lat = feature["geometry"]["coordinates"][:2]
        uses = [PORT_USES[v] for i in range(1, 7) if (v := props.get(f"MnActivitePortuaire_{i}")) in PORT_USES]
        if "leisure" not in uses or not in_metropolitan_france((lat, lon)):
            continue
        found.append({
            "type": "port", "name": sandre_name(props["NomPort"]), "at": (lat, lon), "source": "sandre",
            "ref": str(props["CdPort"]), "uses": sorted(uses),
        })  # fmt: skip
    return sorted(found, key=lambda p: p["ref"])


def read_bathing(rows: Iterable[dict[str, str]]) -> list[dict[str, Any]]:
    """The coastal bathing sites of metropolitan France with the class of their water."""
    found = []
    for row in rows:
        if row["type_eau_norm"] != "COTIERE" or row["departement"].startswith("97") or row["qualite"] not in QUALITY:
            continue
        at = (float(row["latitude"]), float(row["longitude"]))
        if not in_metropolitan_france(at):
            continue
        found.append({
            "type": "bathing", "name": tidy_name(clean(row["nom_site"]) or ""), "at": at, "source": "qsb",
            "ref": f"{row['code_geographique']}/{row['index']}", "commune": clean(row["libelle_geographique"]),
            "quality": QUALITY[row["qualite"]],
        })  # fmt: skip
    return sorted(found, key=lambda p: p["ref"])


def osm_items(elements: Iterable[dict[str, Any]], kind: str, keep: Any) -> list[dict[str, Any]]:
    """The OpenStreetMap elements of ``kind`` that ``keep(tags)`` accepts, named when they have a name; a named one first."""
    found = []
    for element in elements:
        tags = element.get("tags", {})
        at = position(element)
        if at is None or not in_metropolitan_france(at) or not keep(tags):
            continue
        found.append({
            "type": kind, "name": clean(tags.get("name")), "at": at, "source": "osm", "ref": osm_ref(element),
        })  # fmt: skip
    return sorted(found, key=lambda p: (p["name"] is None, p["ref"]))


def keep_slipway(tags: dict[str, str]) -> bool:
    return tags.get("access") not in CLOSED_ACCESS and tags.get("boat") != "no"


def keep_anchorage(tags: dict[str, str]) -> bool:
    if tags.get("seamark:type") == "anchorage":
        return tags.get("mooring") != "private"
    return tags.get("seamark:type") == "mooring" and tags.get("mooring") in OPEN_MOORING and bool(tags.get("name"))


def keep_dive(tags: dict[str, str]) -> bool:
    return bool(tags.get("name"))


def keep_marina(tags: dict[str, str]) -> bool:
    return tags.get("leisure") == "marina" and bool(tags.get("name")) and not NOT_A_PORT.search(tags["name"])


def merge_same_name(items: list[dict[str, Any]], within_km: float) -> list[dict[str, Any]]:
    """``items`` in order of preference; one with the name of an earlier one, within ``within_km`` of it, is dropped."""
    kept: list[dict[str, Any]] = []
    seen: dict[str, list[tuple[float, float]]] = {}
    for item in items:
        key = slug(item["name"] or "")
        if key and any(distance_km(item["at"], at) < within_km for at in seen.get(key, ())):
            continue
        kept.append(item)
        seen.setdefault(key, []).append(item["at"])
    return kept


# --- the catalogue ------------------------------------------------------------------------------------------------------------


def place_id(item: dict[str, Any]) -> str:
    label = slug(item["name"] or item.get("near") or item.get("commune") or "") or "unnamed"
    source_ref = item["source"] + ":" + item["ref"]
    return f"{item['type']}-{label}-{hashlib.sha1(source_ref.encode()).hexdigest()[:4]}"


def build(
    ports: list[dict[str, Any]],
    bathing: list[dict[str, Any]],
    osm: dict[str, list[dict[str, Any]]],
    coast: Grid,
    localities: Grid,
) -> list[dict[str, Any]]:
    """The places of the catalogue, from the parsed sources (``osm``: the elements by question name, see ``overpass_queries``)."""

    def coastal(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [i for i in items if coast.nearest(i["at"], COAST_KM) is not None]

    sandre = coastal(ports)
    marinas = merge_close(coastal(osm_items(osm["marina"], "port", keep_marina)), OSM_PORT_MERGE_KM)
    sandre_grid = Grid()
    for p in sandre:
        sandre_grid.add(p["at"][0], p["at"][1])
    marinas = [m for m in marinas if sandre_grid.nearest(m["at"], PORT_MERGE_KM) is None]
    slipways = merge_close(coastal(osm_items(osm["slipway"], "slipway", keep_slipway)), SLIPWAY_MERGE_KM)
    anchorages = merge_close(coastal(osm_items(osm["anchorage"], "anchorage", keep_anchorage)), ANCHORAGE_MERGE_KM)
    dives = merge_close(coastal(osm_items(osm["divespot"], "dive", keep_dive)), DIVE_MERGE_KM)
    everything = merge_same_name(sandre + marinas, SAME_NAME_KM) + slipways + anchorages + dives + bathing

    places = []
    for item in everything:
        near = localities.nearest(item["at"], LOCALITY_KM)
        item["near"] = near[1] if near else None
        places.append(item)
    ids: dict[str, dict[str, Any]] = {}
    for item in places:
        item["id"] = place_id(item)
        if item["id"] in ids:
            raise ValueError(
                f"two places with the id {item['id']}: {item['source']}:{item['ref']} and {ids[item['id']]['ref']}"
            )
        ids[item["id"]] = item
    return sorted(places, key=lambda p: (TYPES.index(p["type"]), slug(p["name"] or p["near"] or ""), p["id"]))


def record(item: dict[str, Any]) -> dict[str, Any]:
    """The entry written in the file (the keys that do not apply to the kind of place are left out)."""
    out: dict[str, Any] = {
        "id": item["id"], "type": item["type"], "name": item["name"], "near": item["near"],
        "lat": round(item["at"][0], 4), "lon": round(item["at"][1], 4),
    }  # fmt: skip
    for key in ("commune", "uses", "quality"):
        if item.get(key):
            out[key] = item[key]
    out["source"], out["ref"] = item["source"], item["ref"]
    return out


def write(path: Path, places: list[dict[str, Any]], osm_data_utc: str) -> None:
    counts = {t: sum(1 for p in places if p["type"] == t) for t in TYPES}
    data = {"osm_data_utc": osm_data_utc, "count": len(places), "counts": counts, "places": [record(p) for p in places]}
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    # No time and no file name in the gzip header: the same input gives the same bytes.
    with (
        open(path, "wb") as raw,
        gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=9, mtime=0) as handle,
    ):
        handle.write(payload)
    print(f"wrote {path}: {len(places)} places {counts}, {path.stat().st_size / 1024:.0f} KiB gzipped")


def check_pinned(path: Path, expected: str) -> None:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != expected:
        print(
            f"warning: {path.name} is not the file the catalogue was built from (sha256 {digest}); update the NOTICE",
            file=sys.stderr,
        )


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument(
        "--cache-dir", type=Path, required=True, help="where the downloads and the Overpass answers are kept"
    )
    parser.add_argument(
        "--offline", action="store_true", help="download nothing: the cache directory must hold every source"
    )
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args(argv)
    cache: Path = args.cache_dir

    if args.offline:
        qsb_path, ports_path = cache / "qsb.csv", cache / "ports.zip"
        if not qsb_path.exists() or not ports_path.exists():
            parser.error("--offline: qsb.csv and ports.zip must be in the cache directory")
    else:
        qsb_path = download(QSB_URL, cache / "qsb.csv")
        ports_path = download(PORTS_URL, cache / "ports.zip")
    check_pinned(qsb_path, QSB_SHA256)
    check_pinned(ports_path, PORTS_SHA256)

    with zipfile.ZipFile(ports_path) as archive:
        name = next(n for n in archive.namelist() if n.endswith(".geojson"))
        ports = read_ports(json.loads(archive.read(name).decode("utf-8")))
    with open(qsb_path, encoding="utf-8", newline="") as handle:
        bathing = read_bathing(csv.DictReader(io.StringIO(handle.read()), delimiter=";"))

    raw = {name: overpass(query, cache) for name, query in overpass_queries().items()}
    answers = {name: answer["elements"] for name, answer in raw.items()}
    # The date of the oldest OpenStreetMap data any answer was made of: the catalogue is not fresher than that.
    osm_data_utc = min(a["osm3s"]["timestamp_osm_base"] for a in raw.values())
    localities = Grid()
    for node in answers["places"]:
        name = node.get("tags", {}).get("name")
        if (
            name
            and not NOT_A_LOCALITY.search(name)
            and "lat" in node
            and in_metropolitan_france((node["lat"], node["lon"]))
        ):
            localities.add(float(node["lat"]), float(node["lon"]), name)
    places = build(ports, bathing, answers, coastline_grid(answers["coast"]), localities)
    write(args.output, places, osm_data_utc)


if __name__ == "__main__":
    main()
