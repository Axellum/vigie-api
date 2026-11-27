"""Record the answers of the online demo of the Home Assistant card (`demo/`, published on GitHub Pages).

    python tools/record_card_demo.py                     # the public instance
    python tools/record_card_demo.py --base http://localhost:8080

For each place of `PLACES` (public places: a beach, a mountain town, a city centre; never anyone's home), it asks the instance
what the integration asks for the card (the same routes, the same parameters: the integration's own client, `api.py`), puts the
answers together as the WebSocket command `vigie/card/data` gives them to the card (`websocket.py`), and writes
`demo/data/<id>.json`. A place with `stations` also gets, for each, what `vigie/card/mountain` gives the Mountain page (the
weather at its top and at its base, each at its own altitude, and its avalanche bulletin). The demo page then plays the part of
Home Assistant and sets the card's clock to the time of the read.

The integration's modules are loaded from `custom_components/vigie` as they are, without Home Assistant: `api.py`, `const.py`,
`models.py` and `quakes.py` do not import it; for `weather_data.py` (whose helpers choose the nearest station and spots and
trim the answers of the Mountain page), the few names it imports from Home Assistant and astral are stand-ins that are never
called. The small parts that need Home Assistant to load (the answer's shape and the earthquakes' summary of `websocket.py`,
the station's shape of `weather_data.py`) are written again here, and `tests/test_card_demo.py` checks their keys against those
files. Needs aiohttp and yarl (Home Assistant's own client libraries): `pip install aiohttp yarl`. An instance that ignores
the altitude of `GET /v1/point?elevation=` is refused: its top and base would be the same weather.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import importlib.util
import json
import sys
import time
import types
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
INTEGRATION = ROOT / "custom_components" / "vigie"
OUT = ROOT / "demo" / "data"
PUBLIC_INSTANCE = "https://vigie.auxfilsdesidees.fr"
LANGUAGES = ("fr", "en")
# `GET /v1/point` costs the instance calls to a provider: the public one allows 10 a minute per address.
POINT_SPACING_S = 7
POINT_RETRY_S = 20
POINT_TRIES = 4

# Public places only. `spot`: what the card is set to (`auto`: the nearest spot within 20 km, as the editor offers).
# `stations`: the Mountain page's, as the card's editor writes them. Each is a village at the foot of a public lift, with the
# altitude of the summit it climbs to; positions and altitudes from GeoNames (Open-Meteo's geocoding API, read on 2026-11-27).
PLACES: tuple[dict[str, Any], ...] = (
    {"id": "biarritz", "name": "Biarritz", "lat": 43.4832, "lon": -1.5586, "spot": "auto"},
    {
        "id": "chamonix",
        "name": "Chamonix",
        "lat": 45.9237,
        "lon": 6.8694,
        "spot": "none",
        "stations": [
            # Chamonix-Mont-Blanc (1,060 m) and Le Brévent (2,524 m)
            {"name": "Brévent", "latitude": 45.9238, "longitude": 6.8693, "base_m": 1060, "top_m": 2524},
            # Argentière (1,245 m) and the Aiguille des Grands Montets (3,295 m)
            {"name": "Grands Montets", "latitude": 45.9826, "longitude": 6.9268, "base_m": 1245, "top_m": 3295},
        ],
    },
    {"id": "tokyo", "name": "Tokyo", "lat": 35.6812, "lon": 139.7671, "spot": "none"},
)

PACKAGE = "vigie_integration"


def _stand_ins() -> None:
    """The names `weather_data.py` imports from Home Assistant and astral, for loading it without them (never called here)."""

    class _Generic:
        def __class_getitem__(cls, _item: Any) -> type:
            return cls

    def module(name: str, **names: Any) -> None:
        if name not in sys.modules:
            stand_in = types.ModuleType(name)
            stand_in.__dict__.update(names)
            sys.modules[name] = stand_in

    module("astral", Observer=object)
    module("astral.sun", elevation=lambda *_a, **_k: None)
    module("homeassistant")
    module("homeassistant.config_entries", ConfigEntry=_Generic)
    module("homeassistant.const", CONF_LATITUDE="latitude", CONF_LONGITUDE="longitude")
    module("homeassistant.core", HomeAssistant=object)
    module("homeassistant.helpers")
    module("homeassistant.helpers.update_coordinator", DataUpdateCoordinator=_Generic, UpdateFailed=Exception)


def _load(name: str) -> types.ModuleType:
    """One module of the integration, loaded from its file (the package's `__init__.py`, which needs Home Assistant, is not run)."""
    if PACKAGE not in sys.modules:
        package = types.ModuleType(PACKAGE)
        package.__path__ = [str(INTEGRATION)]
        sys.modules[PACKAGE] = package
    full = f"{PACKAGE}.{name}"
    if full in sys.modules:
        return sys.modules[full]
    spec = importlib.util.spec_from_file_location(full, INTEGRATION / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[full] = module
    spec.loader.exec_module(module)
    return module


def auto_spot_km() -> float:
    """`AUTO_SPOT_KM` of `websocket.py`, read from the file (the module needs Home Assistant to load)."""
    tree = ast.parse((INTEGRATION / "websocket.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "AUTO_SPOT_KM" for t in node.targets
        ):
            return float(ast.literal_eval(node.value))
    raise SystemExit("AUTO_SPOT_KM not found in websocket.py")


def quakes_summary(data: Any) -> dict[str, Any]:
    """`_quakes` of `websocket.py`: the count, the credit and the five strongest earthquakes."""
    return {
        "count": data.count,
        "attribution": data.attribution,
        "earthquakes": [
            {
                "title": quake.title,
                "magnitude": quake.magnitude,
                "time_utc": quake.time_utc,
                "distance_km": quake.distance_km,
                "bearing_cardinal": quake.bearing_cardinal,
                "depth_km": quake.depth_km,
                "tsunami_warning": quake.tsunami_warning,
            }
            for quake in data.quakes[:5]
        ],
    }


def mountain_station(
    weather_data: Any, top: Any, base: Any, avalanches: Any, station: dict[str, Any]
) -> dict[str, Any]:
    """`async_mountain` of `weather_data.py`, after its three reads: the top, the base and the bulletin, trimmed by its helpers."""
    top_point = weather_data.mountain_point(top, station["top_m"])
    base_point = weather_data.mountain_point(base, station.get("base_m"))
    return {
        "top": top_point,
        "base": base_point,
        "avalanches": weather_data.mountain_avalanches(avalanches),
        "elevation_ignored": (top_point is None and isinstance(top, dict) and isinstance(top.get("daily"), list))
        or (
            base_point is None
            and station.get("base_m") is not None
            and isinstance(base, dict)
            and isinstance(base.get("daily"), list)
        ),
    }


async def record(base: str, place: dict[str, Any]) -> dict[str, Any]:
    import aiohttp  # noqa: PLC0415 - only this tool needs it

    api, const, models, quakes = _load("api"), _load("const"), _load("models"), _load("quakes")
    _stand_ins()
    weather_data = _load("weather_data")
    lat, lon = place["lat"], place["lon"]

    async def optional(read: Any) -> Any:
        """A part the integration keeps as "nothing there" when the instance answers 404 (the tide inland)."""
        try:
            return await read
        except api.VigieNotFoundError:
            return None

    headers = {"User-Agent": "vigie-api card demo recorder (+https://github.com/Axellum/vigie-api)"}
    async with aiohttp.ClientSession(headers=headers) as session:
        client = api.VigieClient(session, base)

        async def read_point(*args: Any, **kwargs: Any) -> Any:
            """`GET /v1/point` within the limit of the public instance for one address (10 a minute, 120 an hour, shared
            with whatever else reads it from the same address): spaced, and asked again a little later after a 429."""
            for attempt in range(POINT_TRIES):
                await asyncio.sleep(POINT_SPACING_S if attempt == 0 else POINT_RETRY_S)
                try:
                    return await client.point(*args, **kwargs)
                except api.VigieResponseError as err:
                    if "HTTP 429" not in str(err) or attempt == POINT_TRIES - 1:
                        raise
                    print(f"  429 from {base}: asking again in {POINT_RETRY_S} s")
            raise AssertionError("unreachable")

        point = await read_point(lat, lon)
        read_utc = int(time.time())
        tides = await optional(client.tides(lat, lon))
        normals = await optional(client.climate_normals(lat, lon))
        station = forest_fire = None
        if isinstance(normals, dict) and normals.get("status") == "available":
            forest_fire = await optional(client.forest_fire(lat, lon))
            box = const.STATION_BOX_DEG
            station = weather_data.nearest_station(
                await client.station_wind(lon - box, lat - box, lon + box, lat + box), lat, lon
            )
        warnings = models.parse_response(await client.vigilances(lat, lon, const.DEFAULT_RIVER_RADIUS_KM))
        quake_data = quakes.parse_earthquakes(
            await client.earthquakes(
                lat, lon, const.DEFAULT_QUAKE_RADIUS_KM, const.DEFAULT_QUAKE_MIN_MAGNITUDE, const.QUAKE_WINDOW_HOURS
            ),
            const.DEFAULT_QUAKE_MIN_MAGNITUDE,
        )
        spot = activities = None
        if place["spot"] == "auto":
            spots = weather_data.nearby_spots(await client.surf_spots(), lat, lon)
            if spots and spots[0]["distance_km"] <= auto_spot_km():
                spot = spots[0]
                activities = await optional(client.activities(spot["id"]))
        mountain = []
        for station in place.get("stations", []):
            s_lat, s_lon = station["latitude"], station["longitude"]
            answer_of_station = mountain_station(
                weather_data,
                await optional(read_point(s_lat, s_lon, elevation_m=station["top_m"], marine=False)),
                await optional(read_point(s_lat, s_lon, elevation_m=station.get("base_m"), marine=False)),
                await optional(client.avalanches(s_lat, s_lon)),
                station,
            )
            if answer_of_station["elevation_ignored"] or answer_of_station["top"] is None:
                raise SystemExit(
                    f"{base} ignores the altitude of GET /v1/point?elevation=: deploy a newer vigie-api first"
                )
            mountain.append(answer_of_station)

    def answer(language: str) -> dict[str, Any]:
        """What `vigie/card/data` gives the card, in one language (only the warnings' texts depend on it)."""
        return {
            "entry_id": f"demo-{place['id']}",
            "title": place["name"],
            "language": language,
            "read_utc": read_utc,
            "point": point,
            "tides": tides if isinstance(tides, dict) else None,
            "normals": normals if isinstance(normals, dict) else None,
            "station_wind": station,
            "forest_fire": forest_fire if isinstance(forest_fire, dict) else None,
            "warnings": {
                "max_level": warnings.max_level,
                "server_utc": warnings.server_utc,
                "note": warnings.note,
                "attribution": warnings.attribution,
                "vigilances": [vigilance.summary(language) for vigilance in warnings.vigilances],
            },
            "earthquakes": quakes_summary(quake_data),
            "spot": spot,
            "activities": activities if isinstance(activities, dict) else None,
        }

    first, *others = LANGUAGES
    recorded = answer(first)
    return {
        "place": place,
        "recorded_utc": read_utc,
        "instance": base,
        "answer": recorded,
        # The warnings' texts in each language; the rest of the answer is the same in every language.
        "vigilances": {language: answer(language)["warnings"]["vigilances"] for language in (first, *others)},
        # What `vigie/card/mountain` gives for each station of the place, in their order (without its `language`).
        "mountain": mountain,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--base", default=PUBLIC_INSTANCE, help=f"the Vigie instance (default {PUBLIC_INSTANCE})")
    parser.add_argument("--only", help="the id of one place")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    for place in PLACES:
        if args.only and place["id"] != args.only:
            continue
        data = asyncio.run(record(args.base.rstrip("/"), place))
        path = OUT / f"{place['id']}.json"
        path.write_text(
            json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8", newline="\n"
        )
        answer = data["answer"]
        print(
            f"{place['id']}: {path.stat().st_size // 1024} KiB, read at {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(data['recorded_utc']))},"
            f" tide {'yes' if answer['tides'] else 'no'}, normals {'yes' if answer['normals'] else 'no'},"
            f" spot {answer['spot']['name'] if answer['spot'] else 'none'}, warnings {answer['warnings']['max_level']},"
            f" earthquakes {answer['earthquakes']['count']}"
        )
        for station, kept in zip(place.get("stations", []), data["mountain"], strict=True):
            avalanches = kept["avalanches"] or {}
            print(
                f"  {station['name']}: top {kept['top']['coordinates'].get('elevation_m')} m,"
                f" base {(kept['base'] or {}).get('coordinates', {}).get('elevation_m')} m,"
                f" avalanches {avalanches.get('status')} {avalanches.get('max_danger_level')}"
            )


if __name__ == "__main__":
    main()
