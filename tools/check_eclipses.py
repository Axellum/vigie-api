"""Check Vigie's eclipse computation against NASA's path table of an eclipse, and against astronomy-engine at a few places.

NASA's path tables (``https://eclipse.gsfc.nasa.gov/SEpath/SEpath2001/SE2027Aug02Tpath.html``) give, every two minutes, the northern
and southern limits of the path, the central line, the Sun's altitude and the central duration, computed from the same Besselian
elements as the ones Vigie bundles. For each row this tool works out, from the bundled elements:

* on the central line: the instant of the maximum (it should be the row's time) and the central duration;
* on each limit: how far the place is from the edge of the umbra at its maximum (0 km on the limit).

It then compares the local circumstances of a few cities with those of astronomy-engine (MIT; its own ephemerides and delta-T), an
independent check of the contacts and of the share of the Sun hidden.

    python tools/check_eclipses.py                          # 2027-08-02, the table downloaded
    python tools/check_eclipses.py --date 2027-08-02 --table FILE --markdown
"""

from __future__ import annotations

import argparse
import html
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import eclipse_besselian as eb  # noqa: E402

PATH_URL = "https://eclipse.gsfc.nasa.gov/SEpath/SEpath{folder}/SE{year}{month}{day:02d}{letter}path.html"
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
CITIES = {
    "Cadiz": (36.5271, -6.2886),
    "Malaga": (36.7213, -4.4214),
    "Sevilla": (37.3891, -5.9845),
    "Madrid": (40.4168, -3.7038),
    "Paris": (48.8566, 2.3522),
    "Tunis": (36.8065, 10.1815),
    "Luxor": (25.6872, 32.6396),
    "Jeddah": (21.4858, 39.1925),
}


@dataclass(frozen=True)
class PathRow:
    """One row of NASA's path table: the time (seconds after 0 h UT), the three points, the Sun's altitude and the duration."""

    time_s: int
    north: tuple[float, float]
    south: tuple[float, float]
    central: tuple[float, float]
    sun_altitude: int
    duration_s: float


def _angle(degrees: str, minutes: str, side: str) -> float:
    value = int(degrees) + float(minutes) / 60.0
    return -value if side in "SW" else value


_POINT = r"(\d+) (\d+\.\d)([NS]) (\d+) (\d+\.\d)([EW])"
_ROW = re.compile(
    r"^\s*(\d\d):(\d\d)\s+" + r"\s+".join([_POINT] * 3) + r"\s+([\d.]+)\s+(\d+)\s+(\d+|-)\s+(\d+)\s+(\d\d)m(\d\d\.\d)s"
)


def parse_path_table(page: str) -> list[PathRow]:
    """The timed rows of a NASA path table (the 'Limits' rows, which have no time, are left out)."""
    match = re.search(r"<pre>(.*?)</pre>", page, re.S | re.I)
    if match is None:
        raise ValueError("no table on the page")
    text = html.unescape(re.sub(r"<[^>]+>", "", match.group(1)))
    rows = []
    for line in text.splitlines():
        m = _ROW.match(line)
        if m is None:
            continue
        g = m.groups()
        rows.append(
            PathRow(
                time_s=int(g[0]) * 3600 + int(g[1]) * 60,
                north=(_angle(*g[2:5]), _angle(*g[5:8])),
                south=(_angle(*g[8:11]), _angle(*g[11:14])),
                central=(_angle(*g[14:17]), _angle(*g[17:20])),
                sun_altitude=int(g[21]),
                duration_s=int(g[24]) * 60 + float(g[25]),
            )
        )
    return rows


def edge_distance_km(eclipse: eb.SolarEclipse, lat: float, lon: float) -> float:
    """How far ``(lat, lon)`` is from the edge of the umbra (or antumbra) at its maximum, in km (negative inside)."""
    places = eb.Places.at(lat, lon)
    s = eb.shadow(eclipse, eb.time_of_maximum(eclipse, places), places)
    return float(np.asarray(s.distance - np.abs(s.l2)).reshape(-1)[0]) * eb.EARTH_RADIUS_KM


@dataclass(frozen=True)
class PathComparison:
    rows: int
    max_time_s: float
    max_duration_s: float
    max_altitude_deg: float
    max_limit_km: float


