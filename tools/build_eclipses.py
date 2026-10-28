"""Build ``app/data/solar-eclipses.json.gz``: NASA's solar eclipses of 2001 to 2100 and their Besselian elements.

Two NASA sources, both by Fred Espenak:

1. **The list and the catalogue**: the table behind the Five Millennium Canon of Solar Eclipses (Espenak and Meeus,
   NASA/TP-2006-214141), one CSV file (https://eclipse.gsfc.nasa.gov/eclipse_besselian_from_mysqldump2.csv) with one row per eclipse:
   the catalogue number, NASA's type code, the Saros series, the place of greatest eclipse, and Besselian elements computed with the
   delta-T the canon extrapolated in 2006 (76.0 s for 2027, for instance).
2. **The elements of each eclipse**, from its page on NASA's eclipse web site
   (``https://eclipse.gsfc.nasa.gov/SEbeselm/SEbeselm2001/SE2027Aug02Tbeselm.html``, ``SEbeselm2051/`` after 2050): the elements, delta-T, gamma, magnitude and the
   instant of greatest eclipse that NASA's path tables and maps of the same site use (71.7 s of delta-T for 2027). They are taken when
   the page can be read and parsed; otherwise the canon's (the record says which, in ``elements_source``).

Every value is kept as NASA writes it: a field it leaves empty stays ``null``; the path width and the central duration of a partial
eclipse, which the canon writes as 0, are ``null`` (a partial eclipse has no path).

"Permission is freely granted to reproduce this data when accompanied by an acknowledgment: 'Eclipse Predictions by Fred Espenak,
NASA's GSFC'" (the eclipse pages of the site, read on 2026-10-28; ``SOURCES.md``).

    python tools/build_eclipses.py                              # download everything (one page a second) and write the file
    python tools/build_eclipses.py --csv FILE --pages DIR       # the CSV from a copy; the pages read from (and saved to) DIR
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import html
import io
import json
import re
import time
from datetime import date
from pathlib import Path
from typing import Any, Optional

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT = REPO_ROOT / "app" / "data" / "solar-eclipses.json.gz"
CSV_URL = "https://eclipse.gsfc.nasa.gov/eclipse_besselian_from_mysqldump2.csv"
PAGE_URL = "https://eclipse.gsfc.nasa.gov/SEbeselm/SEbeselm{folder}/SE{year}{month}{day:02d}{letter}beselm.html"
FIRST_YEAR, LAST_YEAR = 2001, 2100
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
USER_AGENT = "vigie-api build_eclipses (personal project)"


def _number(text: str) -> Optional[float]:
    text = text.strip()
    return float(text) if text else None


def _required(row: dict[str, str], name: str) -> float:
    value = _number(row[name])
    if value is None:
        raise ValueError(f"the row of {row['year']}-{row['month']}-{row['day']} has no {name}")
    return value


def canon_record(row: dict[str, str]) -> dict[str, Any]:
    """One row of the canon's CSV as the record the API reads (``app/services/eclipse_besselian.py``)."""
    year, month, day = int(row["year"]), int(row["month"]), int(row["day"])
    type_code = row["eclipse_type"].strip()
    partial = type_code.startswith("P")
    h, m, s = (int(part) for part in row["td_ge"].strip().split(":"))
    return {
        "date": f"{year:04d}-{month:02d}-{day:02d}",
        "catalogue_number": int(_required(row, "cat_no")),
        "type": type_code,
        "saros": int(_required(row, "saros")),
        "greatest_ut_s": h * 3600 + m * 60 + s - _required(row, "dt"),  # seconds after 0 h UT of the date
        "delta_t_s": _required(row, "dt"),
        "gamma": _required(row, "gamma"),
        "magnitude": _required(row, "magnitude"),
        "greatest_lat": _number(row["lat_dd_ge"]),
        "greatest_lon": _number(row["lng_dd_ge"]),
        "sun_altitude_deg": _number(row["sun_alt"]),
        "sun_azimuth_deg": _number(row["sun_azm"]),
        "path_width_km": None if partial else _number(row["path_width"]),
        "central_duration_s": None if partial else _number(row["duration_secs"]),
        "t0": _required(row, "t0"),
        "x": [_required(row, f"x{i}") for i in range(4)],
        "y": [_required(row, f"y{i}") for i in range(4)],
        "d": [_required(row, f"d{i}") for i in range(3)],
        "mu": [_required(row, f"mu{i}") for i in range(3)],
        "l1": [_required(row, f"l1{i}") for i in range(3)],
        "l2": [_required(row, f"l2{i}") for i in range(3)],
        "tan_f1": _required(row, "tan_f1"),
        "tan_f2": _required(row, "tan_f2"),
        "tmin": _required(row, "tmin"),
        "tmax": _required(row, "tmax"),
        "elements_source": CSV_URL,
    }


