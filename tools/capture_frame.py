"""Capture a real embedded frame and write it to a file.

The application is started in-process (FastAPI ``TestClient`` with its lifespan) and
``GET /v1/embedded/frame`` is fetched from the REAL providers (USGS, EMSC, GDACS, NOAA,
Open-Meteo...). No data is simulated and nothing is served on the network. The output is
meant to be a test fixture of the display repository.

Usage (from the repository root)::

    python tools/capture_frame.py frame.txt
    python tools/capture_frame.py frame.txt --device-type tab5 --lat 47.75 --lon -3.36 --marine

By default a temporary database with the initial watchlist is used, so the developer's
``vigie.db`` is neither read nor changed. Use ``--db`` to capture with another watchlist.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[1]


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Capture a real /v1/embedded/frame to a file.")
    parser.add_argument("output", type=Path, help="file to write (overwritten)")
    parser.add_argument("--device-type", default="waveshare-7b", help="display profile (default: waveshare-7b)")
    parser.add_argument("--lat", type=float, help="optional GPS latitude (adds the GPS zone)")
    parser.add_argument("--lon", type=float, help="optional GPS longitude")
    parser.add_argument("--marine", action="store_true", help="include marine conditions in the GPS zone")
    parser.add_argument("--max-hazards", type=int, default=12, help="maximum number of HAZ lines (default: 12)")
    parser.add_argument("--db", type=Path, help="SQLite database holding the watchlist (default: a temporary one)")
    args = parser.parse_args(argv)

    if (args.lat is None) != (args.lon is None):
        parser.error("--lat and --lon must be given together")

    # Must be set before the application is imported.
    os.environ["VIGIE_SCHEDULER"] = "0"
    if args.db is not None:
        os.environ["VIGIE_DB_PATH"] = str(args.db)
    else:
        os.environ["VIGIE_DB_PATH"] = str(Path(tempfile.mkdtemp(prefix="vigie-capture-")) / "capture.db")
    sys.path.insert(0, str(REPO_ROOT))

    from fastapi.testclient import TestClient

    from app.main import app
    from app.services.embedded_frame import verify_embedded_frame_crc

    params: dict[str, object] = {"device_type": args.device_type, "max_hazards": args.max_hazards}
    if args.lat is not None:
        params.update(lat=args.lat, lon=args.lon, marine=str(args.marine).lower())

    with TestClient(app) as client:
        response = client.get("/v1/embedded/frame", params=params, timeout=120.0)

    if response.status_code != 200:
        print(f"error: HTTP {response.status_code}: {response.text[:200]}", file=sys.stderr)
        return 1
    frame = response.text
    if not verify_embedded_frame_crc(frame):
        print("error: the frame CRC32 is not valid", file=sys.stderr)
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(response.content)

    lines = [line.split("|") for line in frame.split("\n") if line]
    counts = Counter(cols[0] for cols in lines)
    no_weather = [cols[1] for cols in lines if cols[0] == "ZONE" and not cols[6]]
    print(f"wrote {args.output} ({len(response.content)} bytes)")
    print("lines: " + ", ".join(f"{tag}={n}" for tag, n in counts.items()))
    if no_weather:
        print(f"warning: no weather for zone(s) {', '.join(no_weather)} (providers unreachable?)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
