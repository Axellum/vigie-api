"""The Mountain page of the card (sketch "Montagne · sous-menu des stations" of 2026-11-27, without the tiles of SkiRadar nor the
"open" pill): the stations of its configuration, a sentence, the tiles of the avalanches, the freezing level and the fresh snow,
the bulletin with its aspects, 24 hours at the top and at the base, the days at the top.

The weather of a station is the recorded answer of Chamonix moved to the altitudes asked (``payloads.station_point``: the API's
``elevation`` was not recorded yet); the bulletins are real ones, recorded on 2026-11-27. The page is drawn by Node outside a
browser, like the other pages.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any

import pytest

from custom_components.vigie.card import CARD_FILE

from .conftest import recorded
from .payloads import station_point
from .test_card_season import text

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed here (it is in CI)")

EVENING = 1_795_378_800  # 2026-11-22 20:20 UTC, a few minutes after the read of Chamonix
CHAMONIX = recorded("point_chamonix_2026-11-22.json")
STATIONS = [
    {"name": "Chamonix", "latitude": 45.92, "longitude": 6.87, "base_m": 1350, "top_m": 2400},
    {"name": "Gourette", "latitude": 42.96, "longitude": -0.335, "base_m": 1350, "top_m": 2400},
]

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
  const i = __input;
  const config = { type: 'custom:vigie-card', _pages: PAGE_IDS, _station: i.station || 0, ...(i.config || {}) };
  const c = makeContext(i.lang, true, i.width, { point: i.home, warnings: null, tides: null, normals: null }, config);
  c.mountain = i.mountain;
  c.mountainError = i.error || null;
  const page = pageMountain(c);
  return JSON.stringify({ page, pages: availablePages(c, PAGE_IDS), stations: stationsOf(config),
    icons: nav(c, ['home', 'mountain'], 'mountain', true), names: nav(c, ['home', 'mountain'], 'mountain', 'names'),
    narrow: c.mountainTilesNarrow ? c.mountainTilesNarrow() : null });
})()`, context));
"""


def station(avalanches: str | None = "avalanches_chamonix_2026-11-27.json", **kw: Any) -> dict[str, Any]:
    """What ``vigie/card/mountain`` answers for Chamonix, 1 350 to 2 400 m (the integration trims more; the page reads the same)."""
    return {
        "language": "fr",
        "top": station_point(CHAMONIX, 2400, -8.0, **kw),
        "base": station_point(CHAMONIX, 1350, -1.5, **kw),
        "avalanches": recorded(avalanches) if avalanches else None,
        "elevation_ignored": False,
    }


