"""Build ``app/data/candhis-stations.json`` from the CANDHIS stations file (Cerema, Licence Ouverte 2.0).

Nothing is read from the Internet by the API for this: download the GeoPackage once (data.gouv.fr, dataset "Stations de mesure de
Candhis", https://www.data.gouv.fr/datasets/674ee38cbede289cf234d829) into an empty directory and run::

    python -I tools/build_buoys.py PATH/candhis-houlographes-202412.gpkg

The file lists every wave buoy CANDHIS has had (the ones that no longer measure too): it does not say which still do. ``real_time`` is
true for the stations that the CANDHIS campaigns page (https://candhis.cerema.fr/_public_/campagne.php) marked [TR] when it was read
on 2026-11-08 (the list below), and ``null`` for the others: unknown, not false.
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "app" / "data" / "candhis-stations.json"
# Marked [TR] on the campaigns page, read on 2026-11-08 (the page may list more: that reading was partial).
REAL_TIME_SEEN = {
    "02911", "02922", "05602", "01704", "01705", "03302", "06402", "06403",
    "08505", "04403", "08002", "07609", "07610", "07611", "05903",
}  # fmt: skip
DMS = re.compile(r"""^(\d+)°(\d+(?:[.,]\d+)?)['’′]\s*([NSEW])$""")


def degrees(text: str) -> float:
    match = DMS.match(text.strip())
    if match is None:
        raise ValueError(f"not a position: {text!r}")
    value = int(match.group(1)) + float(match.group(2).replace(",", ".")) / 60
    return -value if match.group(3) in "SW" else value


def main(path: str) -> None:
    rows = sqlite3.connect(path).execute(
        "SELECT CodeCampagne, NomCampagne, Latitude, Longitude FROM candhis_houlographes_202412 ORDER BY CodeCampagne"
    ).fetchall()
    stations = []
    for code, name, lat, lon in rows:
        stations.append(
            {
                "id": code,
                "name": name.split("_", 1)[1].replace("_", " ") if "_" in name else name,
                "lat": round(degrees(lat), 4),
                "lon": round(degrees(lon), 4),
                "real_time": True if code in REAL_TIME_SEEN else None,
            }
        )
    data = {
        "source": "CANDHIS (Cerema), file 'Stations de mesure de Candhis' of 2024-12-03 (data.gouv.fr)",
        "licence": "Licence Ouverte 2.0",
        "count": len(stations),
        "stations": stations,
    }
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"{len(stations)} stations -> {OUT}")


if __name__ == "__main__":
    main(sys.argv[1])
