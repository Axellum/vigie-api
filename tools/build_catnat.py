"""Build the compact offline GASPAR CatNat lookup bundle (app/data/gaspar-catnat.json.gz).

Reads the official TACCT "Arrêtés de catastrophe naturelle par commune" CSV
published on data.gouv.fr (Licence Ouverte 2.0) and pairs each commune with its
centroid from DINUM/Etalab's API Découpage Administratif (geo.api.gouv.fr).

Only French departments present in the GASPAR dataset are kept (the 101 French
metropolitan and overseas departments 01..95, 2A, 2B, 971, 972, 973, 974, 976),
including covered communes with zero recorded decrees so point lookups never
fall back onto a neighbouring commune with decrees. Overseas territories absent
from GASPAR (975, 977, 978, 984, 986, 987, 988, 989) are intentionally excluded.

Usage:
    python tools/build_catnat.py
    python tools/build_catnat.py --csv /tmp/catnat.csv --communes /tmp/communes.json
"""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "app" / "data" / "gaspar-catnat.json.gz"

DATA_GOUV_DATASET_URL = "https://www.data.gouv.fr/datasets/arretes-de-catastrophe-naturelle-par-commune"
CATNAT_CSV_URL = (
    "https://static.data.gouv.fr/resources/"
    "arretes-de-catastrophe-naturelle-par-commune/20260720-124514/catnat.csv"
)
COMMUNES_GEO_URL = (
    "https://geo.api.gouv.fr/communes"
    "?fields=code,nom,centre,codeDepartement&format=json"
)
DEFAULT_DATASET_UPDATED = "2026-07-20"
USER_AGENT = "vigie-api/build_catnat (https://github.com/Axellum/vigie-api)"

# Exact 8 hazard labels present in the GASPAR TACCT CSV, mapped to stable English slugs.
HAZARD_TYPES: list[dict[str, Any]] = [
    {"idx": 0, "slug": "floods", "label": "Inondations"},
    {"idx": 1, "slug": "drought", "label": "Sécheresse"},
    {"idx": 2, "slug": "landslides", "label": "Mouvements de terrain"},
    {"idx": 3, "slug": "storms_cyclones", "label": "Cyclones / Tempêtes"},
    {"idx": 4, "slug": "hail_snow", "label": "Grêle / neige"},
    {"idx": 5, "slug": "clay_shrink_swell", "label": "Retrait-gonflement des argiles"},
    {"idx": 6, "slug": "avalanche", "label": "Avalanche"},
    {"idx": 7, "slug": "other", "label": "Divers"},
]
LABEL_TO_TYPE_IDX: dict[str, int] = {item["label"]: item["idx"] for item in HAZARD_TYPES}


def _clean_iso_date(raw: str) -> str:
    """Normalize a GASPAR timestamp like '1999-12-25 00:00:00.000' to '1999-12-25'."""
    value = raw.strip()
    if len(value) >= 10 and value[4] == "-" and value[7] == "-":
        return value[:10]
    return ""


def parse_catnat_csv(text: str) -> tuple[dict[str, list[tuple[int, str, str, str]]], dict[str, str], set[str]]:
    """Parse headerless 16-column GASPAR CatNat CSV into per-commune tuples."""
    events_by_commune: dict[str, list[tuple[int, str, str, str]]] = defaultdict(list)
    csv_names: dict[str, str] = {}
    covered_depts: set[str] = set()

    reader = csv.reader(io.StringIO(text), delimiter=";")
    for line_no, row in enumerate(reader, start=1):
        if not row:
            continue
        if len(row) != 16:
            raise ValueError(f"Line {line_no}: expected 16 semicolon columns, got {len(row)}")

        insee = row[1].strip().upper()
        name = row[2].strip()
        dept = row[5].strip().upper()
        hazard_label = row[12].strip()
        start_date = _clean_iso_date(row[13])
        end_date = _clean_iso_date(row[14])
        decree_date = _clean_iso_date(row[15])

        if not insee or not dept:
            raise ValueError(f"Line {line_no}: missing INSEE code or department")
        if hazard_label not in LABEL_TO_TYPE_IDX:
            raise ValueError(f"Line {line_no}: unknown GASPAR hazard label {hazard_label!r}")

        covered_depts.add(dept)
        if name and insee not in csv_names:
            csv_names[insee] = name
        events_by_commune[insee].append((LABEL_TO_TYPE_IDX[hazard_label], start_date, end_date, decree_date))

    return events_by_commune, csv_names, covered_depts


