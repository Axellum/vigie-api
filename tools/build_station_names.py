"""Build ``app/data/meteofrance-station-names.json`` from Météo-France's station list (Licence Ouverte 2.0).

Reads ``POSTES_MF.csv`` (dataset "Postes" on data.gouv.fr, https://www.data.gouv.fr/datasets/66ebf2cb9dd6916802ae90a1,
``https://meteofrance.s3.sbg.io.cloud.ovh.net/data/synchro_ftp/BASE/POSTES/POSTES_MF.csv``) and keeps, for every open station
(``DATFERM`` empty, every ``TYPE_POSTE_ACTUEL``: some type 3 stations publish a 6-minute wind), its usual name and its altitude. The 6-minute
package (``DPPaquetObs``) gives a station's position but neither its name nor its altitude: the wind map
(``GET /v1/stations/wind``) reads them here. Run it again when stations open or close (a station missing here is still served,
without a name).
"""

from __future__ import annotations

import csv
import io
import json
import sys
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_FILE = REPO_ROOT / "app" / "data" / "meteofrance-station-names.json"
POSTES_URL = "https://meteofrance.s3.sbg.io.cloud.ovh.net/data/synchro_ftp/BASE/POSTES/POSTES_MF.csv"


def main(csv_path: str | None = None) -> None:
    if csv_path:
        text = Path(csv_path).read_text(encoding="utf-8")
    else:
        resp = httpx.get(POSTES_URL, timeout=30.0)
        resp.raise_for_status()
        text = resp.content.decode("utf-8")
    names: dict[str, list[object]] = {}
    for row in csv.DictReader(io.StringIO(text), delimiter=";"):
        if (row.get("DATFERM") or "").strip():
            continue
        station_id = (row.get("NUM_POSTE") or "").strip().zfill(8)
        name = (row.get("NOM_USUEL") or "").strip()
        if not station_id.strip("0") or not name:
            continue
        try:
            alt = int(float(row["ALTI"])) if (row.get("ALTI") or "").strip() else None
        except ValueError:
            alt = None
        names[station_id] = [name, alt]
    payload = {
        "source": "Météo-France, file 'POSTES_MF.csv' (dataset 66ebf2cb9dd6916802ae90a1 on data.gouv.fr)",
        "licence": "Licence Ouverte 2.0",
        "fields": ["name", "alt_m"],
        "count": len(names),
        "stations": dict(sorted(names.items())),
    }
    OUT_FILE.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8", newline="\n")
    print(f"{len(names)} stations -> {OUT_FILE}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
