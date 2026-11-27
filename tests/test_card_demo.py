"""The online demo of the Home Assistant card (`demo/`, built by `tools/build_card_demo.py`, published by `pages.yml`).

No network: the recorded answers must keep the shape the integration gives the card (`websocket.py`, read as text because it
needs Home Assistant to load), each place of the recorder must have its answers, and the built site must hold every file its
page asks for, with the card copied unchanged.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import re
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "demo"
WEBSOCKET = ROOT / "custom_components" / "vigie" / "websocket.py"
WEATHER_DATA = ROOT / "custom_components" / "vigie" / "weather_data.py"
CARD = ROOT / "custom_components" / "vigie" / "frontend" / "vigie-card.js"


def _tool(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


recorder = _tool("record_card_demo")
builder = _tool("build_card_demo")


def _function(name: str, path: Path = WEBSOCKET) -> ast.AST:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in {path.name}")


def _keys(node: ast.Dict) -> set[str]:
    return {key.value for key in node.keys if isinstance(key, ast.Constant) and isinstance(key.value, str)}


def _dict_with(node: ast.AST, key: str) -> ast.Dict:
    """The first dict literal under `node` that has `key`."""
    for child in ast.walk(node):
        if isinstance(child, ast.Dict) and key in _keys(child):
            return child
    raise AssertionError(f"no dict with the key {key!r}")


def _records() -> dict[str, dict[str, Any]]:
    return {path.stem: json.loads(path.read_text(encoding="utf-8")) for path in sorted((DEMO / "data").glob("*.json"))}


def test_each_place_of_the_recorder_has_its_answers_and_no_other_file_is_there() -> None:
    records = _records()
    assert sorted(records) == sorted(place["id"] for place in recorder.PLACES)
    for place in recorder.PLACES:
        assert records[place["id"]]["place"] == place, (
            f"{place['id']}: record it again with `python tools/record_card_demo.py`"
        )


def test_the_page_offers_exactly_the_recorded_places() -> None:
    offered = re.findall(r"\bid: '([a-z]+)'", (DEMO / "demo.js").read_text(encoding="utf-8"))
    assert sorted(offered) == sorted(_records())


@pytest.mark.parametrize("place", [place["id"] for place in recorder.PLACES])
def test_a_recorded_answer_has_the_shape_the_integration_gives_the_card(place: str) -> None:
    record = _records()[place]
    answer = record["answer"]
    card_data = _function("ws_card_data")
    expected = _keys(_dict_with(card_data, "entry_id"))
    assert set(answer) == expected, (
        f"vigie/card/data changed ({sorted(expected ^ set(answer))}): update the answer in tools/record_card_demo.py, "
        "then record again with `python tools/record_card_demo.py`"
    )
    if answer["warnings"] is not None:
        assert set(answer["warnings"]) == _keys(_dict_with(card_data, "max_level"))
        for language in recorder.LANGUAGES:
            assert isinstance(record["vigilances"][language], list)
    quakes = _function("_quakes")
    if answer["earthquakes"] is not None:
        assert set(answer["earthquakes"]) == _keys(_dict_with(quakes, "count"))
        for quake in answer["earthquakes"]["earthquakes"]:
            assert set(quake) == _keys(_dict_with(quakes, "magnitude"))
    # Recorded, not made up: from an instance, at a known time, with a forecast in it.
    assert record["instance"].startswith("https://") or record["instance"].startswith("http://localhost")
    assert isinstance(record["recorded_utc"], int) and record["recorded_utc"] > 1_700_000_000
    assert answer["point"] is not None and answer["read_utc"] is not None


def test_each_station_has_its_answer_with_the_shape_the_integration_gives_the_mountain_page() -> None:
    expected = _keys(_dict_with(_function("async_mountain", WEATHER_DATA), "elevation_ignored"))
    stations_seen = 0
    for record in _records().values():
        stations = record["place"].get("stations", [])
        assert len(record["mountain"]) == len(stations)
        for station, answer in zip(stations, record["mountain"], strict=True):
            stations_seen += 1
            assert set(answer) == expected, (
                f"vigie/card/mountain changed ({sorted(expected ^ set(answer))}): update mountain_station in "
                "tools/record_card_demo.py, then record again"
            )
            # Read at the altitudes asked: the top and the base are not the same weather.
            assert answer["elevation_ignored"] is False
            assert answer["top"]["coordinates"]["elevation_m"] == station["top_m"]
            if station.get("base_m") is not None:
                assert answer["base"]["coordinates"]["elevation_m"] == station["base_m"]
    assert stations_seen, "no place has a station: the Mountain page is not in the demo"


def test_each_beach_has_its_answer_with_the_shape_the_integration_gives_the_sea_page() -> None:
    expected = _keys(_dict_with(_function("async_beach", WEATHER_DATA), "bathing"))
    beaches_seen = 0
    for record in _records().values():
        beaches = record["place"].get("beaches", [])
        answers = record.get("beaches", [])
        assert len(answers) == len(beaches)
        listed = {spot["id"] for spot in record.get("spots", [])}
        for spot_id, answer in zip(beaches, answers, strict=True):
            beaches_seen += 1
            assert set(answer) == expected, (
                f"vigie/card/beach changed ({sorted(expected ^ set(answer))}): update beach_answer in "
                "tools/record_card_demo.py, then record again"
            )
            # A spot near the place, as `vigie/card/spots` lists it (the name of its tab), with its hours from the one under
            # way at the time of the record and its verdicts.
            assert answer["spot"]["id"] == spot_id and spot_id in listed
            hours = answer["forecast"]["hours"]
            assert hours and all(hour["time_utc"] >= record["recorded_utc"] - 3600 for hour in hours)
            assert answer["activities"]["activities"]
    assert beaches_seen, "no place has a beach: the demo shows the Sea page of before"


def test_the_beaches_are_what_the_card_reads() -> None:
    found = re.search(r"const MAX_BEACHES = (\d+);", CARD.read_text(encoding="utf-8"))
    assert found, "MAX_BEACHES not found in vigie-card.js"
    max_beaches = int(found.group(1))
    for place in recorder.PLACES:
        beaches = place.get("beaches", [])
        assert all(isinstance(b, str) and b for b in beaches)
        assert len(set(beaches)) == len(beaches) <= max_beaches


def test_the_stations_are_what_the_card_reads() -> None:
    for place in recorder.PLACES:
        for station in place.get("stations", []):
            assert set(station) <= {"name", "latitude", "longitude", "base_m", "top_m"}
            assert -90 <= station["latitude"] <= 90 and -180 <= station["longitude"] <= 180
            assert isinstance(station["top_m"], int) and station["top_m"] > station.get("base_m", 0)


def test_the_spot_of_a_record_is_within_the_distance_the_card_uses() -> None:
    for record in _records().values():
        spot = record["answer"]["spot"]
        if record["place"]["spot"] == "auto" and spot is not None:
            assert spot["distance_km"] <= recorder.auto_spot_km()
        if record["place"]["spot"] == "none":
            assert spot is None and record["answer"]["activities"] is None


def test_the_built_site_holds_every_file_its_page_asks_for(tmp_path: Path) -> None:
    site = tmp_path / "_site"
    builder.build(site)
    page = (site / "index.html").read_text(encoding="utf-8")
    local = [ref for ref in re.findall(r'(?:src|href)="([^"]+)"', page) if not re.match(r"[a-z]+:", ref)]
    assert local, "the page asks for no local file"
    for ref in local:
        assert (site / ref).is_file(), ref
    script = (site / "demo.js").read_text(encoding="utf-8")
    assert "import('./vigie-card.js')" in script
    assert (site / "vigie-card.js").read_bytes() == CARD.read_bytes()
    for place in recorder.PLACES:
        assert (site / "data" / f"{place['id']}.json").is_file()
    assert (site / ".nojekyll").is_file()


def test_the_page_asks_nothing_of_an_instance() -> None:
    script = (DEMO / "demo.js").read_text(encoding="utf-8")
    assert re.findall(r"fetch\(([^)]*)\)", script) == ["`data/${id}.json`"]
    assert "https://" not in script