def page_url(record: dict[str, Any]) -> str:
    year, month, day = (int(part) for part in record["date"].split("-"))
    folder = 2001 if year <= 2050 else 2051  # NASA keeps the pages by half-century
    return PAGE_URL.format(folder=folder, year=year, month=MONTHS[month - 1], day=day, letter=record["type"][0])


def _blocks(page: str) -> list[str]:
    return [html.unescape(re.sub(r"<[^>]+>", "", m.group(1))) for m in re.finditer(r"<pre>(.*?)</pre>", page, re.S | re.I)]


def _search(pattern: str, text: str) -> re.Match[str]:
    match = re.search(pattern, text)
    if match is None:
        raise ValueError(f"not found on the page: {pattern}")
    return match


def _angle(degrees: str, minutes: str, side: str) -> float:
    value = int(degrees) + float(minutes) / 60.0
    return -value if side in "SW" else value


def parse_page(page: str) -> dict[str, Any]:
    """The elements, delta-T, gamma, magnitude and greatest eclipse that a NASA Besselian-elements page states."""
    blocks = _blocks(page)
    if not blocks:
        raise ValueError("no table on the page")
    main = blocks[0]
    hms = _search(r"Greatest Eclipse:\s*\(=(\d\d):(\d\d):(\d\d(?:\.\d+)?) UT\)", main)
    t0 = _search(r"Polynomial Besselian Elements for:.*?(\d\d):(\d\d):(\d\d(?:\.\d+)?) TDT", main)
    t0_h = int(t0.group(1)) + int(t0.group(2)) / 60 + float(t0.group(3)) / 3600
    valid = _search(r"valid over the period\s+(-?[\d.]+)\s*≤\s*t0\s*≤\s*(-?[\d.]+)\s*TDT", main)
    rows: dict[int, list[float]] = {}
    for line in main.splitlines():
        parts = line.split()
        if parts and parts[0] in ("0", "1", "2", "3") and len(parts) > 2:
            rows[int(parts[0])] = [float(p) for p in parts[1:]]
    if sorted(rows) != [0, 1, 2, 3] or [len(rows[n]) for n in range(4)] != [6, 6, 5, 2]:
        raise ValueError("the table of elements does not have the expected shape")
    record: dict[str, Any] = {
        "greatest_ut_s": int(hms.group(1)) * 3600 + int(hms.group(2)) * 60 + float(hms.group(3)),
        "delta_t_s": float(_search(r"ΔT\s*=\s*(-?[\d.]+)\s*s", main).group(1)),
        "gamma": float(_search(r"Gamma\s*=\s*(-?[\d.]+)", main).group(1)),
        "magnitude": float(_search(r"Eclipse Magnitude\s*=\s*(-?[\d.]+)", main).group(1)),
        "t0": t0_h,
        "x": [rows[n][0] for n in range(4)],
        "y": [rows[n][1] for n in range(4)],
        "d": [rows[n][2] for n in range(3)],
        "l1": [rows[n][3] for n in range(3)],
        "l2": [rows[n][4] for n in range(3)],
        "mu": [rows[0][5], rows[1][5], 0.0],
        "tan_f1": float(_search(r"Tan ƒ1\s*=\s*(-?[\d.]+)", main).group(1)),
        "tan_f2": float(_search(r"Tan ƒ2\s*=\s*(-?[\d.]+)", main).group(1)),
        "tmin": float(valid.group(1)) - t0_h,
        "tmax": float(valid.group(2)) - t0_h,
    }
    greatest = next((b for b in blocks[1:] if "Greatest Eclipse:" in b), None)
    if greatest is not None:  # a central eclipse: where and how long, as the page says
        place = _search(r"Lat\s*=\s*(\d+)°([\d.]+)'([NS])\s+Long\s*=\s*(\d+)°([\d.]+)'([EW])", greatest)
        duration = _search(r"Central Duration\s*=\s*(\d+)m([\d.]+)s", greatest)
        record.update(
            greatest_lat=round(_angle(*place.group(1, 2, 3)), 5),
            greatest_lon=round(_angle(*place.group(4, 5, 6)), 5),
            sun_altitude_deg=float(_search(r"Sun Altitude\s*=\s*(-?[\d.]+)°", greatest).group(1)),
            sun_azimuth_deg=float(_search(r"Sun Azimuth\s*=\s*(-?[\d.]+)°", greatest).group(1)),
            path_width_km=float(_search(r"Path Width\s*=\s*([\d.]+)\s*km", greatest).group(1)),
            central_duration_s=int(duration.group(1)) * 60 + float(duration.group(2)),
        )
    return record


