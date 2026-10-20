"""A fake Vigie API instance, to try the Home Assistant integration with a warning you choose.

``POST /v1/simulate`` of the real API injects hazards (earthquakes, tsunamis...), not official warnings, so it cannot
make ``/v1/vigilances`` say anything. This script serves the two routes the integration reads (``/v1/health`` and
``/v1/vigilances``) from memory, with the warnings you add and remove by hand. **Everything it says is labelled as a
simulation** (the credit, the event and the headline), and it never talks to a provider.

Usage (from the repository root, standard library only)::

    python tools/fake_instance.py                      # port 8099, all interfaces
    python tools/fake_instance.py --port 8099 --host 127.0.0.1

    # PowerShell 5.1: add a yellow wind warning, raise it, end it
    Invoke-RestMethod -Method Post "http://localhost:8099/fake/set?phenomenon=wind&level=YELLOW"
    Invoke-RestMethod -Method Post "http://localhost:8099/fake/set?phenomenon=wind&level=ORANGE"
    Invoke-RestMethod -Method Post "http://localhost:8099/fake/remove?phenomenon=wind"
    Invoke-RestMethod -Method Post "http://localhost:8099/fake/clear"
    Invoke-RestMethod "http://localhost:8099/fake"      # what it serves now

Point a second entry of the integration at it (another name, any place), watch its ``event`` entity, then delete that
entry. The real instance and the real warnings are not touched. It has no authentication: run it on a trusted network.
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional
from urllib.parse import parse_qs, urlsplit

# The phenomena and levels of the API (app/models/vigilance.py, app/models/schemas.py): a test keeps them equal.
PHENOMENA = ("wind", "rain", "thunderstorm", "snow-ice", "heat", "cold", "flood", "avalanche", "coastal", "fire", "fog", "other")
LEVELS = ("YELLOW", "ORANGE", "RED")

SIMULATION = "SIMULATION"
ATTRIBUTION = "SIMULATION: this is not a real warning (fake instance of the Vigie Home Assistant integration tests)"
DURATION_S = 6 * 3600


class Scenario:
    """The simulated warnings: one per phenomenon. Setting one again gives it a new identifier, as MeteoAlarm does."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, dict[str, Any]] = {}
        self._counter = 0

    def set(self, phenomenon: str, level: str, now: Optional[int] = None) -> dict[str, Any]:
        """Add or replace the warning of a phenomenon."""
        if phenomenon not in PHENOMENA:
            raise ValueError(f"phenomenon must be one of {', '.join(PHENOMENA)}")
        if level not in LEVELS:
            raise ValueError(f"level must be one of {', '.join(LEVELS)}")
        now = int(time.time()) if now is None else now
        with self._lock:
            self._counter += 1
            item = _warning(self._counter, phenomenon, level, now)
            self._items[phenomenon] = item
            return item

    def remove(self, phenomenon: str) -> bool:
        """End the warning of a phenomenon; False when there was none."""
        with self._lock:
            return self._items.pop(phenomenon, None) is not None

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def warnings(self) -> list[dict[str, Any]]:
        """The warnings now, highest level first (the order of the real API)."""
        with self._lock:
            return sorted(self._items.values(), key=lambda w: (-LEVELS.index(w["level"]), w["id"]))


def _warning(number: int, phenomenon: str, level: str, now: int) -> dict[str, Any]:
    title = f"{level.title()} {phenomenon} warning ({SIMULATION})"
    return {
        "id": f"fake:{number}",
        "source": "meteoalarm",
        "country": "FR",
        "attribution": ATTRIBUTION,
        "identifier": f"{SIMULATION}-{number}",
        "sent_utc": now,
        "effective_utc": now,
        "onset_utc": now - 60,
        "expires_utc": now + DURATION_S,
        "ends_utc": None,
        "message_type": "Alert",
        "status": "new",
        "level": level,
        "level_original": level.title(),
        "phenomenon": phenomenon,
        "phenomenon_original": phenomenon.title(),
        "event": title,
        "issuer": SIMULATION,
        "texts": [
            {"language": "fr-FR", "sender_name": SIMULATION, "event": title, "headline": f"Vigilance {level.lower()} {phenomenon} ({SIMULATION})"},
            {"language": "en-GB", "sender_name": SIMULATION, "event": title, "headline": title},
        ],
        "area": {
            "description": f"Simulated area ({SIMULATION})",
            "geocodes": [{"scheme": SIMULATION, "value": "SIM-1"}],
            "has_polygon": False,
            "localization": "geocode",
        },
        "distance_km": None,
        "link": None,
        "web": None,
        "derived": ["status", "level", "phenomenon"],
    }


