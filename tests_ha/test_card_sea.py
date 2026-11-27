"""The Sea page of the card (sketch "Mer · sous-menu des plages" of 2026-11-27, without the flag of the beach, which no open source
gives): the beaches of its configuration as chips, the name, the tide and the water, six tiles, the verdicts of the activities
hour by hour, the tide of 24 hours and the next hours by 3 hours. Without a beach answer (the online demo, or while the spot of
``spot`` is asked), the page of before.

The beach is what ``vigie/card/beach`` answers for the Plage des Corsaires, made by the integration's own trimming from the real
answers recorded on 2026-11-27 at 17:11 UTC; the clock of the page is frozen just after. The page is drawn by Node outside a
browser, like the other pages.
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
from custom_components.vigie.weather_data import bathing_site, beach_activities, beach_forecast, beach_water

from .conftest import SPOT, recorded
from .test_card_season import text

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed here (it is in CI)")

READ_UTC = 1_795_799_503  # 2026-11-27 17:11:43 UTC
NOW = READ_UTC + 17  # 18:12 in Biarritz
FORECAST = recorded(f"surfspot_{SPOT}_2026-11-27.json")
ACTIVITIES = recorded(f"activities_{SPOT}_2026-11-27.json")
BATHING = recorded(f"seaplaces_{SPOT}_2026-11-27.json")
POINT = recorded("point_biarritz_2026-11-22.json")
NEAR = {"id": SPOT, "name": "Plage des Corsaires", "place": "Biarritz", "distance_km": 1.2}
OTHER = "anglet-plage-des-cavaliers"
SPOTS = [NEAR, {"id": OTHER, "name": "Plage des Cavaliers", "place": "Anglet", "distance_km": 4.1}]

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
  const config = { type: 'custom:vigie-card', _pages: PAGE_IDS, _beach: i.index || 0, ...(i.config || {}) };
  const data = { point: i.home, warnings: null, tides: null, normals: null, spot: i.spot || null, activities: null };
  const c = makeContext(i.lang, true, i.width, data, config);
  c.beach = i.beach;
  c.beachError = i.error || null;
  c.spots = i.spots || null;
  c.squeeze = i.squeeze || 0;
  const page = pageSea(c);
  return JSON.stringify({ page, home: pageSeaHome(c), pages: availablePages(c, PAGE_IDS), beaches: beachesOf(config),
    levels: c.beachTilesAt ? [1, 2].map((level) => c.beachTilesAt(level)) : null,
    short: ['Plage des Corsaires', 'Plage de la Côte des Basques', 'Grande Plage', 'Plage Miramar', 'Plage d’Erretegia', 'Fistral Beach', 'Plage'].map(shortBeach),
    windows: i.lows ? i.lows.map((now) => baineWindow(i.extremes, now)) : null });
})()`, context));
"""


def beach(**spot: Any) -> dict[str, Any]:
    """What ``vigie/card/beach`` answers for the Plage des Corsaires (``spot``: fields of the spot changed)."""
    forecast = beach_forecast(copy.deepcopy(FORECAST), READ_UTC)
    assert forecast is not None
    forecast["spot"].update(spot)
    return {
        "language": "fr",
        "spot": {**NEAR, **forecast["spot"]},
        "forecast": forecast,
        "activities": beach_activities(copy.deepcopy(ACTIVITIES), READ_UTC),
        "water": beach_water(POINT),
        "bathing": bathing_site(BATHING, FORECAST["spot"]),
    }