def render(mountain: Any, *, lang: str = "fr", width: int = 480, stations: Any = None, index: int = 0, error: str | None = None) -> dict[str, Any]:
    config = {"stations": STATIONS if stations is None else stations}
    result = subprocess.run(
        ["node", "-e", RUNNER, str(CARD_FILE)],
        input=json.dumps({"lang": lang, "width": width, "now_ms": EVENING * 1000, "home": CHAMONIX, "mountain": mountain, "config": config,
                          "station": index, "error": error}),
        capture_output=True, text=True, encoding="utf-8", check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_the_page_is_there_only_with_a_station_that_can_be_read() -> None:
    assert "mountain" in render(station())["pages"]
    assert "mountain" not in render(station(), stations=[])["pages"]
    odd = [{"name": "No top", "latitude": 45.9, "longitude": 6.8}, {"latitude": 95, "longitude": 6.8, "top_m": 2000},
           {"latitude": 45.9, "longitude": 6.8, "top_m": 12000}, {"latitude": 45.9, "longitude": 6.8, "top_m": 2000, "base_m": "bas"}]
    out = render(station(), stations=odd)
    assert out["stations"] == [{"name": None, "latitude": 45.9, "longitude": 6.8, "top_m": 2000, "base_m": None}]
    assert len(render(station(), stations=[STATIONS[0]] * 9)["stations"]) == 6


def test_the_page_of_a_station_as_the_sketch_draws_it() -> None:
    out = render(station())
    page, words = out["page"], text(out["page"])
    # The stations as chips, the one shown first; its name, its altitudes and the sentence.
    assert page.count('data-station="') == 2 and 'class="sch on" data-station="0"' in page
    assert "Chamonix 1 350 m – 2 400 m" in words
    assert "Neige en ce moment jusqu’au pied" in words  # it snows at 20:00 at the top (-5.6 °C) and at the base (0.9 °C)
    # The tiles: the danger of the bulletin, the freezing level now and where it goes, the next fresh snow.
    assert "3 Avalanches marqué" in words
    assert "Iso 0 1 500 m jusqu’à 2 210 m mercredi" in words  # 1 500 m now, 10 m higher each hour over the 72 hours
    assert "Neige fraîche 15 cm Aujourd’hui · chance 100 %" in words
    # The bulletin: its two altitudes, the aspects of its problems (all eight), the problems, what comes next, the link.
    assert "Bulletin d’avalanches Météo-France · Mont-Blanc" in words
    assert "3 au-dessus de 2 700 m 2 en dessous de 2 700 m" in words
    assert page.count('stroke="var(--vc-carte)" stroke-width="1.5"') == 8 and page.count('fill="#FFA24D" stroke="var(--vc-carte)"') == 8
    assert "Neige soufflée toutes les pentes" in words and "Avalanches de glissement toutes les pentes" in words
    assert "Ensuite : Indice de risque marqué" in words and "Le bulletin d’avalanche de Météo-France" in words
    # 24 hours at the top and at the base, then the days at the top, and the credits.
    assert "24 h · en haut et en bas" in words and "en haut -6°" in words and "en bas 1°" in words
    assert "Neige ou pluie estimée d’après la température de l’heure." in words
    assert "7 jours · en haut" in words and words.count(" cm ") >= 4
    assert "Données : Météo-France" in words and "relayées par Vigie" in words


def test_a_bulletin_of_avalanche_report_in_english_with_its_aspects_and_trend() -> None:
    out = render(station("avalanches_stubai_2026-11-27.json"), lang="en")
    page, words = out["page"], text(out["page"])
    assert "Avalanche bulletin Avalanche.report" in words and "AT-07-14-03" not in words  # its area has a code, not a name
    assert "2 above 2,400 m 1 below 2,400 m" in words
    assert "Persistent weak layers aspects N, NE, E, W, NW, from 2,400 m to 2,900 m" in words
    assert "Wind slab aspects N, NE, E, W, NW, above 2,200 m" in words and "Trend: steady" in words
    assert page.count('fill="#FFD25C" stroke="var(--vc-carte)"') == 5  # N, NE, E, W, NW in the colour of level 2
    assert "Freezing level 1,500 m up to 2,210 m Wednesday" in words


def test_one_level_at_every_altitude() -> None:
    out = render(station("avalanches_gourette_2026-11-27.json"))
    words = text(out["page"])
    assert "Bulletin d’avalanches Météo-France · Aspe Ossau" in words and "2 à toutes les altitudes" in words
    # The problems in the card's words, their six aspects in French (SO, not SW) and on the rose.
    assert "Neige fraîche pentes N, NE, E, SE, S, SO" in words and "Neige mouillée pentes N, NE, E, SE, S, SO" in words
    assert out["page"].count('fill="#FFD25C" stroke="var(--vc-carte)"') == 6


@pytest.mark.parametrize(
    ("status", "said"),
    [
        ("confirmed_none", "Pas de bulletin d’avalanche en cours pour ce lieu"),
        ("outside_coverage", "Aucun bulletin d’avalanche relayé par Vigie ne couvre ce lieu."),
        ("unavailable", "Le bulletin d’avalanche ne répond pas pour le moment."),
    ],
)
def test_without_a_bulletin_in_force_the_page_says_why(status: str, said: str) -> None:
    answer = station(None)
    answer["avalanches"] = {"status": status, "covered_by": None, "bulletins": [], "attribution": None}
    words = text(render(answer)["page"])
    assert said in words and "Iso 0" in words


def test_what_is_not_given_is_not_drawn() -> None:
    """No freezing level (an instance before it), no fresh snow, no bulletin: no tile for them, no empty box."""
    answer = station(None, freezing=False)
    for part in ("top", "base"):
        for day in answer[part]["daily"]:
            day.pop("snowfall_cm")
    words = text(render(answer)["page"])
    assert "Iso 0" not in words and "Neige fraîche" not in words and "Avalanches" not in words and "Bulletin" not in words
    assert "24 h · en haut et en bas" in words and "max / min · vent" in words


def test_an_instance_older_than_the_altitude_is_said() -> None:
    words = text(render({"language": "fr", "top": None, "base": None, "avalanches": None, "elevation_ignored": True})["page"])
    assert "L’instance Vigie ne donne pas encore la météo à l’altitude choisie" in words and "Iso 0" not in words


def test_the_second_station_and_the_waiting() -> None:
    out = render(station(), index=1)
    assert 'class="sch on" data-station="1"' in out["page"] and text(out["page"]).count("Gourette") == 2
    assert "Chargement…" in text(render(None)["page"]) and "Les données de Vigie ne répondent pas" in text(render(None, error="error")["page"])


def test_the_bar_of_icons_when_the_names_do_not_fit() -> None:
    icons = render(station())["icons"]
    assert 'aria-label="Montagne"' in icons and 'aria-label="Accueil"' in icons and "<span>" not in icons


def test_a_narrow_card_puts_the_avalanches_on_the_whole_width() -> None:
    page = render(station(), width=339)["page"]
    assert 'class="mt mtav" style="grid-column:1 / -1"' in page and "grid-template-columns:repeat(2,minmax(0,1fr))" in page
    assert 'grid-column' not in render(station(), width=480)["page"]


def test_the_fresh_snow_is_the_one_of_the_seven_days_shown() -> None:
    """Seen on a real Home Assistant: "21 cm Tuesday" in the tile, the Tuesday of the next week (11 days ahead), while the seven
    days under it showed none. The tile reads the days the page shows, with the threshold of their pills (what rounds to 1 cm)."""
    answer = station()
    for index, day in enumerate(answer["top"]["daily"]):
        day["snowfall_cm"] = 21.0 if index == 10 else 0.0
    words = text(render(answer)["page"])
    assert "Neige fraîche 0 cm Rien sur 7 jours" in words and "21" not in words.split("Neige fraîche")[1].split("Bulletin")[0]
    answer["top"]["daily"][3]["snowfall_cm"] = 0.5  # shown "1 cm" in its day's pill: the tile says the same day
    words = text(render(answer)["page"])
    assert "Neige fraîche 1 cm" in words and "chance" in words.split("Neige fraîche")[1].split("Bulletin")[0]


def test_the_tiles_drawn_again_on_two_rows_when_a_word_is_cut() -> None:
    """The card measures the tiles once drawn ("considerable" cut in English at 480 px): it draws them again, the avalanches on
    the whole width and the two others under them."""
    out = render(station(), lang="en", width=480)
    assert "data-narrow" not in out["page"]
    assert 'data-narrow="1"' in out["narrow"] and 'class="mt mtav" style="grid-column:1 / -1"' in out["narrow"]
    assert "grid-template-columns:repeat(2,minmax(0,1fr))" in out["narrow"] and "considerable" in out["narrow"]


def test_a_wide_bar_drops_its_icons_before_its_names() -> None:
    names = render(station(), width=1144)["names"]
    assert "<span>Montagne</span>" in names and "<svg" not in names and "aria-label" not in names
