"""The Map page of the card (ADR 0048, sketch "Carte · fiche d'abord" of 2026-11-28): the activities as chips, the map's zone and its
credit, the sheet of a spot (its verdicts, its hour, the star, its Sea page) and of a place, and the beaches of the Sea page once
a star wrote them (the list of the entry first, then the configuration's, then the spot a sheet opened).

The spots are what ``vigie/card/map`` answers, made by the integration's own code from the overviews recorded on 2026-11-28, the
places what ``vigie/card/places`` answers for the box around Biarritz. The page is drawn by Node outside a browser, like the
other pages; the map itself (MapLibre, WebGL) is not, it is tried in a real Home Assistant by the ``card`` job.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any

import pytest

from custom_components.vigie.card import CARD_FILE
from custom_components.vigie.weather_data import PLACE_FIELDS, map_place, map_spots

from .conftest import SPOT, recorded
from .test_card_season import text

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed here (it is in CI)")

NOW = 1_795_863_925  # 2026-11-28, the time of the overviews
MAP = {"language": "fr", **map_spots(recorded("spots_2026-11-22.json"), recorded("activitiesoverview_2026-11-28.json"), recorded("surfoverview_2026-11-28.json"))}
CATALOGUE = recorded("seaplaces_catalogue_basque_2026-11-28.json")
PLACES = {
    **{key: CATALOGUE.get(key) for key in ("count", "by_type", "built_utc", "note", "licence", "attribution")},
    "fields": list(PLACE_FIELDS),
    "places": [p for p in (map_place(x) for x in CATALOGUE["places"]) if p],
}
BATHING = next(p for p in PLACES["places"] if p[1] == "bathing" and p[6] == "excellent" and p[4])

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
  const config = { type: 'custom:vigie-card', _pages: PAGE_IDS, ...(i.config || {}) };
  const c = makeContext(i.lang, true, i.width, { point: null }, config);
  const places = placesOf(i.places);
  c.map = i.map; c.places = i.places; c.mapState = i.state ?? null; c.mapAct = i.act || 'surf';
  c.keptReady = i.keptReady ?? true; c.seaWanted = i.seaWanted ?? true; c.keepError = i.keepError || null;
  const spot = i.spot ? i.map.spots.find((s) => s.id === i.spot) : null, place = i.place ? places.find((p) => p.id === i.place) : null;
  c.mapSelected = spot ? { spot } : place ? { place } : null;
  return JSON.stringify({ page: pageMap(c), kept: keptOf(config), tabs: beachesOf(config), places: places.length,
    pin: spot ? ['surf', 'kite', 'boat', 'shore_fishing'].map((a) => mapPinText(c, spot, a)) : null,
    short: spot ? mapNowLine(c, spot, true) : null, pages: availablePages(c, PAGE_IDS),
    off: availablePages(Object.assign(Object.create(c), { mapAvailable: false }), PAGE_IDS) });
})()`, context));
"""


