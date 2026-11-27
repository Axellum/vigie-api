"""The home page and the Details page of the card, as the sketches chosen on 2026-11-27 draw them, and its fixed size.

Home: now, the rain of the hour as a tile, the next 24 hours and the days in columns tinted by their sun; no credits (the other
pages give them). Details: the rain until tomorrow noon in a few periods, the hour by 5 minutes, the evening by quarter hour,
48 hours hour by hour and the chances per 3 hours. The answers are the ones recorded on 2026-11-22; the pages are drawn by Node
outside a browser, and their look is checked in a real Home Assistant by the `card` job of the workflow.
"""

from __future__ import annotations

import copy
import json
import re
import shutil
import subprocess
from typing import Any

import pytest

from custom_components.vigie.card import CARD_FILE

from .conftest import recorded
from .test_card_season import text

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed here (it is in CI)")

EVENING = 1_795_378_800  # 2026-11-22 20:20 UTC, a few minutes after the reads of Chamonix and Angoulême
BIARRITZ_READ = 1_795_353_069  # 2026-11-22 13:11 UTC, the read of Biarritz
ANGOULEME = recorded("point_angouleme_2026-11-22.json")
CHAMONIX = recorded("point_chamonix_2026-11-22.json")
BIARRITZ = recorded("point_biarritz_2026-11-22.json")

# The card runs in a bare context with the few names of a browser it needs when it loads; the pages come out as HTML.
RUNNER = r"""
const fs = require('fs'), vm = require('vm');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const context = { console, HTMLElement: class {}, customElements: { get: () => undefined, define: () => {} } };
context.window = context;
vm.createContext(context);
vm.runInContext(`Date.now = () => ${Number(input.now_ms)};`, context);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context, { filename: 'vigie-card.js' });
context.__input = input;
process.stdout.write(vm.runInContext(`(() => {
  const i = __input, all = ['home', 'details', 'alerts', 'sea', 'normal'];
  const c = makeContext(i.lang, true, i.width, i.data, { type: 'custom:vigie-card', _pages: all });
  const card = new VigieCard();
  return JSON.stringify({ home: pageHome(c), details: pageDetails(c), pages: availablePages(c, all), header: header(c).right,
    grid: card.getGridOptions(), size: card.getCardSize() });
})()`, context));
"""


