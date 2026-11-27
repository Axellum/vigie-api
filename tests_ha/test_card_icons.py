"""The weather icons of the card (ADR 0045): the icon set of the Vigie family, in the style chosen in the editor.

The home page is drawn by Node outside a browser (``card_render.cjs``) with the answer recorded at Biarritz on 2026-11-22; the
hours of the curve are built here (the recorded answer has none), labelled as such. That the block of drawings is the one of
``scripts/card_icons.py`` is checked by ``tests/test_card_icons.py``; the look in a real Home Assistant, by the `card` job of the
workflow.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from custom_components.vigie.card import CARD_FILE

from .conftest import POINT, recorded

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed here (it is in CI)")

HARNESS = Path(__file__).with_name("card_render.cjs")
NOON_MS = 1_795_353_120_000  # 2026-11-22 13:12 UTC, just after the read of Biarritz
BOXES = {"meridien": "0 0 128 128", "line": "0 0 24 24", "duo": "0 0 24 24", "badge": "-4 -4 32 32"}


def built_hours(day_hours: int) -> list[dict[str, Any]]:
    """BUILT for the test: twelve hours from 13:00 UTC, broken clouds and showers, the first `day_hours` by day."""
    first = NOON_MS // 1000 // 3600 * 3600
    codes = [2, 2, 2, 80, 80, 3, 2, 2, 80, 2, 2, 2]
    return [{"time_utc": first + i * 3600, "temp_c": 15.0 - i * 0.3, "precipitation_mm": 1.0 if c == 80 else 0.0, "wind_gust_kmh": 50,
             "weather_code": c, "is_day": i < day_hours} for i, c in enumerate(codes)]  # fmt: skip


def render(config: dict[str, Any] | None = None, day_hours: int = 4) -> dict[str, Any]:
    point = recorded(POINT)
    point["hourly"] = built_hours(day_hours)
    data = {"point": point, "warnings": None, "earthquakes": None, "tides": None, "normals": None}
    result = subprocess.run(
        ["node", str(HARNESS), str(CARD_FILE)],
        input=json.dumps({"lang": "fr", "width": 374, "now_ms": NOON_MS, "data": data, "config": config or {}}),
        capture_output=True, text=True, encoding="utf-8", check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def icons(page: str) -> list[str]:
    """The weather icons of a page, the ids of their masks made alike (each drawing numbers its own)."""
    return [re.sub(r"vi\d+-", "vi-", s) for s in re.findall(r'<svg[^>]*fill="currentColor"[^>]*>.*?</svg>', page)]


@pytest.mark.parametrize("style", list(BOXES))
def test_each_style_draws_the_home_page_with_its_own_drawings(style: str) -> None:
    home = render({"icon_style": style})["home"]
    boxes = re.findall(r'<svg[^>]*viewBox="([^"]+)"[^>]*fill="currentColor"', home)
    # the icon of the moment, twelve hours along the curve, the days
    assert len(boxes) >= 1 + 6 + 5 and set(boxes) == {BOXES[style]}
    # each layer is coloured from the palette (the dark theme here); no colour is left to the text
    layers = re.findall(r'<g class="(v[a-z])"( color="#[0-9A-F]{6}")?', home)
    assert layers and all(colour for _, colour in layers)
    assert ' color="#FFC83D"' in home  # the sun of the dark theme, behind the clouds of the moment
    # the masks: no id left to fill, and never twice the same in a page
    ids = re.findall(r'<mask id="([^"]+)"', home)
    assert "@" not in home and ids and len(ids) == len(set(ids))
    assert all(f'url(#{i})' in home for i in ids)


def test_the_screens_drawings_by_default_and_for_a_style_it_does_not_know() -> None:
    default = icons(render()["home"])
    assert len(default) >= 12 and all('viewBox="0 0 128 128"' in s for s in default)
    assert icons(render({"icon_style": "meridien"})["home"]) == default
    assert icons(render({"icon_style": "neon"})["home"]) == default


@pytest.mark.parametrize("style", list(BOXES))
def test_the_hours_of_the_night_show_the_moon(style: str) -> None:
    # the moment (no is_day in the recorded answer) and the days are drawn by day: a moon only for the hours of the night
    assert 'class="vl"' not in render({"icon_style": style}, day_hours=12)["home"]
    assert 'class="vl" color="#EFE9CF"' in render({"icon_style": style}, day_hours=4)["home"]


# The icon of each WMO weather code on the Meridien screen (meteo_wmo.hpp of its repository): (day, night).
SCREEN = {
    0: ("soleil", "lune"), 1: ("eclaircies", "nuit_nuageuse"), 2: ("eclaircies", "nuit_nuageuse"), 3: ("couvert", "couvert"),
    45: ("brouillard", "brouillard"), 48: ("brouillard", "brouillard"), 51: ("bruine", "bruine"), 53: ("bruine", "bruine"),
    55: ("bruine", "bruine"), 56: ("pluie_verglacante", "pluie_verglacante"), 57: ("pluie_verglacante", "pluie_verglacante"),
    61: ("pluie", "pluie"), 63: ("pluie", "pluie"), 65: ("pluie_forte", "pluie_forte"),
    66: ("pluie_verglacante", "pluie_verglacante"), 67: ("pluie_verglacante", "pluie_verglacante"), 71: ("neige", "neige"),
    73: ("neige", "neige"), 75: ("neige_forte", "neige_forte"), 77: ("grains_de_neige", "grains_de_neige"),
    80: ("averses", "averses_nuit"), 81: ("averses", "averses_nuit"), 82: ("averses_violentes", "averses_violentes"),
    85: ("averses_neige", "averses_neige_nuit"), 86: ("averses_neige", "averses_neige_nuit"), 95: ("orage", "orage"),
    96: ("orage_grele", "orage_grele"), 97: ("orage", "orage"), 99: ("orage_grele", "orage_grele"),
}  # fmt: skip


def test_the_icon_of_each_weather_code_is_the_screens() -> None:
    codes = render()["codes"]
    assert {code: tuple(codes[str(code)]) for code in SCREEN} == SCREEN
    assert tuple(codes["4"]) == ("couvert", "couvert")  # a code the WMO table does not use: a cloud, as before
