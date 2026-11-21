"""Rebuild ``app/data/geocodes-meteofrance-om.geojson.gz``: the outline of each overseas territory Météo-France publishes a vigilance for.

A warning of ``DPVigilance`` for an overseas territory names its area by the territory (``MF_OM`` code, ``app/collectors/
meteofrance_overseas_vigilance.py``), and Météo-France does not publish the shapes of the sub-zones it cuts a territory in (nothing read
in the portal does, ADR 0036). A point is therefore matched to the **territory**: its outline is the border of the country of
``countries.geojson.gz`` (Natural Earth, public domain, already simplified to 0.01 degree) widened by ``SEA_MARGIN_DEG`` (0.25 degree,
about 25 km, the margin by which ``countries_for_point`` gives a point at sea to the nearest coast), because the warnings of these
territories name the sea around them (waves, submersion, cyclone). No input but the file of the repository: nothing is downloaded::

    python tools/build_overseas_geocodes.py
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

from shapely.geometry import mapping, shape
from shapely.ops import unary_union

DATA = Path(__file__).resolve().parents[1] / "app" / "data"
SEA_MARGIN_DEG = 0.25
SIMPLIFY_DEG = 0.01
NOTE = "Outline of each French overseas territory Météo-France publishes a vigilance for: the border of its country (Natural Earth, public domain) widened by 0.25 degree. Built by tools/build_overseas_geocodes.py."
# ``MF_OM`` code -> the country (ISO 3166-1 alpha-2) of ``countries.geojson.gz`` whose outline it takes.
TERRITORIES = {
    "971": "GP",
    "972": "MQ",
    "973": "GF",
    "974": "RE",
    "975": "PM",
    "976": "YT",
    "977": "BL",
    "978": "MF",
    "987": "PF",
    "988": "NC",
}


def round_coordinates(value: object) -> object:
    if isinstance(value, float):
        return round(value, 3)
    if isinstance(value, (list, tuple)):
        return [round_coordinates(item) for item in value]
    return value


def main() -> None:
    with gzip.open(DATA / "countries.geojson.gz", "rt", encoding="utf-8") as handle:
        countries = {item["properties"]["country"]: shape(item["geometry"]) for item in json.load(handle)["features"]}
    features = []
    for code, country in TERRITORIES.items():
        outline = unary_union([countries[country].buffer(SEA_MARGIN_DEG)]).simplify(
            SIMPLIFY_DEG, preserve_topology=True
        )
        geometry = mapping(outline)
        features.append(
            {
                "type": "Feature",
                "properties": {"scheme": "MF_OM", "code": code, "name": country},
                "geometry": {"type": geometry["type"], "coordinates": round_coordinates(geometry["coordinates"])},
            }
        )
    payload = json.dumps({"type": "FeatureCollection", "note": NOTE, "features": features}, separators=(",", ":"), ensure_ascii=False)
    target = DATA / "geocodes-meteofrance-om.geojson.gz"
    with gzip.GzipFile(target, "wb", mtime=0) as raw:
        raw.write(payload.encode("utf-8"))
    print(f"{target.name}: {len(features)} outlines, {target.stat().st_size} bytes")


if __name__ == "__main__":
    main()