def render(point: dict[str, Any], *, lang: str = "fr", width: int = 374, now: int = EVENING, warnings: dict[str, Any] | None = None) -> dict[str, Any]:
    data = {"point": point, "forest_fire": None, "warnings": warnings, "earthquakes": None, "tides": None, "normals": None}
    result = subprocess.run(
        ["node", "-e", RUNNER, str(CARD_FILE)],
        input=json.dumps({"lang": lang, "width": width, "now_ms": now * 1000, "data": data}),
        capture_output=True, text=True, encoding="utf-8", check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_the_card_has_the_fixed_size_of_the_sketches() -> None:
    """480 x 760 px: 12 rows of the sections grid, not resizable, 15 units of 50 px elsewhere."""
    out = render(ANGOULEME)
    assert out["grid"] == {"columns": 12, "min_columns": 6, "rows": 12, "min_rows": 12, "max_rows": 12}
    assert out["size"] == 15


def test_home_says_now_the_rain_of_the_hour_the_hours_and_the_days_without_credits() -> None:
    out = render(ANGOULEME)
    home = text(out["home"])
    assert "Bruine faible" in home and "ressenti 8° · 83 %" in home
    assert "Rafales 26 vers 21 h 30" in home and "1021 hPa ↗" in home and "Air assez bon · UV 0" in home
    assert "Pas de pluie dans l’heure" in home and "Prochaine pluie vers 22 h 30" in home
    assert "24 h · par 2 h" in home and "7 jours · max et min" in home and "Auj." in home
    assert "Données" not in home
    # A column per 2-hour slot and per day; the current one and today ringed.
    assert out["home"].count('class="cl"') == 12 + 7 and out["home"].count("box-shadow:inset") == 2


def test_on_a_wall_screen_the_hours_go_one_by_one_over_ten_days() -> None:
    home = render(ANGOULEME, width=1200)["home"]
    assert "24 h · heure par heure" in text(home) and "10 jours · max et min" in text(home)
    assert home.count('class="cl"') == 24 + 10


def columns(html: str) -> str:
    """The styles of the columns only (the legend above them shows the whole ramp)."""
    return " ".join(re.findall(r'class="cl" style="([^"]*)"', html)).lower()


def test_the_glow_of_a_column_follows_its_sun() -> None:
    """Navy at night, grey under a cloudy sky, gold under the sun (the "Lueur" of the sketches, dark theme)."""
    assert "#6f8fe0" in columns(render(ANGOULEME)["home"])  # the evening hours
    point = copy.deepcopy(ANGOULEME)
    for h in point["hourly"]:
        h.update(is_day=True, cloud_cover_pct=0)
    sunny = columns(render(point)["home"])
    assert "#ffc94a" in sunny and "#6f8fe0" not in sunny


def test_snow_is_said_as_snow() -> None:
    home = text(render(CHAMONIX)["home"])
    assert "Il neige" in home and "modérée" in home


def test_details_give_the_rain_until_tomorrow_noon_the_three_magnifiers_and_the_chances() -> None:
    details = text(render(ANGOULEME)["details"])
    assert "La pluie d’ici demain midi" in details and "Sec → 22 h 30" in details and "Pluie → 0 h" in details and "Sec → 12 h" in details
    assert "Dans l’heure · toutes les 5 min" in details and "radar PIAF · mm/h" in details
    assert "Jusqu’à 2 h 15 · par quart d’heure" in details and "48 h · heure par heure" in details
    assert "Chances par 3 h" in details and "35 scénarios PE-AROME" in details
    assert "Pluie ≥ 1 mm" in details and "Rafales ≥ 56" in details and "Orage" in details
    assert "Neige ≥ 1 mm" not in details and "Rafales ≥ 93" not in details  # none of the windows has some
    assert "grêle" not in details  # on a phone, hail and visibility only when there is some
    assert "grêle : non · visibilité : bonne" in text(render(ANGOULEME, width=480)["details"])
    assert "Données : Météo-France" in details  # the credits stay on this page


def test_details_add_the_rows_of_snow_and_of_strong_gusts_when_there_are_some() -> None:
    details = text(render(CHAMONIX)["details"])
    assert "Neige ≥ 1 mm" in details and "Rafales ≥ 93" in details and "Pluie et neige → 12 h" in details


def test_a_dry_half_hour_between_two_rains_is_part_of_the_rain() -> None:
    """Biarritz at its read, the nowcast from its 5-minute boundary: PIAF, then AROME-PI by quarter hour."""
    point = copy.deepcopy(BIARRITZ)
    point["rain_nowcast"]["start_utc"] = BIARRITZ_READ - BIARRITZ_READ % 300
    details = text(render(point, now=BIARRITZ_READ)["details"])
    assert "Sec → 14 h 50" in details and "Pluie faible → 16 h" in details and "Accalmie → 17 h 30" in details
    assert "Pluie → 18 h 45" in details and "Pluie → 17 h 45" not in details  # 17:45 to 18:15 dry: one rain
    assert "maint." in details and "14 h 50" in details  # the first rain of the hour is labelled


def test_the_same_in_english() -> None:
    out = render(ANGOULEME, lang="en")
    assert "No rain in the next hour" in text(out["home"]) and "Rain until tomorrow noon" in text(out["details"])
    assert "Today" in text(out["home"]) and "Chances per 3 h" in text(out["details"])


def test_the_warning_pill_names_the_phenomenon() -> None:
    warnings = {"max_level": "orange", "vigilances": [{"level": "yellow", "phenomenon": "rain"}, {"level": "orange", "phenomenon": "wind"}]}
    assert "Vent orange" in text(render(ANGOULEME, warnings=warnings)["header"])
    assert "Orange wind" in text(render(ANGOULEME, warnings=warnings, lang="en")["header"])
    assert "Vigilance jaune" in text(render(ANGOULEME, warnings={"max_level": "yellow"})["header"])  # no phenomenon given


def test_details_are_shown_only_where_there_is_something_to_draw() -> None:
    assert "details" in render(ANGOULEME)["pages"]
    point = copy.deepcopy(ANGOULEME)
    del point["short_term"], point["probabilities"]
    assert "details" in render(point)["pages"]  # in France the nowcast and the hours are enough
    assert "details" not in render(recorded("point_tokyo_2026-11-22.json"))["pages"]
