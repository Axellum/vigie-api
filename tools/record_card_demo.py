"""Record the answers of the online demo of the Home Assistant card (`demo/`, published on GitHub Pages).

    python tools/record_card_demo.py                     # the public instance
    python tools/record_card_demo.py --base http://localhost:8080

For each place of `PLACES` (public places: a beach, a mountain town, a city centre; never anyone's home), it asks the instance
what the integration asks for the card (the same routes, the same parameters: the integration's own client, `api.py`), puts the
answers together as the WebSocket command `vigie/card/data` gives them to the card (`websocket.py`), and writes
`demo/data/<id>.json`. The demo page then plays the part of Home Assistant and sets the card's clock to the time of the read.

The integration's modules are loaded from `custom_components/vigie` as they are, without Home Assistant: `api.py`, `const.py`,
`models.py` and `quakes.py` do not import it; for `weather_data.py` (whose two helpers choose the nearest station and spots),
the few names it imports from Home Assistant and astral are stand-ins that are never called. The two small parts of
`websocket.py` that need Home Assistant to load (the answer's shape, the earthquakes' summary) are written again here, and
`tests/test_card_demo.py` checks their keys against that file. Needs aiohttp and yarl (Home Assistant's own client libraries):
`pip install aiohttp yarl`.
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

# Public places only. `spot`: what the card is set to (`auto`: the nearest spot within 20 km, as the editor offers).
PLACES: tuple[dict[str, Any], ...] = (
    {"id": "biarritz", "name": "Biarritz", "lat": 43.4832, "lon": -1.5586, "spot": "auto"},
    {"id": "chamonix", "name": "Chamonix", "lat": 45.9237, "lon": 6.8694, "spot": "none"},
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
        point = await client.point(lat, lon)
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
        "place": {key: place[key] for key in ("id", "name", "lat", "lon", "spot")},
        "recorded_utc": read_utc,
        "instance": base,
        "answer": recorded,
        # The warnings' texts in each language; the rest of the answer is the same in every language.
        "vigilances": {language: answer(language)["warnings"]["vigilances"] for language in (first, *others)},
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


if __name__ == "__main__":
    main()
