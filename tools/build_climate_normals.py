"""Build the offline bundle of the climate normals and records of Météo-France (app/data/meteofrance-climate-normals.json.gz).

Reads the "Fiches climatologiques" of Météo-France on data.gouv.fr (Licence Ouverte 2.0): the list of the stations
(`liste_fiches_clim.geojson`) and, for each station, its sheet in the "data" format (`FICHECLIM_<id>.data`, semicolon
text). A sheet holds the monthly normals of the station (1991-2020, or a shorter period said block by block) and its
records with their dates. Only the blocks Vigie serves are kept; a value the sheet marks missing (`-`) stays missing,
a `.` is the sheet's own way of writing zero.

The sheets are refreshed once a month by Météo-France: run this tool again to refresh the bundle.

Usage:
    python tools/build_climate_normals.py
    python tools/build_climate_normals.py --cache-dir /tmp/fiches   # keeps the downloaded sheets, reads them again next time
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Optional

import httpx

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "app" / "data" / "meteofrance-climate-normals.json.gz"

DATASET_URL = "https://www.data.gouv.fr/datasets/fiches-climatologiques"
BASE_URL = "https://meteofrance.s3.sbg.io.cloud.ovh.net/data/synchro_ftp/REF_STATION"
LIST_URL = f"{BASE_URL}/liste_fiches_clim.geojson"
DATASET_API_URL = "https://www.data.gouv.fr/api/1/datasets/684c2d56f3861808c0a5d465/"
_SHEET_URL_RE = re.compile(r"FICHECLIM_(\d{8})\.data$")
USER_AGENT = "vigie-api/build_climate_normals (https://github.com/Axellum/vigie-api)"
WORKERS = 4

# The title of a block in the sheet -> the key in the bundle. Records carry a row of dates.
BLOCKS: dict[str, str] = {
    "La température la plus élevée (°C)": "tmax_record",
    "Température maximale (Moyenne en °C)": "tmax_mean",
    "Température moyenne (Moyenne en °C)": "tmean",
    "Température minimale (Moyenne en °C)": "tmin_mean",
    "La température la plus basse (°C)": "tmin_record",
    "Précipitations : Hauteur quotidienne maximale (mm)": "rain_day_record",
    "Précipitations : Hauteur moyenne mensuelle (mm)": "rain_mean",
    "Rafale maximale de vent (m/s)": "gust_record",
    "Durée d'insolation (Moyenne en heures)": "sunshine_mean",
}
# Rows of the "Nombre moyen de jours avec" blocks, by their label (spaces collapsed).
DAY_ROWS: dict[str, str] = {
    "Tx >= 30°C": "hot_days",
    "Tn <= 0°C": "frost_days",
    "Rr >= 1 mm": "rain_days",
}
RECORD_KEYS = frozenset(key for key in BLOCKS.values() if key.endswith("_record"))

_HEADER_RE = re.compile(
    r"^(?P<name>.+?)\s+Indicatif\s*:\s*(?P<id>\d{8}),\s*alt\s*:\s*(?P<alt>-?\d+)\s*m,\s*"
    r"lat\s*:\s*(?P<lat>[^,]+),\s*lon\s*:\s*(?P<lon>[^;]+)"
)
_DMS_RE = re.compile(r"(\d+)°(\d+)'(\d+)\"([NSEWO])")
_EDITED_RE = re.compile(r"Edité le\s*:\s*(\d{2})/(\d{2})/(\d{4})")
_STATS_PERIOD_RE = re.compile(r"période\s+(\d{4})-(\d{4})")
_RECORD_PERIOD_RE = re.compile(r"du\s+(\d{2})-(\d{2})-(\d{4})\s+au\s+(\d{2})-(\d{2})-(\d{4})")


class SheetError(ValueError):
    """A sheet that cannot be read: its station is left out of the bundle, never guessed."""


def _dms_to_deg(text: str) -> float:
    match = _DMS_RE.search(text)
    if match is None:
        raise SheetError(f"unreadable coordinate {text!r}")
    deg, minutes, seconds, hemi = match.groups()
    value = int(deg) + int(minutes) / 60 + int(seconds) / 3600
    return round(-value if hemi in "SWO" else value, 5)


def _cells(line: str) -> list[str]:
    """The cells of a row, without the empty last one left by the trailing ``;``."""
    cells = [cell.strip() for cell in line.split(";")]
    if cells and cells[-1] == "":
        cells.pop()
    return cells


def _value(cell: str) -> Optional[float]:
    if cell == "-" or cell == "":
        return None
    if cell == ".":
        return 0.0
    return float(cell)


def _record_date(cell: str, month: int) -> Optional[str]:
    """``DD-YYYY`` in a month column is a day of that month; the year column holds the year alone."""
    if cell in ("", "-"):
        return None
    if month == 13:
        if not re.fullmatch(r"\d{4}", cell):
            raise SheetError(f"unreadable record year {cell!r}")
        return cell
    match = re.fullmatch(r"(\d{2})-(\d{4})", cell)
    if match is None:
        raise SheetError(f"unreadable record date {cell!r}")
    return f"{match.group(2)}-{month:02d}-{match.group(1)}"


def _period(line: str) -> Optional[list[str]]:
    record = _RECORD_PERIOD_RE.search(line)
    if record:
        d1, m1, y1, d2, m2, y2 = record.groups()
        return [f"{y1}-{m1}-{d1}", f"{y2}-{m2}-{d2}"]
    stats = _STATS_PERIOD_RE.search(line)
    if stats:
        return [stats.group(1), stats.group(2)]
    return None


def parse_sheet(text: str) -> dict[str, Any]:
    """Read one sheet (``FICHECLIM_<id>.data``) into the compact station of the bundle."""
    lines = [line.rstrip("\r") for line in text.split("\n")]
    header = next((_HEADER_RE.match(line.strip()) for line in lines if "Indicatif" in line), None)
    if header is None:
        raise SheetError("no station line")
    edited = next((_EDITED_RE.search(line) for line in lines if line.startswith("Edité")), None)
    station: dict[str, Any] = {
        "id": header.group("id"),
        "name": header.group("name").strip(),
        "alt": int(header.group("alt")),
        "lat": _dms_to_deg(header.group("lat")),
        "lon": _dms_to_deg(header.group("lon")),
        "edited": f"{edited.group(3)}-{edited.group(2)}-{edited.group(1)}" if edited else None,
        # The period of the normals of the whole sheet; a block that names its own period keeps it in its "p".
        "period": next(
            ([m.group(1), m.group(2)] for line in lines if line.startswith("Ces statistiques") for m in [_STATS_PERIOD_RE.search(line)] if m),
            None,
        ),
        "blocks": {},
    }

    blocks: dict[str, Any] = station["blocks"]
    current: Optional[str] = None
    period: Optional[list[str]] = None
    days_period: Optional[list[str]] = None
    in_days = False
    for raw in lines:
        title = raw.strip().rstrip(";").strip()
        if title in BLOCKS:
            current, period, in_days = BLOCKS[title], None, False
            continue
        if title.startswith("Nombre moyen de jours avec"):
            current, days_period, in_days = None, None, True
            continue
        found = _period(raw)
        if found is not None and (current or in_days):
            if in_days:
                days_period = found
            else:
                period = found
            continue
        cells = _cells(raw)
        if len(cells) != 14:
            # Any other title ("Données non disponibles", a block Vigie does not keep, a footnote) closes the block, so
            # its rows are never read as the rows of the block before it.
            if title:
                current, in_days = None, False
            continue
        label = re.sub(r"\s+", " ", cells[0])
        if in_days:
            key = DAY_ROWS.get(label)
            if key is not None:
                blocks[key] = {"p": days_period, "v": [_value(c) for c in cells[1:]]}
            continue
        if current is None:
            continue
        if label == "":
            blocks[current] = {"p": period, "v": [_value(c) for c in cells[1:]]}
            if current not in RECORD_KEYS:
                current = None
        elif label == "Date" and current in blocks:
            blocks[current]["d"] = [_record_date(c, month) for month, c in enumerate(cells[1:], start=1)]
            current = None
    return station


def build_payload(stations: list[dict[str, Any]], *, built: str) -> dict[str, Any]:
    stations = sorted(stations, key=lambda item: item["id"])
    edited = sorted({s["edited"] for s in stations if s.get("edited")})
    return {
        "dataset": DATASET_URL,
        "dataset_updated": edited[-1] if edited else None,
        "built": built,
        "stations": stations,
    }


def write_bundle_gz(payload: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    # No name and no time in the gzip header: the same sheets give the same bytes.
    with output_path.open("wb") as out, gzip.GzipFile(filename="", fileobj=out, mode="wb", mtime=0) as fh:
        fh.write(raw)


def _fetch(client: httpx.Client, url: str, cache: Optional[Path]) -> Optional[str]:
    if cache is not None and cache.exists():
        return cache.read_text(encoding="utf-8")
    for attempt in range(3):
        # Raw bytes: some sheets come with a "Content-Encoding: gzip" header over a body that is plain text (seen on
        # 2026-11-17), which httpx refuses to decode. The body is gunzipped here only when it really is gzip.
        try:
            with client.stream("GET", url) as response:
                body = b"".join(response.iter_raw())
        except httpx.TransportError:
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))
            continue
        if response.status_code == 404:
            return None
        if response.status_code < 500:
            response.raise_for_status()
            if body[:2] == b"\x1f\x8b":
                body = gzip.decompress(body)
            text = body.decode("utf-8")
            if cache is not None:
                cache.write_text(text, encoding="utf-8")
            return text
        if attempt == 2:
            response.raise_for_status()
        time.sleep(2 * (attempt + 1))
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--cache-dir", type=Path, default=None, help="keep the downloaded sheets here and read them again")
    args = parser.parse_args()
    if args.cache_dir is not None:
        args.cache_dir.mkdir(parents=True, exist_ok=True)

    headers = {"User-Agent": USER_AGENT}
    with httpx.Client(timeout=60.0, follow_redirects=True, headers=headers) as client:
        listing = json.loads(_fetch(client, LIST_URL, None) or "{}")
        ids_set = {feature["properties"]["num"] for feature in listing.get("features", [])}
        # The list of the stations misses a few sheets the dataset publishes (4 on 2026-11-17): read both.
        resources = json.loads(_fetch(client, DATASET_API_URL, None) or "{}").get("resources", [])
        ids_set |= {m.group(1) for r in resources for m in [_SHEET_URL_RE.search(r.get("url", ""))] if m}
        ids = sorted(ids_set)

        def one(station_id: str) -> tuple[str, Optional[dict[str, Any]], Optional[str]]:
            cache = args.cache_dir / f"FICHECLIM_{station_id}.data" if args.cache_dir else None
            text = _fetch(client, f"{BASE_URL}/FICHECLIM_{station_id}.data", cache)
            if text is None:
                return station_id, None, "no data sheet (404)"
            try:
                return station_id, parse_sheet(text), None
            except (SheetError, ValueError) as exc:
                return station_id, None, str(exc)

        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            results = list(pool.map(one, ids))

    stations = [station for _, station, _ in results if station is not None]
    skipped = [(station_id, reason) for station_id, station, reason in results if station is None]
    payload = build_payload(stations, built=time.strftime("%Y-%m-%d", time.gmtime()))
    write_bundle_gz(payload, args.output)
    print(f"{len(ids)} stations listed, {len(stations)} sheets read, {len(skipped)} left out -> {args.output}")
    for station_id, reason in skipped[:20]:
        print(f"  left out {station_id}: {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
