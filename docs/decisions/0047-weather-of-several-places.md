# 0047. The weather of several places in one request, for the weather icons of a map

- Status: accepted
- Date: 2026-11-28
- Number: given by `scripts/prochain_adr.py` on 2026-11-28.

## Context

The map of the site is to show the weather of the towns it displays, as small icons (an icon, the temperature, the rain of the
next hours, a strong gust), with a short card on hover and the dashboard card of Home Assistant on a click. A map of France at
the zoom of the home page shows thirty to forty towns. The API had no request for more than one place: `/v1/point` answers one
place with its hazards, marine, air and fires, and the server in front of it limits it to 10 requests a minute and 120 an hour
per address (sketches of 2026-11-28: 12 of 55 towns could not be read in a row before the limit).

Two ways were weighed: asking a weather model for many coordinates at once (Open-Meteo takes lists), or reading each place as
`/v1/point` reads it. The first is cheaper per place, but in France `/v1/point` reads the Météo-France models through the gateway,
adds the PIAF rain of the next hour and keeps each forecast cell in a cache: an icon read another way would disagree with the card
opened on a click.

## Decision

1. **`GET /v1/points`** takes 1 to 40 places as repeated `at=lat,lon` (WGS84) and answers, for each, in the order asked: its
   position, offset, provider, `current`, `rain_nowcast` (without the radar), `hours` coming hours (1 to 72, 36 by default; the
   fields an icon and a short card draw) and `days` days (1 to 14, 5 by default). Anything else is refused with a 422.
2. **Each place is read as `/v1/point` (`compact=false`) reads it**: `fetch_weather_with_fallback` with its hours, the same
   sources, fallbacks, grid and caches. A place therefore agrees with `/v1/point` there, costs what `/v1/point` would cost, and
   nothing when its cell was read in the last minutes; two places of one cell share one reading (the collectors join the
   requests in flight).
3. **Bounded**: at most 8 places are read at a time, and a place is waited for at most 25 s. A place whose reading fails or takes
   longer has `current` null and empty lists; it never takes another's weather, and the others keep theirs.
4. **The weather only.** No hazards, marine, air or fires: the place opened on a click asks `/v1/point`, as now.

## Consequences

- A map of 40 towns costs one request to the API instead of 40, and the providers no more than 40 points would (less once the
  cells are in the cache).
- The server in front of the API needs its own limit for `/v1/points` (one request can read 40 places); it is set with the map of
  the site (vigie-web), whose server reads the towns for its visitors and keeps them in its own cache.
- `rain_nowcast` has no radar tile here: the radar of the map is its own layer.