def build_bundle_payload(
    csv_text: str,
    communes_data: list[dict[str, Any]],
    dataset_updated: str = DEFAULT_DATASET_UPDATED,
) -> dict[str, Any]:
    """Assemble the compact offline JSON structure from parsed CSV and communes geometry."""
    events_by_commune, csv_names, covered_depts = parse_catnat_csv(csv_text)

    communes_by_code: dict[str, dict[str, Any]] = {}
    for entry in communes_data:
        code = str(entry.get("code", "")).strip().upper()
        dept = str(entry.get("codeDepartement", "")).strip().upper()
        if not code or dept not in covered_depts:
            continue
        centre = entry.get("centre") or {}
        coords = centre.get("coordinates") or []
        if len(coords) != 2:
            continue
        lon = round(float(coords[0]), 5)
        lat = round(float(coords[1]), 5)
        communes_by_code[code] = {
            "c": code,
            "n": str(entry.get("nom") or csv_names.get(code) or code).strip(),
            "d": dept,
            "lat": lat,
            "lon": lon,
        }

    missing_insee = sorted(set(events_by_commune) - set(communes_by_code))
    if missing_insee:
        sample = ", ".join(missing_insee[:5])
        raise ValueError(f"{len(missing_insee)} CatNat INSEE codes missing from communes lookup: {sample}")

    earliest_start: str | None = None
    latest_start: str | None = None
    latest_decree: str | None = None

    packed_communes: list[dict[str, Any]] = []
    for code in sorted(communes_by_code):
        item = dict(communes_by_code[code])
        raw_events = events_by_commune.get(code, [])
        # Newest first for direct paginated slicing at query time.
        raw_events.sort(key=lambda ev: (ev[1], ev[3], ev[2], ev[0]), reverse=True)

        packed_parts: list[str] = []
        for type_idx, start_date, end_date, decree_date in raw_events:
            if start_date:
                if earliest_start is None or start_date < earliest_start:
                    earliest_start = start_date
                if latest_start is None or start_date > latest_start:
                    latest_start = start_date
            if decree_date and (latest_decree is None or decree_date > latest_decree):
                latest_decree = decree_date
            packed_parts.append(f"{type_idx},{start_date},{end_date},{decree_date}")

        item["e"] = ";".join(packed_parts)
        packed_communes.append(item)

    return {
        "version": 1,
        "source": "GASPAR CatNat (TACCT extraction on data.gouv.fr) + DINUM/Etalab API Decoupage Administratif",
        "source_url": DATA_GOUV_DATASET_URL,
        "dataset_updated": dataset_updated,
        "earliest_start_date": earliest_start,
        "latest_start_date": latest_start,
        "latest_decree_date": latest_decree,
        "hazard_types": HAZARD_TYPES,
        "covered_departments": sorted(covered_depts),
        "communes": packed_communes,
    }


def write_bundle_gz(payload: dict[str, Any], output_path: Path) -> None:
    """Write deterministic gzip-compressed JSON (mtime=0 for reproducible builds)."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    raw_bytes = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    with output_path.open("wb") as fh:
        with gzip.GzipFile(filename="", mode="wb", compresslevel=9, fileobj=fh, mtime=0) as gz:
            gz.write(raw_bytes)


def _fetch_text(url: str) -> str:
    with httpx.Client(timeout=120.0, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=None, help="Local path to catnat.csv (downloads if omitted)")
    parser.add_argument("--communes", type=Path, default=None, help="Local path to geo.api.gouv.fr communes JSON")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Destination .json.gz file")
    parser.add_argument("--dataset-updated", default=DEFAULT_DATASET_UPDATED, help="Dataset modification date (ISO)")
    args = parser.parse_args()

    csv_text = args.csv.read_text(encoding="utf-8") if args.csv else _fetch_text(CATNAT_CSV_URL)
    if args.communes:
        communes_data = json.loads(args.communes.read_text(encoding="utf-8"))
    else:
        communes_data = json.loads(_fetch_text(COMMUNES_GEO_URL))

    payload = build_bundle_payload(csv_text, communes_data, dataset_updated=args.dataset_updated)
    write_bundle_gz(payload, args.output)

    size_kib = args.output.stat().st_size / 1024
    total_events = sum(len(c["e"].split(";")) if c["e"] else 0 for c in payload["communes"])
    print(
        f"Wrote {args.output} ({size_kib:.1f} KiB): "
        f"{len(payload['communes'])} communes across {len(payload['covered_departments'])} departments, "
        f"{total_events} decrees ({payload['earliest_start_date']} .. {payload['latest_decree_date']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
