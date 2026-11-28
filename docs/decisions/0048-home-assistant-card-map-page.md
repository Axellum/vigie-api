# 0048. Home Assistant: a Map page in the card, MapLibre served by the integration, the kept beaches in its storage

- Status: accepted
- Date: 2026-11-28
- Number: given by `scripts/prochain_adr.py` on 2026-11-28.
- Extends [ADR 0042](0042-home-assistant-weather-of-the-place-and-card.md) (the card's data come from the integration's
  commands) and [ADR 0044](0044-home-assistant-card-as-a-dashboard-resource.md) (the files beside the card in `www/vigie/`).

## Context

The card's Sea page shows up to six beaches, ticked in the editor among the spots within 60 km of the place. The author wanted
a page that shows the spots and the places of the coast on a map, like the `/sea` page of the website and the Android app: a
touch on a point opens a short sheet, then the Sea page of that point, and a star keeps it among the beaches of the Sea page.

Three ways were tried on 2026-11-28, with a prototype of the page rendered by the real MapLibre and the real tiles, and
measured on a real Home Assistant (2026.11) through a local relay that replaced only the card, for one test browser:

1. **Home Assistant's own `map` card**: it shows entities and `geo_location` sources, and a click opens their "more info"
   dialog ([its documentation](https://www.home-assistant.io/dashboards/map/)). No star, no Sea page, and 4,000 places do not
   become entities.
2. **A page of the card, drawn by MapLibre GL JS** (the website's library and version, 6.14.0, BSD 3-Clause) on the same
   OpenFreeMap style as the website. Compressed, MapLibre is 439,205 bytes (285,305 the module, 143,415 its worker, 10,485 its
   style sheet). Loaded only when the page opens, it leaves the home page as it was: 6,955 / 4,793 ms (first / second visit)
   without the page, 6,911 / 4,616 ms with it, median of 3 runs on the DevTools "Fast 4G" profile (165 ms, about 8 Mbit/s
   down) with the processor slowed 4 times. The page itself was drawn 2,135 ms after the touch the first time (the code
   608 ms, the background 1,984 ms) and 618 ms the next time, with nothing downloaded again: `/local` is kept 31 days by the
   browsers (`Cache-Control: public, max-age=2678400`) and served compressed.
3. **The website's `/sea` page in a frame**: the website sends `Content-Security-Policy: frame-ancestors 'none'`, so a browser
   refuses to show it; it would also keep its stars on the website, and need the Internet and a login in the frame.

Where to keep a star was weighed too: written into the dashboard's configuration (`lovelace/config/save`) it needs an
administrator, rewrites the whole dashboard (and can overwrite a change being made elsewhere) and cannot work for a dashboard
in YAML; kept per user (`frontend/set_user_data`), the tablet of the hall would not see the stars of the phone.

## Decision

1. **The Map page is a page of the card**, drawn by MapLibre GL JS 6.14.0. **The integration ships MapLibre** (the three files
   of the npm package, unchanged, with its licence: `frontend/maplibre/`) and **serves it beside the card**: copied to
   `www/vigie/maplibre/` with gzip copies (compressed again only when a file changed), and at `/vigie/maplibre/` for a card
   loaded from the integration's path. The card imports it from its own address, with the version in the URL, **only when
   the Map page opens**. No CDN.
2. **The data of the page come from two commands**, read through the entry like the others: `vigie/card/map`, every spot of
   the instance with the verdict of each activity (the best of the daylight hours of the next 24, `/v1/activities/overview`,
   the verdict of the website's pins) and the figures of the hour under way (`/v1/surf/overview`), kept 30 minutes; and
   `vigie/card/places`, the places of the French coast (`/v1/sea/places`, the whole catalogue by pages of 500, as short lists),
   kept a day. The card still never calls the instance.
3. **The kept beaches belong to the entry**, in Home Assistant's storage (`.storage/vigie.favorites`), read with
   `vigie/card/favorites`, written with `vigie/card/favorites/set` (at most 6 spots of the instance, in order) and followed
   with `vigie/card/favorites/subscribe`, so every open card follows a star at once. Until a list is written, the card keeps
   the `beaches` of its configuration; the editor reads and writes the same list. Removing the entry removes it.
4. **A beach of the Sea page can be any spot of the instance** (`vigie/card/beach`), not only one within 60 km: a star can be
   put on a beach far from home. The editor still offers the spots within 60 km.
5. The page's background is OpenFreeMap's (no key, no cookie, the website's style), asked by the browser of the screen when
   the page is shown, like any map. The page says where its background and its points come from.

## Consequences

- The integration grows by 1.6 MB of MapLibre (0.44 MB compressed in `www/vigie/maplibre/`); the home page does not load a
  byte of it. Its digests are checked by the tests (`tests_ha/test_map.py`); updating it is described in
  `frontend/maplibre/README.md`.
- Opening the Map page asks the instance at most two overviews every 30 minutes and nine pages of places a day, for every
  screen together.
- The Map page needs WebGL 2, which MapLibre 6 requires; a screen without it keeps the other pages, and the Map page says
  that the map is not available on it.
- The stars are shared by everyone who sees the entry's card: that is the point (the hall and the phone), and also its limit
  (two people cannot keep different beaches for the same entry).
- Not done: a Sea page for a bathing site that is not a spot (no surf forecast there), which would need its own answer.
