- **The Map page of the dashboard card, its data** (Home Assistant integration, ADR 0048): the integration serves MapLibre
  GL JS 6.14 beside the card (`/local/vigie/maplibre/`, compressed; never loaded by the other pages), every spot of the
  instance with its verdicts and the figures of its hour (`vigie/card/map`), the places of the French coast
  (`vigie/card/places`, read once a day), and keeps the beaches of the Sea page for each entry in Home Assistant's storage,
  shared by every screen (`vigie/card/favorites`, `/set`, `/subscribe`). A beach of the Sea page can now be any spot of the
  instance, not only one within 60 km of the place.