def compare_path(eclipse: eb.SolarEclipse, rows: list[PathRow]) -> PathComparison:
    times, durations, altitudes, limits = [], [], [], []
    for row in rows:
        local = eb.local_circumstances(eclipse, *row.central)
        maximum = next(c for c in local.contacts if c.name == "maximum")
        times.append(maximum.time_utc - eclipse.midnight_utc - row.time_s)
        durations.append((local.central_duration_s or 0.0) - row.duration_s)
        altitudes.append(maximum.sun_altitude_deg - row.sun_altitude)
        limits += [edge_distance_km(eclipse, *row.north), edge_distance_km(eclipse, *row.south)]
    return PathComparison(
        rows=len(rows),
        max_time_s=max(abs(v) for v in times),
        max_duration_s=max(abs(v) for v in durations),
        max_altitude_deg=max(abs(v) for v in altitudes),
        max_limit_km=max(abs(v) for v in limits),
    )


def compare_astronomy_engine(eclipse: eb.SolarEclipse) -> list[tuple[str, str, str, float, float, Optional[float], Optional[float]]]:
    """For each city: ``(city, our kind, its kind, our share hidden, its share, the largest gap of the contacts in s, of the maximum)``."""
    import astronomy

    year, month, day = (int(p) for p in eclipse.date.split("-"))
    start = astronomy.Time.Make(year, month, day, 0, 0, 0).AddDays(-1)
    result = []
    for city, (lat, lon) in CITIES.items():
        ours = eb.local_circumstances(eclipse, lat, lon)
        if ours.kind == "none":
            continue
        theirs = astronomy.SearchLocalSolarEclipse(start, astronomy.Observer(lat, lon, 0))
        peak = theirs.peak.time.Utc().replace(tzinfo=timezone.utc).timestamp()
        times = {c.name: c.time_utc for c in ours.contacts}
        if abs(peak - times["maximum"]) > 3600:
            continue  # astronomy-engine found another eclipse (this one is not visible there)
        events = {
            "partial_begin": theirs.partial_begin,
            "central_begin": theirs.total_begin,
            "maximum": theirs.peak,
            "central_end": theirs.total_end,
            "partial_end": theirs.partial_end,
        }
        gaps = [
            times[name] - event.time.Utc().replace(tzinfo=timezone.utc).timestamp()
            for name, event in events.items()
            if event is not None and name in times
        ]
        result.append(
            (city, ours.kind, theirs.kind.name.lower(), ours.obscuration, theirs.obscuration, max(abs(g) for g in gaps), times["maximum"] - peak)
        )
    return result


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--date", default="2027-08-02")
    parser.add_argument("--table", type=Path, help="a saved copy of NASA's path page")
    parser.add_argument("--markdown", action="store_true")
    args = parser.parse_args(argv)
    eclipse = eb.eclipse_on(args.date)
    if eclipse is None or eclipse.kind == "partial":
        parser.error("no central eclipse of the catalogue on that date")
    year, month, day = (int(p) for p in args.date.split("-"))
    url = PATH_URL.format(folder=2001 if year <= 2050 else 2051, year=year, month=MONTHS[month - 1], day=day, letter=eclipse.type_code[0])
    if args.table:
        page = args.table.read_text(encoding="latin-1")
    else:
        page = httpx.get(url, headers={"User-Agent": "vigie-api check_eclipses (personal project)"}, timeout=60).text
    path = compare_path(eclipse, parse_path_table(page))
    cities = compare_astronomy_engine(eclipse)
    greatest = datetime.fromtimestamp(eclipse.greatest_utc, timezone.utc).strftime("%H:%M:%S.%f")[:-5]
    if args.markdown:
        print(f"NASA's path table ({url}), {path.rows} rows, the elements of {eclipse.elements_source} (delta-T {eclipse.delta_t_s} s):\n")
        print("| Compared | Largest gap |\n| --- | ---: |")
        print(f"| Instant of the maximum on the central line, against the row's time | {path.max_time_s:.2f} s |")
        print(f"| Central duration on the central line | {path.max_duration_s:.2f} s |")
        print(f"| Sun's altitude (NASA rounds it to the degree) | {path.max_altitude_deg:.2f} degrees |")
        print(f"| Northern and southern limits: distance to the edge of the umbra | {path.max_limit_km:.2f} km |")
        print(f"\nastronomy-engine at {len(cities)} places (greatest eclipse per NASA: {greatest} UT):\n")
        print("| Place | Vigie | astronomy-engine | Share hidden, Vigie | astronomy-engine | Largest gap of a contact | Gap of the maximum |")
        print("| --- | --- | --- | ---: | ---: | ---: | ---: |")
        for city, kind, their_kind, share, their_share, gap, gap_max in cities:
            print(f"| {city} | {kind} | {their_kind} | {share:.3f} | {their_share:.3f} | {gap:.1f} s | {gap_max:+.1f} s |")
    else:
        print(path)
        for row in cities:
            print(row)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