def render(*, lang: str = "fr", spot: str | None = None, place: str | None = None, config: dict[str, Any] | None = None, **c: Any) -> dict[str, Any]:
    result = subprocess.run(
        ["node", "-e", RUNNER, str(CARD_FILE)],
        input=json.dumps({"lang": lang, "width": 480, "now_ms": NOW * 1000, "map": MAP, "places": PLACES, "spot": spot, "place": place,
                          "config": config or {}, **c}),
        capture_output=True, text=True, encoding="utf-8", check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def corsaires() -> dict[str, Any]:
    return next(s for s in MAP["spots"] if s["id"] == SPOT)


def test_the_page_its_activities_its_zone_and_its_credit() -> None:
    out = render()
    page, words = out["page"], text(out["page"])
    assert page.count("data-mact=") == 4 and 'class="mact on" role="tab" aria-selected="true" data-mact="surf"' in page
    assert "Surf Kite Bateau Pêche" in words and '<div class="carte-zone"></div>' in page and "fiche" not in page
    # who the points are from: the spots, their hour and the places (the background says its own in the map's corner)
    assert "Données : Open-Meteo, Copernicus, EOT20, OpenStreetMap, GIP Littoral, Shom, Sandre, ministère de la Santé (data.gouv.fr)" in words
    assert out["places"] == len(PLACES["places"]) == CATALOGUE["count"]
    assert "map" in out["pages"] and "map" not in out["off"]  # hidden with an integration that does not answer the command
    assert "Chargement de la carte…" in text(render(state="loading")["page"])
    assert "Carte indisponible sur cet appareil" in text(render(state="nogl")["page"])
    assert "Map not available on this device" in text(render(state="nogl", lang="en")["page"])


def test_the_sheet_of_a_spot() -> None:
    s = corsaires()
    out = render(spot=SPOT)
    page, words = out["page"], text(out["page"])
    assert f"Plage des Corsaires Biarritz · tournée vers {s['facing_deg']}°" in words
    for activity, name in (("surf", "Surf"), ("kite", "Kite"), ("boat", "Bateau"), ("shore_fishing", "Pêche")):
        verdict = s["verdicts"][activity]
        said = {"good": "Bon", "possible": "Possible", "poor": "Déconseillé", None: "non évalué"}[verdict]
        assert f'aria-label="{name}&nbsp;: {said}"' in page
    n = s["now"]
    hour = f"houle {str(n['swell_m']).replace('.', ',')}&nbsp;m · {n['period_s']}&nbsp;s"
    assert hour in page and f"vent {n['wind_kmh']}&nbsp;km/h" in page
    assert ("La mer descend" if n["tide_trend"] == "falling" else "La mer monte") in words
    # the hour without its words, when the line is cut (a phone's card)
    assert "houle" not in out["short"] and "vent" not in out["short"] and ("descend" if n["tide_trend"] == "falling" else "monte") in out["short"]
    # the figure of each activity on its pin: the swell, the wind twice, the stage of the tide
    assert out["pin"] == [f"{str(n['swell_m']).replace('.', ',')} m · {n['period_s']} s", f"{n['wind_kmh']} km/h", f"{n['wind_kmh']} km/h",
                          {"low": "tiers bas", "mid": "mi-marée", "high": "tiers haut"}[n["tide_stage"]]]
    # the star (not kept yet) and its Sea page
    assert f'data-keep="{SPOT}" aria-pressed="false"' in page and "Garder" in words and f'data-open-sea="{SPOT}"' in page and "Page Mer" in words


def test_the_star_kept_full_or_not_offered() -> None:
    kept = render(spot=SPOT, config={"_kept": [SPOT]})["page"]
    assert f'data-keep="{SPOT}" aria-pressed="true"' in kept and "Gardée" in text(kept)
    six = [f"s{i}" for i in range(6)]
    full = render(spot=SPOT, config={"_kept": six})["page"]
    assert " disabled>" in full and "6 plages au plus : retirez-en une pour garder celle-ci." in text(full)
    # an integration without the kept beaches, a card without its Sea page: no star, no button to it
    none = render(spot=SPOT, keptReady=False, seaWanted=False)["page"]
    assert "data-keep" not in none and "data-open-sea" not in none and 'class="acts"' not in none
    assert "L’étoile n’a pas été enregistrée." in text(render(spot=SPOT, keepError="not_found")["page"])


def test_the_sheet_of_a_place() -> None:
    page = render(place=BATHING[0])["page"]
    words = text(page)
    assert f"{BATHING[4]} Site de baignade" in words and "Qualité de l’eau : Excellente · classe sur 4 ans" in words
    assert "Un lieu de la côte, sans prévision." in words and "data-keep" not in page and "data-open-sea" not in page


def test_the_beaches_of_the_sea_page() -> None:
    # the configuration's until a list is written; the list of the entry then, even empty; the spot a sheet opened, last
    assert render(config={"beaches": ["a", "b", "a"]})["kept"] == ["a", "b"]
    assert render(config={"beaches": ["a", "b"], "_kept": ["c"]})["kept"] == ["c"]
    assert render(config={"beaches": ["a", "b"], "_kept": []})["tabs"] == []
    assert render(config={"_kept": ["a", "b"], "_temp": "z"})["tabs"] == ["a", "b", "z"]
    assert render(config={"_kept": ["a", "b"], "_temp": "b"})["tabs"] == ["a", "b"]
    assert render(config={"_kept": [f"s{i}" for i in range(8)]})["kept"] == [f"s{i}" for i in range(6)]


ROOM = r"""
const fs = require('fs'), vm = require('vm');
const context = { console, HTMLElement: class {}, customElements: { get: () => undefined, define: () => {} } };
context.window = context;
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context, { filename: 'vigie-card.js' });
process.stdout.write(vm.runInContext(`(() => {
  const layers = [], sources = {}, images = {};
  let sets = 0;
  const map = { getSource: (id) => sources[id], addLayer: (l) => layers.push(l), hasImage: (id) => id in images,
    addSource: (id, s) => { sources[id] = { data: s.data, setData(d) { sets += 1; this.data = d; } }; },
    addImage: (id, img) => { images[id] = [img.width, img.height, img.data.length]; } };
  const card = Object.create(VigieCard.prototype);
  Object.assign(card, { _gl: map, _mapData: { spots: [] }, _places: null, _config: { type: 'custom:vigie-card' } });
  card._mapLayers();
  const room = [[{ lon: -1.44, lat: 43.66 }, 119, 26, 'right', [-9, 0]], [{ lon: -1.44, lat: 43.66 }, 14, 10, 'bottom-left', [4, -13]]];
  card._mapRoom(room); card._mapRoom(room);  // the same pins: the map is not given them again
  return JSON.stringify({ layers: layers.map((l) => ({ id: l.id, layout: l.layout || {}, paint: l.paint || {} })), images, sets,
    room: sources.pins.data.features.map((f) => f.properties) });
})()`, context));
"""


def test_the_names_make_room_for_the_pins() -> None:
    # The pins and the stars are HTML over the map, which MapLibre does not see: an invisible icon of their size on the top
    # layer keeps their room, so that no name of a spot, of a place or of the background is drawn under them (seen on the
    # coast of Hossegor on 2026-11-28); a name tries its four sides before it gives up.
    result = subprocess.run(["node", "-e", ROOM, str(CARD_FILE)], capture_output=True, text=True, encoding="utf-8", check=False)
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    layers = {layer["id"]: layer for layer in out["layers"]}
    assert out["layers"][-1]["id"] == "pin-room"
    room = layers["pin-room"]
    assert room["paint"]["icon-opacity"] == 0 and room["layout"]["icon-allow-overlap"] is True and room["layout"]["icon-ignore-placement"] is False
    for label in ("spot-label", "bath-label"):
        assert layers[label]["layout"]["text-variable-anchor"] == ["top", "bottom", "left", "right"] and "text-anchor" not in layers[label]["layout"]
    assert out["room"] == [{"img": "vigie-room-120x26", "anchor": "right", "off": [-9, 0]}, {"img": "vigie-room-16x10", "anchor": "bottom-left", "off": [4, -13]}]
    assert out["images"] == {"vigie-room-120x26": [120, 26, 120 * 26 * 4], "vigie-room-16x10": [16, 10, 16 * 10 * 4]}
    assert out["sets"] == 1
