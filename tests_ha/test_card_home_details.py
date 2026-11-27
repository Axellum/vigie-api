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
  if (i.squeeze) c.squeeze = i.squeeze;  // what the card measured the home page over by (see _render)
  if (i.now_less) c.nowLess = i.now_less;  // a line of "now" measured cut (see _render)
  const card = new VigieCard();
  return JSON.stringify({ home: pageHome(c), details: pageDetails(c), pages: availablePages(c, all), header: header(c).right,
    grid: card.getGridOptions(), size: card.getCardSize() });
})()`, context));
"""


def render(point: dict[str, Any], *, lang: str = "fr", width: int = 374, now: int = EVENING, warnings: dict[str, Any] | None = None,
           squeeze: int = 0, now_less: int = 0) -> dict[str, Any]:
    data = {"point": point, "forest_fire": None, "warnings": warnings, "earthquakes": None, "tides": None, "normals": None}
    result = subprocess.run(
        ["node", "-e", RUNNER, str(CARD_FILE)],
        input=json.dumps({"lang": lang, "width": width, "now_ms": now * 1000, "data": data, "squeeze": squeeze, "now_less": now_less}),
        capture_output=True, text=True, encoding="utf-8", check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_the_card_has_the_fixed_size_of_the_sketches() -> None:
    """480 x 760 px: 12 rows and the whole width of a section of the sections grid, not resizable, 15 units of 50 px elsewhere."""
    out = render(ANGOULEME)
    assert out["grid"] == {"columns": 12, "min_columns": 12, "rows": 12, "min_rows": 12, "max_rows": 12}
    assert out["size"] == 15


def heights(html: str) -> list[int]:
    """The heights of the blocks of columns, hours then days."""
    return [int(h) for h in re.findall(r'class="cols" style="width:[^;"]*;height:(\d+)px"', html)]


def test_a_home_page_measured_too_tall_is_drawn_with_flatter_curves_never_flatter_than_set() -> None:
    """The card measures its home page and draws it again by what it is over (Home Assistant's fonts, a long header): the
    hours lose a third, the days two thirds, at most 20 and 40 px (the curves stay readable; past that the page scrolls)."""
    drawn = heights(render(ANGOULEME)["home"])
    over12 = heights(render(ANGOULEME, squeeze=12)["home"])
    assert over12[0] == drawn[0] - 4 and over12[1] == drawn[1] - 8
    assert heights(render(ANGOULEME, squeeze=500)["home"]) == [192 - 20, 250 - 40]  # no chance per hour, a chance per day


def test_on_a_narrow_card_the_now_block_leaves_out_what_would_be_cut() -> None:
    """Measured on 2026-11-27 with Home Assistant's fonts: the gust of now does not fit under 370 px inside, the humidity under
    340; the gusts to come stay beside."""
    wide = text(render(ANGOULEME, width=412)["home"])  # 380 px inside, the phone of the author
    assert "ressenti 8° · 83 %" in wide and "raf. 22" in wide
    narrow = text(render(ANGOULEME, width=374)["home"])
    now = narrow.split("24 h")[0]  # the days say their gusts ("raf. 25") since the "Calme" sketch
    assert "ressenti 8° · 83 %" in narrow and "raf." not in now and "Rafales 26 vers 21 h 30" in narrow
    narrower = text(render(ANGOULEME, width=360)["home"])
    assert "ressenti 8°" in narrower and "83 %" not in narrower.split("Rafales")[0]


def test_a_line_of_now_measured_cut_is_drawn_again_with_less() -> None:
    """The widths above are measured in French; a longer word ("gusts") or another font is measured by the card itself."""
    assert "gusts 22" in text(render(ANGOULEME, lang="en", width=412)["home"])
    less = text(render(ANGOULEME, lang="en", width=412, now_less=1)["home"])
    assert "gusts 22" not in less and "feels like 8° · 83 %" in less
    assert "83 %" not in text(render(ANGOULEME, lang="en", width=412, now_less=2)["home"]).split("Gusts")[0]


def day_names(html: str) -> list[str]:
    return [text(b) for b in re.findall(r'<div class="dh"><b[^>]*>(.*?)</b>', html)]


def test_narrow_day_columns_say_the_day_name_alone_and_smaller_amounts() -> None:
    """Seen on the dashboard of the author on 2026-11-27: a card 339 px wide (a section of three on a desktop), seven
    columns of 44 px, "Sam 28Dim 29" and "21 mm0,5 mm" touching."""
    narrow = render(ANGOULEME, width=339)["home"]
    assert day_names(narrow) == ["Auj.", "Lun", "Mar", "Mer", "Jeu", "Ven", "Sam"] and 'class="bloc serre"' in narrow
    wide = render(ANGOULEME, width=412)["home"]
    assert day_names(wide)[:3] == ["Auj.", "Lun 23", "Mar 24"] and "serre" not in wide
    css = CARD_FILE.read_text(encoding="utf-8")
    assert ".serre .db b{font-size:11px}" in css and ".serre .vt2{font-size:11px" in css


def test_on_narrow_hour_columns_english_says_the_hour_alone() -> None:
    """"21:00" does not fit a column of 32 px beside "23:00"; "21 h" does."""
    hours = lambda html: [text(x) for x in re.findall(r'<span class="hr"[^>]*>(.*?)</span>', html)]  # noqa: E731
    assert hours(render(ANGOULEME, lang="en", width=412)["home"])[:3] == ["21", "23", "01"]
    assert hours(render(ANGOULEME, lang="en", width=1200)["home"])[:2] == ["21:00", "22:00"]
    assert hours(render(ANGOULEME, width=412)["home"])[:2] == ["21 h", "23 h"]


def day_cells(html: str) -> list[str]:
    """The bottom of each day column: the rain, its chance, the wind, the gusts."""
    return html.split('<div class="db">')[1:]


def day_wind(cell: str) -> str:
    return text(re.search(r'<span class="vt2">(.*?)</span>', cell).group(1))


def day_gust(cell: str) -> str | None:
    found = re.search(r'<span class="pc rf"[^>]*>(.*?)</span>', cell)
    return text(found.group(1)) if found else None


def test_the_gust_of_today_is_left_out_when_below_the_wind_of_the_whole_day() -> None:
    """Today the gust is the one of the hours still to come, the wind the highest of the whole day: "33/24" read wrong. The wind
    says its unit and the gust goes on its own line (the "Calme" sketch chosen on 2026-11-27)."""
    cells = day_cells(render(ANGOULEME)["home"])
    assert day_wind(cells[0]) == "33 km/h" and day_gust(cells[0]) is None
    assert day_wind(cells[1]) == "13 km/h" and day_gust(cells[1]) == "raf. 25"
    assert day_gust(day_cells(render(ANGOULEME, lang="en")["home"])[1]) == "gusts 25"


def test_colour_only_where_it_says_something() -> None:
    """The "Calme" sketch: the temperatures keep the colour of their value, the wind is grey, a gust has a colour only from
    60 km/h (yellow, orange from 80), the rain of a day is a gauge of the same width every day."""
    home = render(ANGOULEME, width=412)["home"]
    now = home.split('class="tuile"')[0]
    assert '<b style="color:#F4F7FA">12</b>&nbsp;km/h' in now  # the wind of now in the colour of the text
    assert "raf. <b style=\"color:#F4F7FA\">22</b>" in now and 'Rafales <b style="color:#F4F7FA">26</b>' in now
    assert re.search(r'font-size:\d+px;color:#[0-9a-f]{6}">[0-9]+°', now)  # the temperature in its colour (a mix of the ramp)
    hours = home.split('class="cols"')[1]
    assert "<small" not in "".join(re.findall(r'<div class="vt">(.*?)</div>', hours))  # gusts of 18 to 26 km/h: left out
    cells = day_cells(home)
    assert all('<span class="gauge">' in cell for cell in cells) and 'class="bw"><i' not in home
    wet = [cell for cell in cells if "mm</b>" in cell]
    assert wet and all('<span class="gauge"><i style="width:' in cell for cell in wet)
    assert '<span class="gauge"></span>' in "".join(cell for cell in cells if "sec</b>" in cell)
    css = CARD_FILE.read_text(encoding="utf-8")
    assert re.search(r"\n\.vt span\{[^}]*color:var\(--vc-t2\)", css) and re.search(r"\n\.vt2\{[^}]*color:var\(--vc-t2\)", css)
    # A strong gust, of an hour and of a day, in its colour.
    point = copy.deepcopy(ANGOULEME)
    for h in point["hourly"][:2]:
        h["wind_gust_kmh"] = 85.0
    for h in point["hourly"]:
        if h["time_utc"] >= 1_795_388_400 + 3 * 3600 and h["time_utc"] < 1_795_388_400 + 6 * 3600:
            h["wind_gust_kmh"] = 66.0
    stormy = render(point, width=412)["home"]
    assert '<small style="color:#FFA24D;font-weight:700">85</small>' in stormy.split('class="cols"')[1]
    assert re.search(r'<span class="pc rf" style="color:#FFD25C;font-weight:700">raf\. 66</span>', stormy)


def test_on_narrow_day_columns_the_wind_keeps_its_unit_and_loses_its_arrow() -> None:
    """339 px, the dashboard of the author: "↖ 11 km/h" came within 3 px of its neighbour; on a 412 px phone "↖ 28 km/h"
    touched the ring of today's column. The arrow only where the line stays 4 px off it; the direction is in the hours."""
    def arrows(point: dict[str, Any], width: int) -> list[bool]:
        found = [re.search(r'<span class="vt2">(.*?)</span>', c) for c in day_cells(render(point, width=width)["home"])]
        return [m is not None and "<svg" in m.group(1) for m in found]

    cells = day_cells(render(ANGOULEME, width=339)["home"])
    assert day_wind(cells[1]) == "13 km/h"
    assert not any(arrows(ANGOULEME, 339)) and not any(arrows(ANGOULEME, 412))
    assert arrows(ANGOULEME, 480)[1]
    gale = copy.deepcopy(ANGOULEME)
    gale["daily"][1]["wind_max"] = 105  # "↖ 105 km/h" is 7 px wider
    assert not arrows(gale, 480)[1] and day_wind(day_cells(render(gale, width=480)["home"])[1]) == "105 km/h"


def test_the_redrawn_pages_set_their_own_line_height_and_the_rain_tile_drops_the_times_it_has_no_room_for() -> None:
    """Home Assistant gives its text a line height of 1.6, which made the home page of the fixed card scroll on a phone."""
    css = CARD_FILE.read_text(encoding="utf-8")
    assert re.search(r"\n\.pile\{[^}]*line-height:normal", css)
    assert re.search(r"\n:host\{display:block\}", css)  # not the height of a panel view: the card is 760 px high
    assert "@container (max-width:130px){.tuile .tl span:nth-child(2){display:none}}" in css
    assert "@container (max-width:80px){.tuile .tl{display:none}}" in css
    # its second line ("Prochaine pluie après-demain vers 6 h") goes on two lines rather than ending in "…"
    assert re.search(r"\.tuile \.tt small\{[^}]*-webkit-line-clamp:2", css)


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


def with_sunshine(point: dict[str, Any], hours: list[float | None]) -> dict[str, Any]:
    """The answer with the hours of sunshine of its days (`sunshine_h`, from 2026-11-27); the recorded answers have none."""
    out = copy.deepcopy(point)
    for i, day in enumerate(out["daily"]):
        day["sunshine_h"] = hours[i] if i < len(hours) else None
    return out


def sun_lines(html: str) -> list[str]:
    return [text(x) for x in re.findall(r'<span class="ds"[^>]*>(.*?)</span>', html)]


def test_the_days_say_their_hours_of_sunshine_under_their_icon_as_the_sketch() -> None:
    """The "Lueur" sketch: "● 2 h" under the icon of each day; under an hour, in minutes ("0,5 h" in a narrow column)."""
    point = with_sunshine(ANGOULEME, [2.0, 1.0, 4.2, 0.5, 0.0, 5.0, 7.3])
    home = render(point, width=412)["home"]
    assert sun_lines(home) == ["2 h", "1 h", "4 h", "30 min", "0 h", "5 h", "7 h"]
    assert "soleil · pluie · vent / rafales" in text(home)
    assert sun_lines(render(point, width=339)["home"])[3] == "0,5 h"
    assert "sun · rain · wind / gusts" in text(render(point, lang="en")["home"])


def test_a_day_whose_sunshine_is_unknown_keeps_an_empty_line_and_a_page_without_any_draws_as_before() -> None:
    point = with_sunshine(ANGOULEME, [2.0, None, 4.0])
    lines = sun_lines(render(point)["home"])
    assert lines[:3] == ["2 h", "", "4 h"] and set(lines[3:]) == {""}
    before = render(ANGOULEME)["home"]  # the recorded answer: no hours of sunshine at all
    assert 'class="ds"' not in before and "soleil" not in text(before).split("7 jours")[1]
    assert heights(before)[1] + 14 == heights(render(point)["home"])[1]  # the line of sunshine: 14 px more for the days


def test_the_glow_of_a_day_follows_its_share_of_sunshine_when_the_answer_gives_it() -> None:
    """Gold for a day of sun, grey for a day without, whatever the cloud cover of its hours says (it is only an estimate)."""
    def day_columns(point: dict[str, Any]) -> list[str]:
        return re.findall(r'class="cl" style="([^"]*)"', render(point)["home"])[-7:]
    def top(style: str) -> tuple[int, int, int, int]:  # the colour at the top of the column, "#rrggbbaa"
        h = re.search(r"#([0-9a-f]{8}) 0%", style.lower()).group(1)
        return tuple(int(h[k:k + 2], 16) for k in (0, 2, 4, 6))
    columns = day_columns(with_sunshine(ANGOULEME, [None, 8.5, 0.0]))
    sunny, grey = top(columns[1]), top(columns[2])  # 8.5 h of the 9 h of daylight of late November, and 0 h
    assert sunny[3] > 2 * grey[3]  # the glow of a day of sun is far stronger
    assert sunny[0] - sunny[2] > 120 and abs(grey[0] - grey[2]) < 40  # and gold, where a day without sun stays grey
