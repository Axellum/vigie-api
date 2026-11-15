"""Build ``app/data/meteofrance-coastal-stations.json`` from Météo-France's station list (Licence Ouverte 2.0).

Reads ``POSTES_MF.csv`` (dataset "Postes" on data.gouv.fr, https://www.data.gouv.fr/datasets/66ebf2cb9dd6916802ae90a1,
``https://meteofrance.s3.sbg.io.cloud.ovh.net/data/synchro_ftp/BASE/POSTES/POSTES_MF.csv``) and keeps the open real-time
stations (``DATFERM`` empty and ``TYPE_POSTE_ACTUEL`` in ``0``, ``1`` or ``2``) within 35 km of at least one surf spot of
``app/data/surf-spots.json``.
"""

from __future__ import annotations

import csv
import io
import json
import math
import sys
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
SPOTS_FILE = REPO_ROOT / "app" / "data" / "surf-spots.json"
OUT_FILE = REPO_ROOT / "app" / "data" / "meteofrance-coastal-stations.json"
POSTES_URL = "https://meteofrance.s3.sbg.io.cloud.ovh.net/data/synchro_ftp/BASE/POSTES/POSTES_MF.csv"
MAX_SPOT_DISTANCE_KM = 35.0


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    rad = math.pi / 180.0
    dlat = (lat2 - lat1) * rad
    dlon = (lon2 - lon1) * rad
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1 * rad) * math.cos(lat2 * rad) * math.sin(dlon / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(a))


def main(csv_path: str | None = None) -> None:
    spots = json.loads(SPOTS_FILE.read_text(encoding="utf-8"))["spots"]
    if csv_path:
        text = Path(csv_path).read_text(encoding="utf-8")
    else:
        resp = httpx.get(POSTES_URL, timeout=30.0)
        resp.raise_for_status()
        text = resp.text

    reader = csv.DictReader(io.StringIO(text), delimiter=";")
    stations = []
    for row in reader:
        if (row.get("DATFERM") or "").strip():
            continue
        kind_raw = (row.get("TYPE_POSTE_ACTUEL") or "").strip()
        if kind_raw not in ("0", "1", "2"):
            continue
        try:
            lat = float(row["LAT"])
            lon = float(row["LON"])
            alt = int(float(row["ALTI"])) if (row.get("ALTI") or "").strip() else 0
        except (KeyError, ValueError):
            continue
        min_d = min(haversine_km(lat, lon, float(s["lat"]), float(s["lon"])) for s in spots)
        if min_d > MAX_SPOT_DISTANCE_KM:
            continue
        station_id = (row.get("NUM_POSTE") or "").strip().zfill(8)
        stations.append(
            {
                "id": station_id,
                "name": (row.get("NOM_USUEL") or "").strip(),
                "commune": (row.get("COMMUNE") or "").strip(),
                "lat": round(lat, 5),
                "lon": round(lon, 5),
                "alt_m": alt,
                "kind": int(kind_raw),
            }
        )
    stations.sort(key=lambda s: str(s["id"]))
    payload = {
        "source": "Météo-France, file 'POSTES_MF.csv' (dataset 66ebf2cb9dd6916802ae90a1 on data.gouv.fr)",
        "licence": "Licence Ouverte 2.0",
        "count": len(stations),
        "stations": stations,
    }
    OUT_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"{len(stations)} stations -> {OUT_FILE}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
