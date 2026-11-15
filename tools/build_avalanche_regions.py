"""Build the bundled EAWS Euregio micro-regions dataset for offline point-in-polygon matching.

Downloads the official EAWS micro-region GeoJSON files for Tyrol (`AT-07`),
South Tyrol (`IT-32-BZ`), and Trentino (`IT-32-TN`) from `gitlab.com/eaws/eaws-regions`
(licensed CC0 1.0 Universal), keeps currently valid micro-regions (`valid_until` unset),
simplifies geometries while preserving topology, rounds coordinates to 3 decimal places
(~110 m), and writes `app/data/eaws-euregio-regions.geojson.gz`.

Usage:
    python tools/build_avalanche_regions.py
"""
from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path
from typing import Any

import httpx
from shapely.geometry import mapping, shape

EAWS_BASE_URL = "https://gitlab.com/eaws/eaws-regions/-/raw/master/public/micro-regions"
REGION_FILES = (
    "AT-07_micro-regions.geojson.json",
    "IT-32-BZ_micro-regions.geojson.json",
    "IT-32-TN_micro-regions.geojson.json",
)
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "app" / "data" / "eaws-euregio-regions.geojson.gz"
SIMPLIFY_TOLERANCE_DEG = 0.003
COORD_DECIMALS = 3


def _round_coords(obj: Any, decimals: int = COORD_DECIMALS) -> Any:
    if isinstance(obj, (list, tuple)):
        if obj and isinstance(obj[0], (int, float)):
            return [round(float(obj[0]), decimals), round(float(obj[1]), decimals)]
        return [_round_coords(item, decimals) for item in obj]
    return obj


def build_euregio_regions(output_path: Path = OUTPUT_PATH) -> dict[str, Any]:
    """Download, filter, simplify, and write the Euregio micro-region GeoJSON archive."""
    features: list[dict[str, Any]] = []
    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        for filename in REGION_FILES:
            url = f"{EAWS_BASE_URL}/{filename}"
            resp = client.get(url)
            resp.raise_for_status()
            payload = resp.json()
            for raw in payload.get("features") or []:
                props = raw.get("properties") or {}
                # EAWS stores historical boundaries with a ``valid_until`` date;
                # only regions without ``valid_until`` are currently active.
                if props.get("valid_until"):
                    continue
                region_id = props.get("id")
                geom_raw = raw.get("geometry")
                if not region_id or not geom_raw:
                    continue
                geom = shape(geom_raw).simplify(SIMPLIFY_TOLERANCE_DEG, preserve_topology=True)
                if geom.is_empty:
                    continue
                geom_dict = mapping(geom)
                geom_dict["coordinates"] = _round_coords(geom_dict["coordinates"])
                features.append(
                    {
                        "type": "Feature",
                        "properties": {
                            "id": str(region_id),
                            "name": props.get("name") or props.get("name_en") or str(region_id),
                            "name_en": props.get("name_en"),
                            "valid_from": props.get("valid_from"),
                        },
                        "geometry": geom_dict,
                    }
                )

    collection = {
        "type": "FeatureCollection",
        "license": "CC0-1.0",
        "source": "https://gitlab.com/eaws/eaws-regions",
        "note": "EAWS Euregio avalanche micro-regions (AT-07, IT-32-BZ, IT-32-TN), built by tools/build_avalanche_regions.py",
        "features": features,
    }
    encoded = json.dumps(collection, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # Use mtime=0 so the bundled gzip header stays deterministic (checked by test_build_geometries.py).
    output_path.write_bytes(gzip.compress(encoded, compresslevel=9, mtime=0))
    return {"features": len(features), "bytes": output_path.stat().st_size, "path": str(output_path)}


if __name__ == "__main__":
    summary = build_euregio_regions()
    print(f"Wrote {summary['features']} Euregio micro-regions ({summary['bytes']} bytes) to {summary['path']}")
    sys.exit(0)
