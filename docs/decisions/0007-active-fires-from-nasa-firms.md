# 0007. Active fires: NASA FIRMS, read from its static files, served from memory

- Status: accepted (the shape of the routes was asked for by the site that draws them; the refresh rhythm is the author's, to be confirmed on a longer record). First written on 2026-10-20 for the area API of FIRMS and a key; **changed the same day** to the static files of FIRMS, after the deployed instance saw the area API stall (see "What the first version did"). This is the current decision.
- Date: 2026-10-20

## Context

The site wants the fires that satellites see now, on a map and around a position. NASA's FIRMS publishes them for free. A world file of one
day weighs 5 MB and 50,000 rows, and FIRMS is slow and loses connections: a read can never be assumed to answer.

FIRMS offers the same detections two ways:

- the **area API** (`/api/area/csv/{MAP_KEY}/{SOURCE}/world/1/{DATE}`): needs a free key, which is **in the URL path**; the server **generates**
  each answer (36 of the 5,000 transactions the key is allowed in 10 minutes for a world day), with no validator;
- the **static files** (`/data/active_fire/{folder}/csv/{prefix}_Global_{24|48|7d}h.csv`, the "Text Files (CSV)" table of the
  [Active Fire Data page](https://firms.modaps.eosdis.nasa.gov/active_fire/), one per product and region): no key, nothing generated, and they
  carry `Content-Length`, `ETag`, `Last-Modified` and `Accept-Ranges: bytes`.

## What the first version did, and why it changed

The first version (lot 9) read the area API for NOAA-20 and NOAA-21, one world day each, today at each refresh and yesterday once it had
settled, with a key. On the deployed instance (a rootless container on a small home server, IPv4 only) a world read of NOAA-20 ended in a `ReadTimeout`
after 90 s (NOAA-21 was fine); from the host, the area API answered **HTTP 504 after 74 s** (the FIRMS gateway) over IPv6 and gave no first byte
in 180 s over IPv4, and the home page of FIRMS alternated between timeouts and answers in 0.7 to 5 s over both. **The FIRMS server is
intermittently slow; this is not only a matter of our network.** The same day, from a second machine of the same home network, the three static files
asked answered in 1.8 s (8.5 MB), 2.2 s on the second try (9.9 MB, the first got nothing in 90 s) and stalled after 40 KB (Suomi NPP).
Of 160 `HEAD` requests of the PC to the four static files, one round every 5 minutes from 20:27 UTC to 00:10 UTC, 22 (14 %) got no answer in 40
seconds (11 of the first 28, 11 of the next 132); the others answered in 2.9 s at the median and 34.5 s at the worst. So the static files stall as
well: what they do not do is cost the server a generation, a key or a transaction, and they let a client ask "has it changed?" before it
downloads 10 MB.

## Decision

1. **Two products: VIIRS NOAA-20 and NOAA-21, near real-time** (`VIIRS_NOAA20_NRT`, `VIIRS_NOAA21_NRT`), 375 m pixels, as before. Suomi NPP
   is not read: NASA announces that its deliveries cease on 2026-11-02. MODIS has another layout and 1 km pixels. **The static files hold the
   near real-time version only** (`2.0NRT`, about 3 hours after the observation): the ultra real-time detections of the United States and
   Canada (within minutes, version `URT`), which the area API returns with them, are **not** in these files (the page says they will be
   "coming soon" in the CSV downloads). That is a loss against the first version, for those two countries, and it is said in the docs.
   `VIGIE_FIRMS_SOURCES` can narrow the two to one; it cannot widen them.
2. **The static files first, and they need no key**: the world file of the last 24 or 48 hours of each source. `VIGIE_FIRMS_WINDOW_HOURS`
   (24 or 48, **48 by default**) says which: it is how far back `hours` can go on this instance, and asking for more is a **422** that says
   so (the answer never claims to cover more than the file does; the file of 24 hours goes back at least 24 hours at any time of the day,
   and 40 at 20:00 UTC). 48 is the default so that `hours` up to 48, the documented limit, still works; the 24 hour files cost two thirds of
   its bytes.
3. **Each source is checked at each refresh (60 minutes by default) and downloaded only when it changed.** FIRMS writes a file once an hour.
   The server does **not** answer `304` to `If-None-Match` or `If-Modified-Since` (measured: `200` and the whole file, twice), so one request
   is made and its **headers decide**: when the `ETag` is the one held the connection is closed before the body (one request, no body; a 304
   is believed if a server ever sends it). When it changed, **only what was added is read**: a file of FIRMS grows within a UTC day (the file of
   NOAA-20 written at 20:40 was the one of 19:40 and 257,185 bytes more, byte for byte) and its `ETag` is the MD5 of the whole content, so
   a `Range` request with `If-Range` asks for the bytes after the held ones, and what is held plus what came is checked against the `ETag`. It
   is that file bit for bit, or the whole file is read: **a wrong file is never assembled**. That check is what protects, not `If-Range`: on
   2026-10-23 the server answered `206` to a request whose `If-Range` was not the file's `ETag` (it ignores that header; `Range` itself is honoured, and
   a range that starts at the end of the file is a `416`). At midnight UTC the oldest day leaves the file,
   which then is not an extension of the held one: it is read whole, once a day.
4. **A server that stalls costs a source a minute, not the answers.** A read ends when no byte comes for 25 s (`READ_TIMEOUT_S`) or after
   90 s in all (`TOTAL_DEADLINE_S`). A source that failed is asked again after **1, 2, 5, then every 10 minutes**, **it alone**: the other
   source is neither delayed (they are asked at the same time) nor read again; the failed one keeps serving its last reading, marked `stale`.
   A download that stopped in the middle is kept (at most 15 minutes) and continued with `Range` and `If-Range` rather than started again.
   Nothing is asked while every source that is not fresh is in its backoff (a request for the fires costs FIRMS nothing then). The generic
   circuit breaker of the collectors is not used for FIRMS: its three-failures-ten-minutes rhythm would have overridden the backoff of the
   source that failed.
5. **The key is optional: a second way to the same detections when the file cannot be read.** With `VIGIE_FIRMS_MAP_KEY`, a source whose
   static file failed is read from the area API (today and yesterday, 72 transactions, held to the quota of the key as before) instead of being
   left without data; the next refresh tries the file first again. What the key adds is **not** more detections (the area API holds the
   same near real-time ones, and the ultra real-time ones of the United States and Canada are not merged: that would need a measure of the
   overlap that was not made) and it is **not proven more reliable** (it is the path that stalled): it is a second path. Without a key the
   area API is never asked. The key is in the URL path of every such request, so it is redacted as before in four places (the messages are
   built without the URL; the exceptions of the HTTP client are cleaned before they are recorded and raised outside the `except` block; `last_error`
   and the base class go through the same cleaning; the log records of `httpx` and `httpcore` pass a filter), and `tests/test_firms_secret.py`
   looks for it everywhere, with two controls that show it *would* appear without the cleaning.
6. **On by default, off with `VIGIE_ENABLE_FIRMS=0`.** A source that downloads tens of megabytes an hour is not for every instance, and an
   instance that turned it off says so as before (`404` with the reason, `fires_nearby` null, `disabled_sources` in `GET /v1/health`). The
   application of the tests turns it off.
7. **The rows are kept as before, and so is the last file.** Parsed in a thread (0.36 s for a file of 24 hours, 0.58 s for 48) into typed columns sorted by
   latitude (41 bytes a row, measured on 2026-10-23: 11 MB for the two files of 24 hours, 16 MB for 48), served from memory; a request never waits for FIRMS once
   something was read, only the first one does (`/v1/fires`) or answers without the block (`/v1/point`). **The bytes of the last file of each
   source are kept too** (8 to 15 MB each), because reading only what was added needs them to be compared with the `ETag`; kept only when the
   `ETag` is an MD5, dropped when the area API takes over.
8. **Fires are not hazards, and a detection is not an alert**: unchanged (own routes with a box, a window, a confidence floor, a power floor and
   a cap; a grid for small zooms; `fires_nearby` named in `derived`, never raising `local_alert_level`; NASA's acknowledgment and disclaimer on
   every answer).
9. **Each source says how it stands** in `metadata.sources`: `read_utc` (when its data was last confirmed current: downloaded, or found
   unchanged), `ok`, and two new fields, `via` (`static` or `area`) and `published_utc` (the `Last-Modified` of the file read: the detections
   are from before it).

## Consequences

- **Bytes and requests a day**, by the simulation of 1,000 clients (`tests/test_fires_sobriety.py`; measured model, see `docs/data-sources.md`):
  the first version read 60 world days, 2,160 transactions and about 170 MB (289 MB at most); this version, with the files of 48 hours, makes
  86 requests, **no transaction of a key** and about **56 MB**, of which 28 MB are the first reading and 20 MB the file that is read whole after
  midnight; with the files of 24 hours, 89 requests and 36 MB. The same day with a server that did not honour `Range` would cost 509 MB, and the
  files read whole each hour would be the 450 to 650 MB that the size of the files (8 to 15 MB each, where a world day was 5 MB) suggests.
  A refresh every 30 minutes makes 134 requests and the same 56 MB: a check that finds the file it holds costs its head (about 770 bytes
  with the request, without the TLS handshake).
- **Seen on a real run** (2026-10-23, no key, refresh every 10 minutes, the files of 48 hours): first reading of the two files 33.1 MB in 3.6 s; a check
  that found them unchanged 2.8 s; at the next writes (18:40:31 and 18:43:46 UTC) the added bytes only, 245,128 and 371,274 (`206`, the `ETag` check
  passing), instead of 16.6 and 17.1 MB, in 2.9 s. The world grid answered in 0.22 s, a box over Europe in 0.15 s.
- The data are fresher by no more than FIRMS publishes: a file is written once an hour at about :40, with detections up to 2.3 to 3.4 hours old.
- The memory of the process grows by the columns and by the last file of each source. Measured on 2026-10-23 on a local instance with no key, a few
  minutes after its first reading of the files of 48 hours (33 MB for the two): 150 MB against 96 MB for the same instance with `VIGIE_ENABLE_FIRMS=0`, and a
  peak of 246 MB against 113 MB (the first reading, while the file is parsed). A Raspberry Pi or another small machine was not measured.
- A FIRMS that is down is asked at most 4 + 144 times a day per source (284 requests for the two in the simulation), each a head, none a transaction.
- Whether a public instance may serve FIRMS data is **not verified** (the page that issues the key could not be read; the page of the files
  says "Register for an Earthdata Login to start downloading data" and the files answered with no credential on 2026-10-20: whether that stays so
  is not known). Until it is, this is for an instance that its owner uses.
- The weekly live check (`tools/check_live_feeds.py`) now needs no key: it reads one static file (8 to 15 MB) and, with a key, one world day of the
  area API.
- If NASA retires NOAA-20 or adds a satellite, the list of products is one table (`FILES` in `app/collectors/firms.py`).

## Alternatives not taken

- **The regional files** (`Europe_24h` is 83 KB for NOAA-21): small, but this API answers any box of the world, which does not follow the
  regions of FIRMS; reading by region is a possible follow-up for an instance that follows one part of the world.
- **The 7 day file** (34 MB): nothing asks for it.
- **A `HEAD` request before each download**: it stalls as well as a `GET`, and a changed file would cost two requests where closing the first
  costs none more.
- **Waiting for the first source to answer before serving**: the first request waits for both, at most 25 s of silence each.
- **Merging the ultra real-time detections** of the area API with a key: needs a measure of how they overlap the near real-time ones when those
  arrive, which was not made.
- **Reading only the box asked** of the area API: each box would be a request to FIRMS, with the key, for data that changes once an hour.
- **A shorter refresh to follow the passes**: FIRMS writes once an hour; a check every 30 minutes costs requests and no bytes, but shows
  nothing sooner.

## Not verified

- **The update rhythm** is the one of the writes seen by the poller of 2026-10-20 (every 5 minutes from 20:27 to 00:10 UTC, files of both sources):
  NOAA-20 at 20:40, 21:40, 22:40, 23:40 UTC, NOAA-21 at 19:43, 20:43, 21:43, 22:43, 23:43 (always within the same minute): once an
  hour at about :40 and :43, and the two files of a source (24 and 48 hours) written within 2 seconds of each other, over four hours; nothing was
  measured at other hours or over a whole day, and the write after midnight UTC was not observed.
- **What the window of a file is**: seen once (20:17 UTC): the file of 24 hours began at 00:07 of the day before, the one of 48 hours at 00:01 two
  days before (the rows whose date is at least the date of 24 or 48 hours before the writing). Around midnight it was not observed, nor how the
  rows leave: the check against the `ETag` makes a file that is not an extension harmless, and costs a whole read.
- **That the files stay open** (no credential) and that **the delay of the first detections** (2.3 to 3.4 hours in the files read on 2026-10-20 and 2026-10-23) is the same
  for all regions.
- The parse time (0.36 to 0.58 s) and the memory are those of the development PC, not of a Raspberry Pi or another small machine that may run it.
- The unit of `frp` for VIIRS (megawatts, as the FIRMS FAQ says for the other products) was not checked in the attribute table.