def health(now: Optional[int] = None) -> dict[str, Any]:
    """What ``GET /v1/health`` says: a healthy instance without a scheduler (so it never pushes: the integration polls it)."""
    return {
        "status": "ok",
        "version": "fake-instance",
        "server_utc": int(time.time()) if now is None else now,
        "scheduler": {"running": False},
        "simulation": True,
    }


def vigilances(scenario: Scenario, lat: float, lon: float, river_radius_km: float, now: Optional[int] = None) -> dict[str, Any]:
    """What ``GET /v1/vigilances`` says for a point: the simulated warnings, whatever the point."""
    found = scenario.warnings()
    return {
        "version": "1.0",
        "server_utc": int(time.time()) if now is None else now,
        "query": {"lat": lat, "lon": lon, "country": None, "river_radius_km": river_radius_km},
        "countries": ["FR"],
        "vigilances": found,
        "coverage": [
            {
                "source": "meteoalarm",
                "country": "FR",
                "status": "ok",
                "warnings_in_feed": len(found),
                "warnings_not_matchable": 0,
                "note": ATTRIBUTION,
            }
        ],
        "attribution": ATTRIBUTION,
        "disclaimer": None,
        "note": None,
    }


def make_handler(scenario: Scenario) -> type[BaseHTTPRequestHandler]:
    """The request handler of a server that serves ``scenario``."""

    class Handler(BaseHTTPRequestHandler):
        server_version = "FakeVigie"

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - the signature of the base class
            print(f"{self.address_string()} {format % args}")

        def _send(self, status: int, body: Any) -> None:
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _route(self, method: str) -> None:
            url = urlsplit(self.path)
            query = {key: values[0] for key, values in parse_qs(url.query).items()}
            try:
                if method == "GET" and url.path == "/v1/health":
                    return self._send(200, health())
                if method == "GET" and url.path == "/v1/vigilances":
                    lat, lon = float(query["lat"]), float(query["lon"])
                    return self._send(200, vigilances(scenario, lat, lon, float(query.get("river_radius_km", 5.0))))
                if method == "GET" and url.path == "/fake":
                    return self._send(200, {"warnings": [{key: w[key] for key in ("id", "level", "phenomenon", "event")} for w in scenario.warnings()]})
                if method == "POST" and url.path == "/fake/set":
                    return self._send(200, scenario.set(query.get("phenomenon", ""), query.get("level", "").upper()))
                if method == "POST" and url.path == "/fake/remove":
                    return self._send(200, {"removed": scenario.remove(query.get("phenomenon", ""))})
                if method == "POST" and url.path == "/fake/clear":
                    scenario.clear()
                    return self._send(200, {"warnings": []})
            except (KeyError, ValueError) as err:
                return self._send(422, {"detail": f"bad request: {err}"})
            self._send(404, {"detail": "not found"})

        def do_GET(self) -> None:  # noqa: N802 - the names of the base class
            self._route("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._route("POST")

    return Handler


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Serve a fake Vigie API instance whose warnings you add and remove by hand.")
    parser.add_argument("--host", default="0.0.0.0", help="address to listen on (default: all interfaces)")
    parser.add_argument("--port", type=int, default=8099, help="port (default: 8099)")
    args = parser.parse_args(argv)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(Scenario()))
    print(f"Fake Vigie instance on http://{args.host}:{args.port} (every warning is labelled {SIMULATION}). Ctrl+C stops it.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