def read_page(url: str, pages: Optional[Path], client: httpx.Client) -> Optional[str]:
    """The page, from ``pages`` when it was saved there, else downloaded (and saved); ``None`` when NASA has none."""
    saved = pages / url.rsplit("/", 1)[-1] if pages else None
    if saved is not None and saved.exists():
        return saved.read_text(encoding="latin-1")
    response = client.get(url)
    time.sleep(1.0)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    if saved is not None:
        saved.write_bytes(response.content)
    return response.content.decode("latin-1")


def build(raw: bytes, read_on: str, pages: Optional[Path] = None, client: Optional[httpx.Client] = None) -> dict[str, Any]:
    rows = csv.DictReader(io.StringIO(raw.decode("utf-8")))
    eclipses = sorted(
        (canon_record(row) for row in rows if FIRST_YEAR <= int(row["year"]) <= LAST_YEAR), key=lambda e: e["date"]
    )
    if not eclipses:
        raise ValueError(f"no eclipse of {FIRST_YEAR} to {LAST_YEAR} in the file")
    own_client = client is None
    client = client or httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=60, follow_redirects=True)
    try:
        for record in eclipses:
            url = page_url(record)
            page = read_page(url, pages, client)
            if page is None:
                print(f"{record['date']}: no page at {url}; the canon's elements are kept")
                continue
            try:
                stated = parse_page(page)
            except ValueError as error:
                print(f"{record['date']}: {url} not read ({error}); the canon's elements are kept")
                continue
            record.update(stated)
            record["elements_source"] = url
    finally:
        if own_client:
            client.close()
    return {
        "sources": [CSV_URL, PAGE_URL.split("SEbeselm{")[0]],
        "csv_sha256": hashlib.sha256(raw).hexdigest(),
        "read_on": read_on,
        "years": [FIRST_YEAR, LAST_YEAR],
        "eclipses": eclipses,
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--csv", type=Path, help="a copy of the canon's CSV instead of downloading it")
    parser.add_argument("--pages", type=Path, help="a folder where the pages are saved, and read from when already there")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args(argv)
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=120, follow_redirects=True) as client:
        if args.csv:
            raw = args.csv.read_bytes()
        else:
            response = client.get(CSV_URL)
            response.raise_for_status()
            raw = response.content
        if args.pages:
            args.pages.mkdir(parents=True, exist_ok=True)
        data = build(raw, date.today().isoformat(), args.pages, client)
    text = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    with gzip.GzipFile(args.output, "wb", mtime=0) as handle:
        handle.write(text.encode("utf-8"))
    from_pages = sum(1 for e in data["eclipses"] if e["elements_source"] != CSV_URL)
    print(
        f"{len(data['eclipses'])} eclipses of {FIRST_YEAR} to {LAST_YEAR} ({from_pages} with the elements of their page) -> "
        f"{args.output} ({args.output.stat().st_size} bytes)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