def render(answer: Any, *, lang: str = "fr", width: int = 480, beaches: Any = (SPOT, OTHER), index: int = 0, error: str | None = None,
           spot: Any = None, squeeze: int = 0, now: int = NOW, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    config: dict[str, Any] = {"beaches": list(beaches)} if beaches is not None else {}
    result = subprocess.run(
        ["node", "-e", RUNNER, str(CARD_FILE)],
        input=json.dumps({"lang": lang, "width": width, "now_ms": now * 1000, "home": POINT, "beach": answer, "config": config,
                          "index": index, "error": error, "spot": spot, "spots": SPOTS, "squeeze": squeeze, **(extra or {})}),
        capture_output=True, text=True, encoding="utf-8", check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_the_page_of_a_beach_as_the_sketch_draws_it() -> None:
    out = render(beach())
    page, words = out["page"], text(out["page"])
    # The beaches as chips (their short names), the one shown first; its name, the tide, the coefficient and the water.
    assert page.count('data-beach="') == 2 and 'class="sch on" data-beach="0">Corsaires<' in page and ">Cavaliers<" in page
    assert "Plage des Corsaires La mer monte · pleine mer 18 h 25 · coef. 85 (estimé) eau 16°" in words
    # The six tiles: the sea, the swell and the wind of the hour; the baïnes (Biarritz faces the open Atlantic, on sand) from
    # three hours before the low water of 0 h 27; the class of the bathing site; the lifeguards of the GIP Littoral.
    assert "Vagues · agitée 1,2 m 12 s · du nord-ouest" in words
    assert "Houle 1,1 m 9 s · du nord-ouest" in words
    assert "Vent de terre 3 km/h rafales 6 · du sud-est" in words
    assert "Baïnes dès 21 h 27 jusqu’à 3 h 27" in words and 'title="Courant des baïnes le plus fort de 21&nbsp;h&nbsp;27 à 3&nbsp;h&nbsp;27' in page
    assert "Qualité de l’eau Excellente classe sur 4 ans" in words and 'title="Les Corsaires, Anglet"' in page
    assert "Plage surveillée En saison pas tous les jours" in words
    assert page.count('class="bt"') == 6 and "Coucher du soleil" not in words
    # The verdicts: the best slot of the good one, why the others are not, 24 hours each (the night dark), then the rules.
    assert "Activités dans les 24 h 18 h 0 h 6 h 12 h" in words
    assert "Surf Bon au mieux 9 h–18 h" in words and "Kite / wing Déconseillé Vent : trop faible" in words
    assert "Bateau Possible Vagues : modérées" in words and "Pêche à pied Possible Vagues : modérées" in words
    assert page.count("<i style=\"background:") == 4 * 24
    assert "Règles de Vigie, pas pour la sécurité ni la navigation" in words
    # The tide of 24 hours with its high and low waters, the next hours by 3 hours, the credits.
    assert "Marée · 24 h pas pour la navigation" in words and "PM 18 h 25" in words and "BM 0 h 27" in words
    assert "Les prochaines heures toutes les 3 h" in words and "Vagues m" in words and "Période s" in words and "Vent km/h" in words
    for name in ("Open-Meteo", "Copernicus", "EOT20", "OpenStreetMap", "GIP Littoral", "Shom", "ministère de la Santé (data.gouv.fr)"):
        assert name in words.split("Données :")[1]


def test_in_english_and_wide() -> None:
    out = render(beach(), lang="en", width=1144)
    page, words = out["page"], text(out["page"])
    assert 'class="duo"' in page and "Plage des Corsaires The tide is rising · high water 18:25 · coef. 85 (estimated) water 16°" in words
    assert "Waves · moderate 1.2 m" in words and "Wind offshore 3 km/h gusts 6" in words and "Rip currents from 21:27 until 03:27" in words
    assert "Water quality Excellent class over 4 years" in words and "Lifeguarded beach In season not every day" in words
    assert "Surf Good best 09:00–18:00" in words and "Kite / wing Not advised Wind: too light" in words
    assert "Tide · 24 h not for navigation" in words and "The next hours every 3 h" in words and "French Ministry of Health" in words


def test_the_second_beach_the_waiting_and_the_errors() -> None:
    assert 'class="sch on" data-beach="1"' in render(beach(), index=1)["page"]
    waiting = render(None, index=1)
    assert "Chargement…" in text(waiting["page"]) and ">Cavaliers<" in waiting["page"] and "Plage des Cavaliers" in text(waiting["page"])
    assert "Les données de Vigie ne répondent pas" in text(render(None, error="not_found")["page"])
    # An integration that does not answer `vigie/card/beach` (the online demo): the page of before.
    out = render(None, error="unknown_command")
    assert out["page"] == out["home"]


def test_without_beaches_the_spot_of_the_data_and_the_page_of_before_while_it_is_asked() -> None:
    out = render(beach(), beaches=None, spot={"id": SPOT, "name": "Plage des Corsaires", "distance_km": 1.2})
    assert "data-beach" not in out["page"] and 'class="bts"' in out["page"]
    out = render(None, beaches=None, spot={"id": SPOT, "name": "Plage des Corsaires", "distance_km": 1.2})
    assert out["page"] == out["home"]
    assert "sea" not in render(None, beaches=None)["pages"]  # no tide, no spot, no beach: no page
    assert "sea" in render(None)["pages"] and render(None, beaches=[SPOT] * 3 + [f"s{i}" for i in range(9)])["beaches"] == [SPOT] + [f"s{i}" for i in range(5)]


def test_what_is_not_given_is_not_drawn_and_the_sun_takes_the_tile_left() -> None:
    """Away from the coast of the baïnes, without a bathing site nor the water: no tile and no "eau" for them; the sun on the
    tile left (at 18:12, after sunset: the sunrise of tomorrow)."""
    answer = beach(lat=48.6, lifeguarded=None)
    answer["bathing"] = None
    answer["water"] = None
    words = text(render(answer)["page"])
    assert "Baïnes" not in words and "Qualité" not in words and "Surveillée" not in words and "eau 16°" not in words
    assert "Lever du soleil 8 h 15 coucher 17 h 32" in words
    assert "ministère de la Santé" not in words


def test_the_baines_window() -> None:
    """From three hours before to three hours after the next low water still to come, within 24 hours."""
    lows = [{"time_utc": 300_000, "kind": "low"}, {"time_utc": 100_000, "kind": "low"}, {"time_utc": 200_000, "kind": "high"}]
    out = render(beach(), extra={"extremes": lows, "lows": [0, 10_000, 95_000, 111_000, 250_000]})
    assert [(w or {}).get("from") for w in out["windows"]] == [None, 89_200, 89_200, None, 289_200]
    assert [(w or {}).get("now") for w in out["windows"]] == [None, False, True, None, False]


def test_the_short_names_of_the_chips() -> None:
    assert render(beach())["short"] == ["Corsaires", "Côte des Basques", "Grande Plage", "Miramar", "Erretegia", "Fistral Beach", "Plage"]


def test_a_phone_gives_back_what_the_page_is_over() -> None:
    """The card measures the page once drawn: 20 px over, the tide gives them back; 60 px over, the tide 24 px, the four rows of
    the activities 4 px each (two lines need 30 px), the cells of the table 4 px each and the gaps between the blocks the rest."""
    page = render(beach())["page"]
    assert ';height:34px"' in page and 'height="84"' in page and "height:20px;" in page and 'class="pile" style="gap:8px"' in page
    page = render(beach(), squeeze=20)["page"]
    assert ';height:34px"' in page and 'height="64"' in page and 'class="pile" style="gap:8px"' in page
    page = render(beach(), squeeze=60)["page"]
    assert ';height:30px"' in page and 'height="60"' in page and "height:16px;" in page and 'class="pile" style="gap:6px"' in page
    assert ';height:44px"' in render(beach(), width=1144, squeeze=60)["page"]  # a wide card does not need it
    assert "padding-block" not in page


def test_with_two_beaches_their_tabs_and_the_tiles_give_back_the_rest() -> None:
    """The row of the tabs of two beaches left a phone's card 10 px over once the tide, the rows, the cells and the gaps had
    given back all they can (2026-11-27): the tabs then give 2 px for each px of their padding (3 at most), and the two rows of
    tiles 4 px for each (2 at most)."""
    page = render(beach(), squeeze=80)["page"]
    assert 'class="pile" style="gap:6px"' in page and 'height="60"' in page
    assert page.count('style="padding-block:2px"') == 2 and page.count('style="padding-block:4px"') == 6
    alone = render(beach(), beaches=(SPOT,), squeeze=80)["page"]  # one beach: no tabs, the tiles give the rest
    assert 'class="schs"' not in alone and alone.count('style="padding-block:4px"') == 6


def test_the_shorter_words_of_the_tiles() -> None:
    """Drawn again when the card measures a word cut: first without the directions in words (the arrows keep them), then
    without the state of the sea either, and with the shorter names."""
    out = render(beach(), lang="en")
    first, second = out["levels"]
    assert 'data-level="0"' in out["page"] and "Waves · moderate" in out["page"] and "from the north-west" in out["page"]
    assert 'data-level="1"' in first and "Waves · moderate" in first and "from the north-west" not in first and "Water quality" in first
    assert 'data-level="2"' in second and "Waves · " not in second and ">Quality<" in second
    # The lifeguards and the class of the water, cut on a phone's card in English and at 720 px in French (2026-11-27).
    assert ">Lifeguarded beach<" in out["page"] and ">class over 4 years<" in out["page"] and ">not every day<" in out["page"]
    assert ">Lifeguards<" in second and ">over 4 years<" in second and ">some days<" in second
    assert ">Surveillance<" in render(beach())["levels"][1] and ">selon les jours<" in render(beach())["levels"][1]


def moved(value: Any, seconds: int) -> Any:
    """``value`` with every epoch (a key ending in ``_utc``) moved by ``seconds``, as the fake instance of the workflow replays it."""
    if isinstance(value, dict):
        return {k: v + seconds if k.endswith("_utc") and isinstance(v, int) else moved(v, seconds) for k, v in value.items()}
    if isinstance(value, list):
        return [moved(v, seconds) for v in value]
    return value


@pytest.mark.parametrize("seconds", [1380, -600])
def test_hours_off_the_hour_find_their_slot(seconds: int) -> None:
    """A replay moves the hours by the seconds the clock moved since the read: each is the slot of its nearest whole hour, so
    the 24 hours of the activities and the table of the next hours are drawn as with the hours of the API (2026-11-27 in CI:
    no table and grey hours)."""
    strips = lambda page: re.findall(r'<i style="background:([^"]+)"', page)  # noqa: E731
    cells = lambda page: text(page).split("Les prochaines heures")[1]  # noqa: E731
    page, shifted = render(beach())["page"], render(moved(beach(), seconds))["page"]
    assert strips(shifted) == strips(page) and len(set(strips(page))) > 1
    assert cells(shifted).split("Données")[0] == cells(page).split("Données")[0]
