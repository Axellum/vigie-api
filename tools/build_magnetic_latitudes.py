"""Build ``app/data/apex-latitudes.json.gz``: the magnetic latitude of every point of a 2-degree grid, by tracing the field of IGRF-14.

The aurora follows the Earth's magnetic field, and a centred dipole puts Western Europe 5 to 10 degrees too far north (the
Aurorasaurus database, whose ``mlat`` column follows the real field, puts northern France near 45 degrees where the dipole says 52).
The latitude this file holds is the **modified apex latitude** (Richmond 1995, J. Geomag. Geoelectr. 47, 191-212): the field line
through the point at the reference height of 110 km is followed to its highest point, at a distance ``r_apex`` from the centre of the
Earth, and the latitude is ``arccos(sqrt((R + 110 km) / r_apex))``, positive where the field points down (the northern magnetic
hemisphere). The field is the internal field of IGRF-14 (degree 13), from the coefficients published by IAGA through NOAA NCEI
(https://www.ngdc.noaa.gov/IAGA/vmod/coeffs/igrf14coeffs.txt), at the epoch asked (2025.0 plus the secular variation for a later one).

    python tools/build_magnetic_latitudes.py                  # the grid for the epoch 2026.5, into app/data/
    python tools/build_magnetic_latitudes.py --check FILE     # compare with the mlat of the reports of an Aurorasaurus CSV

The grid is 2 degrees (91 latitudes by 180 longitudes); the API interpolates it (``app/services/magnetic.py``). Over a few years the
field moves by a fraction of a degree: the file says its epoch.
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
from pathlib import Path
from typing import Optional

import httpx
import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT = REPO_ROOT / "app" / "data" / "apex-latitudes.json.gz"
IGRF_URL = "https://www.ngdc.noaa.gov/IAGA/vmod/coeffs/igrf14coeffs.txt"
EARTH_RADIUS_KM = 6371.2  # the reference radius of IGRF
REFERENCE_HEIGHT_KM = 110.0
WGS84_A, WGS84_B = 6378.137, 6356.7523142
GRID_DEG = 2.0
DEFAULT_EPOCH = 2026.5


def read_igrf(text: str, epoch: float) -> tuple[np.ndarray, np.ndarray, int]:
    """The Schmidt semi-normalized ``g[n, m]`` and ``h[n, m]`` (nT) of IGRF-14 at ``epoch`` (2025.0 and its secular variation after)."""
    header: Optional[list[str]] = None
    rows: list[list[str]] = []
    for line in text.splitlines():
        parts = line.split()
        if not parts or line.startswith("#"):
            continue
        if parts[0] == "g/h":
            header = parts
            continue
        if parts[0] in ("g", "h") and header is not None:
            rows.append(parts)
    if header is None or not rows:
        raise ValueError("no coefficient table in the IGRF file")
    epochs = [float(x) for x in header[3:-1]]
    nmax = max(int(row[1]) for row in rows)
    g = np.zeros((nmax + 1, nmax + 1))
    h = np.zeros((nmax + 1, nmax + 1))
    for row in rows:
        n, m = int(row[1]), int(row[2])
        values = [float(x) for x in row[3:]]
        series, secular = values[:-1], values[-1]
        if epoch >= epochs[-1]:
            value = series[-1] + secular * (epoch - epochs[-1])
        else:
            index = max(i for i, e in enumerate(epochs) if e <= epoch)
            share = (epoch - epochs[index]) / (epochs[index + 1] - epochs[index])
            value = series[index] + (series[index + 1] - series[index]) * share
        (g if row[0] == "g" else h)[n, m] = value
    return g, h, nmax


def field(g: np.ndarray, h: np.ndarray, nmax: int, r: float, theta: float, phi: float) -> tuple[float, float, float]:
    """``(Br, Btheta, Bphi)`` in nT at the geocentric radius ``r`` (km), colatitude ``theta`` and longitude ``phi`` (radians)."""
    cos_t, sin_t = math.cos(theta), max(1e-12, math.sin(theta))
    # Schmidt semi-normalized associated Legendre functions and their derivatives in theta.
    p = np.zeros((nmax + 1, nmax + 1))
    dp = np.zeros((nmax + 1, nmax + 1))
    p[0, 0] = 1.0
    for n in range(1, nmax + 1):
        for m in range(0, n + 1):
            if n == m:
                factor = math.sqrt(1.0 - 1.0 / (2 * n)) if n > 1 else 1.0
                p[n, n] = factor * sin_t * p[n - 1, n - 1]
                dp[n, n] = factor * (sin_t * dp[n - 1, n - 1] + cos_t * p[n - 1, n - 1])
            else:
                k = math.sqrt(n * n - m * m)
                a = (2 * n - 1) / k
                b = math.sqrt((n - 1) ** 2 - m * m) / k if n - 1 >= m else 0.0
                p[n, m] = a * cos_t * p[n - 1, m] - (b * p[n - 2, m] if n >= 2 else 0.0)
                dp[n, m] = a * (cos_t * dp[n - 1, m] - sin_t * p[n - 1, m]) - (b * dp[n - 2, m] if n >= 2 else 0.0)
    br = bt = bp = 0.0
    ratio = EARTH_RADIUS_KM / r
    for n in range(1, nmax + 1):
        scale = ratio ** (n + 2)
        for m in range(0, n + 1):
            cos_mp, sin_mp = math.cos(m * phi), math.sin(m * phi)
            gh = g[n, m] * cos_mp + h[n, m] * sin_mp
            br += (n + 1) * scale * gh * p[n, m]
            bt -= scale * gh * dp[n, m]
            bp += scale * m * (g[n, m] * sin_mp - h[n, m] * cos_mp) * p[n, m] / sin_t
    return br, bt, bp


def geodetic_to_geocentric(lat_deg: float, height_km: float) -> tuple[float, float]:
    """The geocentric radius (km) and colatitude (radians) of a WGS84 geodetic latitude and height."""
    lat = math.radians(lat_deg)
    a2, b2 = WGS84_A**2, WGS84_B**2
    rho = math.sqrt(a2 * math.cos(lat) ** 2 + b2 * math.sin(lat) ** 2)
    x = (height_km + a2 / rho) * math.cos(lat)
    z = (height_km + b2 / rho) * math.sin(lat)
    return math.hypot(x, z), math.atan2(x, z)


def apex_latitude(g: np.ndarray, h: np.ndarray, nmax: int, lat_deg: float, lon_deg: float) -> float:
    """The modified apex latitude (degrees) of the geodetic point ``(lat, lon)``, by following its field line from 110 km up."""
    r, theta = geodetic_to_geocentric(lat_deg, REFERENCE_HEIGHT_KM)
    phi = math.radians(lon_deg)
    position = np.array([r * math.sin(theta) * math.cos(phi), r * math.sin(theta) * math.sin(phi), r * math.cos(theta)])

    def direction(point: np.ndarray) -> np.ndarray:
        rr = float(np.linalg.norm(point))
        th = math.acos(max(-1.0, min(1.0, point[2] / rr)))
        ph = math.atan2(point[1], point[0])
        br, bt, bp = field(g, h, nmax, rr, th, ph)
        st, ct, sp, cp = math.sin(th), math.cos(th), math.sin(ph), math.cos(ph)
        vector = np.array(
            [br * st * cp + bt * ct * cp - bp * sp, br * st * sp + bt * ct * sp + bp * cp, br * ct - bt * st]
        )
        return vector / float(np.linalg.norm(vector))

    start_br = field(g, h, nmax, r, theta, phi)[0]
    sign_hemisphere = 1.0 if start_br < 0 else -1.0  # the field points down in the northern magnetic hemisphere
    # Follow the line upwards: along +B where it points up, along -B where it points down.
    way = -1.0 if start_br < 0 else 1.0
    best = float(np.linalg.norm(position))
    point = position
    for _ in range(20000):
        rr = float(np.linalg.norm(point))
        step = max(2.0, 0.01 * rr) * way
        k1 = direction(point)
        k2 = direction(point + 0.5 * step * k1)
        k3 = direction(point + 0.5 * step * k2)
        k4 = direction(point + step * k3)
        point = point + step * (k1 + 2 * k2 + 2 * k3 + k4) / 6.0
        new_r = float(np.linalg.norm(point))
        if new_r < best:
            break
        best = new_r
        if best > 60 * EARTH_RADIUS_KM:
            break
    ratio = (EARTH_RADIUS_KM + REFERENCE_HEIGHT_KM) / best
    return sign_hemisphere * math.degrees(math.acos(math.sqrt(min(1.0, ratio))))


def _row(job: tuple[float, float, str]) -> list[float]:
    """One latitude of the grid (a worker of ``build``)."""
    lat, epoch, text = job
    g, h, nmax = read_igrf(text, epoch)
    lons = [-180.0 + GRID_DEG * j for j in range(int(360 / GRID_DEG))]
    return [round(apex_latitude(g, h, nmax, max(-89.9, min(89.9, lat)), lon), 2) for lon in lons]


def build(epoch: float, text: str, workers: int = 4) -> dict[str, object]:
    """The grid at ``epoch``, one latitude per task, ``workers`` processes."""
    from multiprocessing import Pool

    lats = [-90.0 + GRID_DEG * i for i in range(int(180 / GRID_DEG) + 1)]
    lons = [-180.0 + GRID_DEG * j for j in range(int(360 / GRID_DEG))]
    with Pool(workers) as pool:
        table = pool.map(_row, [(lat, epoch, text) for lat in lats])
    return {
        "what": "Modified apex latitude (degrees) at 110 km, by tracing the field lines of IGRF-14 (tools/build_magnetic_latitudes.py)",
        "source": IGRF_URL,
        "epoch": epoch,
        "grid_deg": GRID_DEG,
        "lat_first": lats[0],
        "lon_first": lons[0],
        "rows": table,
    }


def check(csv_path: Path, text: str, sample: int) -> None:
    """Compare the traced latitude with the ``mlat`` of some reports of an Aurorasaurus CSV (each at its own year)."""
    import csv
    import random
    from datetime import datetime

    with csv_path.open(encoding="utf-8", newline="") as handle:
        rows = [row for row in csv.DictReader(handle) if row.get("mlat") and row.get("st_y")]
    random.seed(1)
    differences: dict[str, list[float]] = {}
    for row in random.sample(rows, min(sample, len(rows))):
        year = datetime.fromisoformat(row["time_start"]).year + 0.5
        g, h, nmax = read_igrf(text, year)
        traced = apex_latitude(g, h, nmax, float(row["st_y"]), float(row["st_x"]))
        differences.setdefault(row["address_country"] or "?", []).append(traced - float(row["mlat"]))
    every = [d for ds in differences.values() for d in ds]
    every.sort()
    print(f"all: {len(every)} reports, median {every[len(every) // 2]:+.2f}, 10th {every[len(every) // 10]:+.2f}, 90th {every[9 * len(every) // 10]:+.2f}")
    for country, ds in sorted(differences.items(), key=lambda item: -len(item[1]))[:12]:
        ds.sort()
        print(f"{country:20} {len(ds):4} median {ds[len(ds) // 2]:+.2f}")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--epoch", type=float, default=DEFAULT_EPOCH)
    parser.add_argument("--igrf", help="a local copy of igrf14coeffs.txt (otherwise it is downloaded)")
    parser.add_argument("--check", help="an Aurorasaurus CSV: compare instead of building")
    parser.add_argument("--sample", type=int, default=300)
    parser.add_argument("--workers", type=int, default=4, help="processes that trace the grid (4 by default)")
    parser.add_argument("--output", default=str(OUTPUT))
    args = parser.parse_args(argv)
    if args.igrf:
        text = Path(args.igrf).read_text(encoding="utf-8")
    else:
        text = httpx.get(IGRF_URL, headers={"User-Agent": "vigie-api build_magnetic_latitudes (personal project)"}, timeout=60).text
    if args.check:
        check(Path(args.check), text, args.sample)
        return 0
    data = build(args.epoch, text, args.workers)
    with gzip.open(args.output, "wt", encoding="utf-8") as handle:
        json.dump(data, handle, separators=(",", ":"))
    print(f"\nwrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
