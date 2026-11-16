"""Build the bundled Météo-France BRA massifs dataset for offline point-in-polygon matching.

Reads the list of the 35 massifs of the avalanche-risk bulletins (BRA) from the Météo-France ``DPBRA`` API
(``GET /public/DPBRA/v1/liste-massifs``, a GeoJSON ``FeatureCollection`` of ``MultiPolygon`` published under Licence Ouverte 2.0
(Etalab)), keeps the code and the name of each massif, simplifies the shapes while preserving topology, rounds the coordinates to
3 decimal places (~110 m) and writes ``app/data/meteofrance-bra-massifs.geojson.gz``.

The API needs a key (``VIGIE_METEOFRANCE_API_KEY``, sent in the ``apikey`` header and never printed). The build is run by hand,
rarely (the massifs change about once in several years); the API itself never calls Météo-France to place a point.

Usage:
    python tools/build_bra_massifs.py [--from-file liste-massifs.json]
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx
from shapely.geometry import mapping, shape

MASSIFS_URL = "https://public-api.meteofrance.fr/public/DPBRA/v1/liste-massifs"
KEY_ENV = "VIGIE_METEOFRANCE_API_KEY"
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "app" / "data" / "meteofrance-bra-massifs.geojson.gz"
SIMPLIFY_TOLERANCE_DEG = 0.003
COORD_DECIMALS = 3


def _round_coords(obj: Any, decimals: int = COORD_DECIMALS) -> Any:
    if isinstance(obj, (list, tuple)):
        if obj and isinstance(obj[0], (int, float)):
            return [round(float(obj[0]), decimals), round(float(obj[1]), decimals)]
        return [_round_coords(item, decimals) for item in obj]
    return obj


def convert(payload: dict[str, Any]) -> dict[str, Any]:
    """The bundled collection built from the answer of ``liste-massifs`` (no network)."""
    if not isinstance(payload, dict) or not isinstance(payload.get("features"), list):
        raise ValueError("liste-massifs did not return a GeoJSON FeatureCollection")
    features: list[dict[str, Any]] = []
    for raw in payload["features"]:
        props = raw.get("properties") or {}
        code = props.get("code")
        raw_geom = raw.get("geometry")
        if not isinstance(code, int) or not raw_geom:
            continue
        geom = shape(raw_geom).simplify(SIMPLIFY_TOLERANCE_DEG, preserve_topology=True)
        if geom.is_empty:
            continue
        geom_dict = mapping(geom)
        geom_dict["coordinates"] = _round_coords(geom_dict["coordinates"])
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "code": code,
                    "name": props.get("title"),
                    "mountain": props.get("mountain"),
                    "department": props.get("Departement"),
                },
                "geometry": geom_dict,
            }
        )
    features.sort(key=lambda f: f["properties"]["code"])
    return {
        "type": "FeatureCollection",
        "license": "Licence Ouverte 2.0 (Etalab)",
        "source": "https://portail-api.meteofrance.fr/web/fr/api/DonneesPubliquesBRA",
        "note": "Météo-France BRA massifs (Alps, Corsica, Pyrenees), built by tools/build_bra_massifs.py",
        "features": features,
    }


def write_collection(path: Path, collection: dict[str, Any]) -> dict[str, Any]:
    encoded = json.dumps(collection, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    # mtime=0 keeps the gzip header deterministic (checked by test_build_geometries.py).
    path.write_bytes(gzip.compress(encoded, compresslevel=9, mtime=0))
    return {"features": len(collection["features"]), "bytes": path.stat().st_size, "path": str(path)}


def download(key: str) -> dict[str, Any]:
    with httpx.Client(timeout=30.0) as client:
        resp = client.get(MASSIFS_URL, headers={"apikey": key, "Accept": "application/geo+json"})
        resp.raise_for_status()
        return resp.json()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--from-file", type=Path, help="read a saved answer of liste-massifs instead of calling the API")
    args = parser.parse_args(argv)
    if args.from_file is not None:
        payload = json.loads(args.from_file.read_text(encoding="utf-8"))
    else:
        key = (os.environ.get(KEY_ENV) or "").strip()
        if not key:
            print(f"{KEY_ENV} is not set", file=sys.stderr)
            return 2
        payload = download(key)
    summary = write_collection(OUTPUT_PATH, convert(payload))
    print(f"Wrote {summary['features']} BRA massifs ({summary['bytes']} bytes) to {summary['path']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
