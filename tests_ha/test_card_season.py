"""The lines of the season of the card (avalanches, drought, pollen, forest fires): shown only when there is something to say.

The real answers recorded on 2026-11-22 give an avalanche bulletin at 4 of 5 and a drought watch at Chamonix, drought
restrictions at Angoulême, nothing at Biarritz; pollen and forest fires are out of season in November, so their answers are
built here, labelled as such. The pages are drawn by Node outside a browser (``card_render.cjs``); the look of the card is
checked in a real Home Assistant by the `card` job of the workflow.
"""

from __future__ import annotations

import copy
import html
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from custom_components.vigie.card import CARD_FILE

from .conftest import recorded
from .payloads import forest_fire

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed here (it is in CI)")

HARNESS = Path(__file__).with_name("card_render.cjs")
EVENING_MS = 1_795_378_800_000  # 2026-11-22 20:20 UTC, a few minutes after the reads of Chamonix and Angoulême
CHAMONIX = recorded("point_chamonix_2026-11-22.json")
ANGOULEME = recorded("point_angouleme_2026-11-22.json")


def render(point: dict[str, Any], *, forest: dict[str, Any] | None = None, lang: str = "fr", width: int = 374) -> dict[str, Any]:
    data = {"point": point, "forest_fire": forest, "warnings": None, "earthquakes": None, "tides": None, "normals": None}
    result = subprocess.run(
        ["node", str(HARNESS), str(CARD_FILE)],
        input=json.dumps({"lang": lang, "width": width, "now_ms": EVENING_MS, "data": data}),
        capture_output=True, text=True, encoding="utf-8", check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def text(page: str) -> str:
    """What a page says: its HTML without the tags, the spaces made plain."""
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", page)).replace("\xa0", " ")).strip()


def pollens(peaks: dict[str, list[float]], dates: list[str]) -> dict[str, Any]:
    """``air_quality.pollens`` of ``/v1/point`` with the daily peaks given (grains/m³), the other species at 0."""
    species = ("alder", "birch", "grass", "mugwort", "olive", "ragweed")
    daily = [{"date": date, **{f"{s}_max_grains_m3": peaks.get(s, [0.0] * len(dates))[i] for s in species}} for i, date in enumerate(dates)]
    return {"observed_time": f"{dates[0]}T18:00", "current": {f"{s}_grains_m3": 0.0 for s in species}, "daily": daily, "dominant_species": None,
            "derived": ["daily", "dominant_species"], "stale": False,
            "attribution": "Weather data by Open-Meteo.com (https://open-meteo.com/) · CAMS ENSEMBLE data provider (Copernicus Atmosphere Monitoring Service)"}


def test_chamonix_shows_the_avalanche_bulletin_but_not_a_drought_watch() -> None:
    assert CHAMONIX["avalanche"]["max_danger_level"] == 4 and CHAMONIX["drought"]["max_level"] == "vigilance"
    out = render(CHAMONIX)
    assert out["items"] == [{"id": "avalanche", "tone": "rouge"}]  # a watch asks for no restriction: nothing to say
    home = text(out["home"])
    assert "Avalanches · risque 4 (fort) · Mont-Blanc" in home and "Sécheresse" not in home
    assert re.search(r'<button class="puce" data-go="alerts"[^>]*>[^<]*<svg[^>]*>.{0,200}?</svg><span class="tx">Avalanches', out["home"])
    alerts = text(out["alerts"])
    assert "De saison" in alerts and "Avalanches 4 · fort" in alerts
    assert "Secteur : Mont-Blanc · Neige fraîche" in alerts and "Bulletin valable jusqu’à demain 18 h" in alerts
    assert "distinct de la vigilance" in alerts and 'href="https://meteofrance.com/meteo-montagne"' in out["alerts"]
    assert "Données : Météo-France" in alerts


def test_the_same_in_english() -> None:
    out = render(CHAMONIX, lang="en")
    assert "Avalanches · danger 4 (high) · Mont-Blanc" in text(out["home"])
    alerts = text(out["alerts"])
    assert "Seasonal" in alerts and "Area: Mont-Blanc · New snow" in alerts and "Bulletin valid until tomorrow 18:00" in alerts


def test_an_avalanche_bulletin_past_its_end_is_not_shown() -> None:
    point = copy.deepcopy(CHAMONIX)
    point["avalanche"]["valid_until"] = "2026-11-22T17:00:00Z"
    assert render(point)["items"] == []


def test_angouleme_shows_its_drought_restrictions() -> None:
    assert ANGOULEME["drought"]["max_level"] == "alerte_renforcee" and ANGOULEME["drought"]["restrictions_in_force"] is True
    out = render(ANGOULEME)
    assert out["items"] == [{"id": "drought", "tone": "orange"}]
    assert "Sécheresse · alerte renforcée · restrictions d’eau" in text(out["home"])
    assert re.search(r"Données : [^·]*VigiEau", text(out["home"]))  # the line of the home page credits its source too
    alerts = text(out["alerts"])
    assert "Des restrictions d’usage de l’eau sont en vigueur." in alerts and "Eau potable jusqu’au 30 nov." in alerts
    assert 'href="https://vigieau.gouv.fr"' in out["alerts"] and "VigiEau" in alerts


def test_biarritz_has_nothing_of_the_season() -> None:
    out = render(recorded("point_biarritz_2026-11-22.json"))
    assert out["items"] == [] and "De saison" not in text(out["alerts"]) and "Sécheresse" not in text(out["home"])


def test_pollen_from_one_grain_per_cubic_metre_from_today_on() -> None:
    point = copy.deepcopy(ANGOULEME)
    # The mugwort peaks recorded at Malaga on 2026-11-15 (Open-Meteo, CAMS), on the days of the read; yesterday is left out.
    dates = ["2026-11-21", "2026-11-22", "2026-11-23", "2026-11-24", "2026-11-25"]
    point["air_quality"]["pollens"] = pollens({"mugwort": [9.0, 4.6, 2.5, 4.7, 0.5], "grass": [0.0, 0.9, 0.8, 0.0, 0.0]}, dates)
    out = render(point)
    assert out["items"] == [{"id": "drought", "tone": "orange"}, {"id": "pollen", "tone": "neutre"}]
    assert "Sécheresse · alerte renforcée +1" in text(out["home"])  # the strongest line, "+1" for the other
    alerts = text(out["alerts"])
    assert "Pollens Armoise" in alerts and "4,7" in alerts and "9,0" not in alerts and "Graminées" not in alerts
    assert "pas un comptage" in alerts and "Copernicus" in alerts
    point["air_quality"]["pollens"] = pollens({"mugwort": [9.0, 0.9, 0.5, 0.2, 0.0]}, dates)
    assert [item["id"] for item in render(point)["items"]] == ["drought"]


def test_forest_fires_from_a_moderate_danger() -> None:
    out = render(ANGOULEME, forest=forest_fire(2, 1))
    assert out["items"] == [{"id": "drought", "tone": "orange"}, {"id": "fire", "tone": "jaune"}]
    alerts = text(out["alerts"])
    assert "Feux de forêt modéré" in alerts and "Demain : modéré" in alerts and "Après-demain : faible" in alerts
    assert "Département : Aude" in alerts
    assert render(CHAMONIX, forest=forest_fire(3, 4))["items"][0] == {"id": "avalanche", "tone": "rouge"}  # 4 of 5 and 4 of 4: both red
    assert [item["id"] for item in render(ANGOULEME, forest=forest_fire(1, 1))["items"]] == ["drought"]
    assert [item["id"] for item in render(ANGOULEME, forest=forest_fire(None, None, reference_time=None, status="out_of_season"))["items"]] == ["drought"]
    # A map published three days ago speaks of days already past: nothing.
    assert [item["id"] for item in render(ANGOULEME, forest=forest_fire(3, 3, reference_time="2026-11-19T14:50:05Z"))["items"]] == ["drought"]


def test_the_sources_go_on_the_title_line_only_when_they_fit_whole() -> None:
    point = copy.deepcopy(ANGOULEME)
    point["air_quality"]["pollens"] = pollens({"mugwort": [0.0, 4.6, 2.5, 4.7, 0.5]}, ["2026-11-21", "2026-11-22", "2026-11-23", "2026-11-24", "2026-11-25"])
    four = '<span class="src">Météo-France, VigiEau, Open-Meteo, Copernicus</span>'
    phone, wall = render(point, forest=forest_fire(2, 1)), render(point, forest=forest_fire(2, 1), width=1200)
    assert four not in phone["alerts"] and four in wall["alerts"]  # on a phone they would be cut: the foot credits them
    assert "Données : Météo-France, VigiEau, Open-Meteo, Copernicus" in text(phone["alerts"])
    assert '<span class="src">VigiEau</span>' in render(ANGOULEME)["alerts"]


def test_on_a_wall_screen_the_season_is_under_the_warnings() -> None:
    wall, phone = text(render(ANGOULEME, width=1200)["alerts"]), text(render(ANGOULEME)["alerts"])
    assert wall.index("De saison") < wall.index("Crues") and phone.index("Crues") < phone.index("De saison")
