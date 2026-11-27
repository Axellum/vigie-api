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

With ``--replay tests_ha/fixtures`` it also serves the routes of the option "Weather and card" (ADR 0042): the real answers
recorded for Biarritz on 2026-11-22 (the weather, the tide, the normals, the wind measured, the spots, the warnings in force
then, and the earthquakes near it, read later that day), and for one spot, the Plage des Corsaires, on 2026-11-27 (its verdicts,
its forecast hour by hour and the bathing sites near it: a beach of the card's Sea page), every time in them moved by as much
as the clock moved since its read, so "now" falls where it fell. A station of the card's Mountain page gets the point recorded at
Chamonix that day, simulated at the altitude it asks (``elevation``: see ``at_altitude``), and the avalanche bulletin recorded
there on 2026-11-27. This is how the dashboard card is tried in a real Home
Assistant, and the integration installed in a fresh one (the ``card`` and ``install`` jobs of the workflow), with nothing
left unanswered. What it serves is a replay of an old read, not today's weather: say so wherever it is shown.
"""

from __future__ import annotations

import argparse
import copy
import datetime
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
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


# The recorded answers of a replay (the files of ``tests_ha/fixtures``, see its README), by route.
REPLAY_ROUTES = {
    "/v1/point": "point_biarritz_2026-11-22.json",
    "/v1/tides": "tides_biarritz_2026-11-22.json",
    "/v1/climate/normals": "normals_biarritz_2026-11-22.json",
    "/v1/stations/wind": "stationwind_biarritz_2026-11-22.json",
    "/v1/surf/spots": "spots_2026-11-22.json",
    "/v1/activities/biarritz-plage-des-corsaires": "activities_biarritz-plage-des-corsaires_2026-11-27.json",
    "/v1/surf/spots/biarritz-plage-des-corsaires": "surfspot_biarritz-plage-des-corsaires_2026-11-27.json",
    "/v1/sea/places": "seaplaces_biarritz-plage-des-corsaires_2026-11-27.json",
    "/v1/earthquakes": "earthquakes_biarritz_2026-11-22.json",
    "/v1/forest-fire": "forestfire_biarritz_2026-11-22.json",
    "/v1/avalanches": "avalanches_chamonix_2026-11-27.json",
}
REPLAY_VIGILANCES = "vigilances_biarritz_2026-11-22.json"
# A route read apart from the others: moved from its own time of reading (the key that holds it), not from the point's.
# The bulletin writes its times as texts (``2026-11-27T17:00:00Z``): they are moved, from its own read, as texts.
REPLAY_OWN_READ = {
    "/v1/earthquakes": "generated_utc",
    "/v1/forest-fire": "checked_at_utc",
    "/v1/avalanches": "server_utc",
    # The beach of the Sea page: its forecast and its verdicts, read together, keep their tides in step.
    "/v1/activities/biarritz-plage-des-corsaires": "generated_utc",
    "/v1/surf/spots/biarritz-plage-des-corsaires": "generated_utc",
}
# The point a station of the Mountain page asks at an altitude (``GET /v1/point?elevation=``).
REPLAY_MOUNTAIN = "point_chamonix_2026-11-22.json"
LAPSE_C_PER_M = 0.0065  # the standard atmosphere: 6.5 °C less for each 1 000 m higher
REFERENCE_M = 1000  # the altitude the replay takes the recorded temperatures for: its own choice, not a reading
ISO_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")


def _moved(value: Any, seconds: int, days: int, dated: bool = False) -> Any:
    """``value`` with every epoch (a key ending in ``_utc``) moved by ``seconds``, and the dates of the days of the weather
    (``date`` under ``daily``) by ``days``; the other dates (a record, a sheet) are left as they were."""
    if isinstance(value, dict):
        moved: dict[str, Any] = {}
        for key, item in value.items():
            if key.endswith("_utc") and isinstance(item, int) and not isinstance(item, bool):
                moved[key] = item + seconds
            elif dated and key == "date" and isinstance(item, str):
                moved[key] = (datetime.date.fromisoformat(item) + datetime.timedelta(days=days)).isoformat()
            else:
                moved[key] = _moved(item, seconds, days, dated or key == "daily")
        return moved
    if isinstance(value, list):
        return [_moved(item, seconds, days, dated) for item in value]
    return value


def _iso(value: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))


def _moved_texts(value: Any, seconds: int) -> Any:
    """``value`` with every time written as a text in UTC (``2026-11-27T17:00:00Z``) moved by ``seconds``."""
    if isinstance(value, dict):
        return {key: _moved_texts(item, seconds) for key, item in value.items()}
    if isinstance(value, list):
        return [_moved_texts(item, seconds) for item in value]
    if isinstance(value, str) and ISO_TIME.match(value):
        return (_iso(value) + datetime.timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return value


def at_altitude(point: dict[str, Any], elevation_m: int) -> dict[str, Any]:
    """``point`` as the API answers it at ``elevation_m`` (``GET /v1/point?elevation=``), simulated: its temperatures moved by
    the standard lapse rate from ``REFERENCE_M``, and the freezing level of each hour where that rate brings its temperature to
    0 °C. A simulation for the Mountain page of the card, not the weather up there."""
    shift = -LAPSE_C_PER_M * (elevation_m - REFERENCE_M)
    point["coordinates"]["elevation_m"] = elevation_m
    if isinstance((current := point.get("current")), dict) and isinstance(current.get("temp_c"), (int, float)):
        current["temp_c"] = round(current["temp_c"] + shift, 1)
    for hour in point.get("hourly") or []:
        if isinstance(hour.get("temp_c"), (int, float)):
            hour["freezing_level_m"] = max(0, int(round(REFERENCE_M + hour["temp_c"] / LAPSE_C_PER_M, -1)))
            hour["temp_c"] = round(hour["temp_c"] + shift, 1)
    for day in point.get("daily") or []:
        for key in ("tmax", "tmin"):
            if isinstance(day.get(key), (int, float)):
                day[key] = round(day[key] + shift, 1)
    return point


class Replay:
    """Recorded answers served again, their times moved to now (``--replay``)."""

    def __init__(self, folder: Path) -> None:
        def read(name: str) -> Any:
            return json.loads((folder / name).read_text(encoding="utf-8"))

        self._answers = {route: read(name) for route, name in REPLAY_ROUTES.items()}
        self._vigilances = read(REPLAY_VIGILANCES)
        self._mountain = read(REPLAY_MOUNTAIN)
        point = self._answers["/v1/point"]
        self.read_utc: int = point["server_utc"]
        self._offset: int = point.get("utc_offset_s") or 0

    def _shift(self, now: int, read_utc: Optional[int] = None) -> tuple[int, int]:
        read_utc = self.read_utc if read_utc is None else read_utc
        seconds = now - read_utc
        local = lambda t: datetime.datetime.fromtimestamp(t + self._offset, tz=datetime.timezone.utc).date()  # noqa: E731
        return seconds, (local(now) - local(read_utc)).days

    def answer(self, route: str, now: Optional[int] = None, elevation_m: Optional[int] = None) -> Optional[Any]:
        """The recorded answer of ``route`` moved to ``now``; None when nothing was recorded for it. The point asked at an
        altitude (``elevation_m``) is the one of the Mountain page (``at_altitude``)."""
        now = int(time.time()) if now is None else now
        if route == "/v1/point" and elevation_m is not None:
            return at_altitude(_moved(copy.deepcopy(self._mountain), *self._shift(now, self._mountain["server_utc"])), elevation_m)
        if route not in self._answers:
            return None
        recorded = self._answers[route]
        read = recorded[REPLAY_OWN_READ[route]] if route in REPLAY_OWN_READ else None
        if isinstance(read, str):
            return _moved_texts(copy.deepcopy(recorded), now - int(_iso(read).timestamp()))
        return _moved(copy.deepcopy(recorded), *self._shift(now, read))

    def vigilances(self, scenario: Scenario, now: Optional[int] = None) -> dict[str, Any]:
        """The warnings recorded then, moved to ``now``, and the simulated ones of ``scenario`` after them."""
        answer = _moved(copy.deepcopy(self._vigilances), *self._shift(int(time.time()) if now is None else now))
        answer["vigilances"] = answer["vigilances"] + scenario.warnings()
        return answer


def make_handler(scenario: Scenario, replay: Optional[Replay] = None) -> type[BaseHTTPRequestHandler]:
    """The request handler of a server that serves ``scenario`` (and the recorded answers of ``replay``, when given)."""

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
                    if replay is not None:
                        return self._send(200, replay.vigilances(scenario))
                    return self._send(200, vigilances(scenario, lat, lon, float(query.get("river_radius_km", 5.0))))
                elevation = int(query["elevation"]) if "elevation" in query else None
                if method == "GET" and replay is not None and (recorded := replay.answer(url.path, elevation_m=elevation)) is not None:
                    return self._send(200, recorded)
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
    parser.add_argument("--replay", type=Path, help="folder of recorded answers (tests_ha/fixtures): also serve the weather routes")
    args = parser.parse_args(argv)
    replay = Replay(args.replay) if args.replay else None
    server = ThreadingHTTPServer((args.host, args.port), make_handler(Scenario(), replay))
    print(f"Fake Vigie instance on http://{args.host}:{args.port} (every warning is labelled {SIMULATION}). Ctrl+C stops it.")
    if replay is not None:
        print(f"Replaying the answers recorded at {replay.read_utc} (UTC epoch), their times moved to now.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
