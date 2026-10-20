# 0007. Active fires: NASA FIRMS, read with a key that is never shown, served from memory

- Status: accepted (the shape of the routes was asked for by the site that draws them; the refresh rhythm is the author's, to be confirmed on a longer record)
- Date: 2026-10-20

## Context

The site wants the fires that satellites see now, on a map and around a position. NASA's FIRMS publishes them for free, but
unlike every other source of this API it needs a **key** (`MAP_KEY`), and the key is **in the URL path**. A world file of one
day weighs about 5 MB and 50,000 rows, FIRMS loses connections, and its answers have no validator (no `ETag`, no
`Last-Modified`, not even a size on a `HEAD`), so a read can never be a cheap "nothing changed". Questions: which products,
how often, how are 5 MB kept out of the way of a request, how is the key kept out of every message, and what does a
detection mean next to the official warnings.

## Decision

1. **Two products: VIIRS NOAA-20 and NOAA-21, near real-time** (`VIIRS_NOAA20_NRT`, `VIIRS_NOAA21_NRT`), 375 m pixels. Suomi NPP
   is not read: NASA announces that its deliveries cease on 2026-11-02, so it would be a source with a known end date. MODIS has
   another layout and 1 km pixels, so it would be a second parser for fewer, coarser detections. `VIGIE_FIRMS_SOURCES` can
   narrow the two to one (half the volume); it cannot widen them, because a product the parser has not been tried on is not
   read. The "ultra real-time" products (the United States and Canada, within minutes) are not read either: they cover two
   countries and would need a second read for them alone.
2. **One world file per source and UTC day: today, and yesterday until it has settled.** The area API takes a day and a range;
   asking for one day at a time keeps a file addressable by its date. Today is read again at each refresh. Yesterday is read
   once more after it has settled (4 hours after midnight, because the passes of its last hours are published up to
   3 hours late) and then kept: it no longer changes. A read costs 36 of the 5,000 transactions allowed in 10 minutes
   (measured with `mapkey_status`), which `app/collectors/budget.py` knows (90 % of the limit, as for every quota).
3. **Refresh every 60 minutes, when someone asks.** The record is short (see the data sources page): between two reads 46 minutes
   apart on 2026-10-20 the file gained 1,425 detections acquired between 16:05 and 16:30 over South America and the Atlantic,
   published about 2 to 2.5 hours after the pass, while the detections of the last minutes over the Americas were already
   there. A new refresh each hour is well below that delay and halves the volume of one every 30 minutes (about 170 MB a day
   against 320 MB with 1,000 clients). `VIGIE_FIRMS_REFRESH_MINUTES` (10 to 360) is there for whoever wants the other trade.
   Besides the read at start-up (see 4), nobody asking costs nothing: the scheduler does not read FIRMS.
4. **A request never waits for FIRMS once something was read.** The rows are parsed in a thread (0.15 s for a world file) into
   typed columns sorted by latitude (about 43 bytes a row: 2.4 MB for a world file of 55,000 rows, about 10 MB for the four files
   kept; the peak while parsing is 35 to 42 MB). The application starts the first read at start-up (only when it has a key), so
   that the first visitor does not wait for it. When a reading is older than its lifetime the previous one is served at once and the next is read in the
   background, one at a time. Only a request that comes while nothing has been read yet waits (`/v1/fires`) or answers without the block (`/v1/point`).
5. **A cut answer does not replace a longer one.** FIRMS sometimes ends a connection after a valid prefix: a CSV with fewer
   rows. A day's file only grows, so a read with under 90 % of the rows of the previous reading of the same day is
   refused and the previous one is kept.
6. **The key is redacted in four places, and a test looks for it everywhere.** The messages are built without the URL; the
   exceptions of the HTTP client, which can hold it, are cleaned before they are recorded and are raised outside the `except`
   block (no `__context__` that still holds it); `last_error` and the circuit breaker go through the same cleaning
   (`BaseCollector.redact`); and the log records of `httpx` and `httpcore` pass a filter. `tests/test_firms_secret.py` and
   `tests/test_firms_api.py` make a read fail in each of these ways and look for the key in the exception chain, `last_error`, the
   health of the provider, every log record and the answers of the routes. Two controls show that the key does appear in the log
   of `httpx` and in the exception of a transport when nothing cleans them, and removing the cleaning made the tests fail.
7. **Without a key the source is off, and says so.** `/v1/fires` and `/v1/fires/grid` answer 404 with the reason (the name of
   the variable, never a value), `fires_nearby` is `null`, and `GET /v1/health` lists the source in `disabled_sources`. It is
   not a provider "in error": an instance that does not want FIRMS is not unhealthy. A key FIRMS refuses is a reading that fails
   (`503` for a first read, `stale` afterwards), not a 404.
8. **Fires are not hazards.** `GET /v1/geojson/hazards` is loaded whole by the site and would grow by tens of thousands of
   points. The fires have their own routes, with a box, a time window, a confidence floor, a power floor and a cap (the strongest
   first, `metadata.truncated` when cut), and a grid for small zooms (count, highest power and latest time per cell).
9. **A detection is not an alert.** `fires_nearby` of `/v1/point` (count in 24 hours, nearest distance, highest power) is named in
   `derived`, is ours (a count of satellite pixels), and **never** raises `local_alert_level`. NASA says that these data are
   not advised for tactical decisions or local conditions; every answer carries NASA's acknowledgment and that disclaimer, word for
   word (`app/attribution.py`, `SOURCES.md`).

## Consequences

- An instance needs a key to serve fires. The key is free and personal; whether a public instance may serve FIRMS data under one
  personal key beyond the acknowledgment and the disclaimer is **not verified** (the page that issues the key could not be read).
  Until it is, this is for an instance that its owner uses.
- The weekly live check (`tools/check_live_feeds.py`) skips FIRMS without the key, and the workflow has none: nothing watches
  the format of the file by itself. Running the tool with the key does (one world day, 36 transactions).
- The default volume (170 MB a day with someone asking) is far larger than any other source of the API. It is the price of having no validator.
- The detections of the first minutes of a day (UTC) are in yesterday's file for a few hours: a window of 24 hours crosses two
  files, which is why two days are kept and why the window is capped at 48 hours.
- When Suomi NPP stops, nothing changes here. If NASA retires NOAA-20 or adds a satellite, the list of products is one constant.

## Alternatives not taken

- **Reading only the box asked** (`/area/csv/{key}/{source}/{west,south,east,north}/{days}`): each box is a transaction of its
  own, cheaper for a small one, but every visitor of the map would send FIRMS a request, with the key, for a data set that
  changes once an hour. One world read serves everyone.
- **A shorter refresh to follow the passes**: the delay of publication (hours) is far longer than the refresh, so a shorter one
  costs more without showing a fire sooner.
- **Listing the source among the providers with an "off" state**: it would make `/v1/health` report an instance without a key as
  degraded.

## Not verified

- The record of the cadence is two reads 46 minutes apart plus a few longer gaps (many of the requests of that day timed out, from
  this machine and through the HA host); the 2 to 2.9 hours are the delay of the passes seen over the Americas. The delay of the
  other regions was not measured.
- The parse time (0.15 s) and the memory are those of the development PC, not of the Raspberry Pi or the Steam Deck that may run it.
- The unit of `frp` for VIIRS (megawatts, as the FIRMS FAQ says for the other products) was not checked in the attribute table.
