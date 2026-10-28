"""Measure what the archive of the official warnings takes in over a real day, on a running instance.

The archive keeps what the instance reads, and an instance reads a country only when it is needed or asked for. This script
plays the visitor who looks at every country: every ``--every`` seconds it asks ``GET /v1/vigilances?country=XX`` for each
country the instance serves (``GET /v1/vigilances/countries``) and the tsunami messages, then prints what the archive weighs
(``GET /v1/vigilances/archive/stats``). It costs the providers what such a visitor costs: one feed per country per round, and
each CAP document once (``docs/data-sources.md``).

Usage (from the repository root, standard library only, against an instance you started)::

    python tools/measure_archive.py --api http://127.0.0.1:8091 --hours 24 --every 600 --out measure.jsonl

Each round appends one line of JSON to ``--out`` (the time, the answers, the stats), so a measurement that stops can be read.
"""

from __future__ import annotations

import argparse
import json
import time
from typing import Any, Optional
from urllib.error import URLError
from urllib.request import Request, urlopen


def get(api: str, path: str, timeout: float = 120.0) -> tuple[int, Optional[Any]]:
    """The status and the JSON of one request (``(0, None)`` when the instance did not answer)."""
    request = Request(api.rstrip("/") + path, headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - the address is the instance given on the command line
            return response.status, json.loads(response.read())
    except URLError as exc:
        status = getattr(exc, "code", 0)
        return int(status or 0), None
    except (OSError, ValueError):
        return 0, None


def one_round(api: str) -> dict[str, Any]:
    """Ask every served country once, then the stats."""
    started = time.time()
    _, listing = get(api, "/v1/vigilances/countries")
    countries = [entry["country"] for entry in (listing or []) if isinstance(entry, dict) and "sources" in entry]
    answers: dict[str, Any] = {}
    for country in countries:
        status, body = get(api, f"/v1/vigilances?country={country}")
        answers[country] = {"status": status, "warnings": len(body["vigilances"]) if body else None}
    status, body = get(api, "/v1/vigilances?source=tsunami")
    answers["tsunami"] = {"status": status, "warnings": len(body["vigilances"]) if body else None}
    _, stats = get(api, "/v1/vigilances/archive/stats?days=3")
    return {"utc": int(started), "seconds": round(time.time() - started, 1), "answers": answers, "stats": stats}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--api", default="http://127.0.0.1:8091", help="Address of the instance")
    parser.add_argument("--hours", type=float, default=24.0, help="How long to measure")
    parser.add_argument("--every", type=int, default=600, help="Seconds between two rounds (at least 180: a MeteoAlarm feed is kept 180 s)")
    parser.add_argument("--out", default="measure_archive.jsonl", help="File the rounds are appended to (JSON lines)")
    args = parser.parse_args()
    every = max(180, args.every)
    end = time.time() + args.hours * 3600
    while True:
        result = one_round(args.api)
        with open(args.out, "a", encoding="utf-8") as out:
            out.write(json.dumps(result, separators=(",", ":")) + "\n")
        stats = result["stats"] or {}
        print(
            f"{time.strftime('%Y-%m-%d %H:%M:%S')} {len(result['answers'])} answers in {result['seconds']} s, "
            f"archive {stats.get('warnings')} warnings, database {stats.get('database_bytes')} bytes",
            flush=True,
        )
        wait = every - (time.time() - result["utc"])
        if time.time() + max(0.0, wait) > end:
            break
        time.sleep(max(0.0, wait))


if __name__ == "__main__":
    main()
