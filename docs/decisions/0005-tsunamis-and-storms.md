# 0005. Tsunami messages and tropical cyclones: one point on the map, the forecast beside it, one event once

- Status: accepted (the shape of the answers was asked for by the site that draws them; the rest is the author's, to be confirmed on a real storm and a real tsunami)
- Date: 2026-10-20

## Context

Two sources of the U.S. National Weather Service were missing: the **tsunami messages** of the U.S. Tsunami Warning Centers
(the NTWC in Alaska for the United States and Canada, the PTWC in Hawaii for the Pacific and the Caribbean) and the **tropical
cyclones** of the National Hurricane Center (NHC). Both are public, need no key, and say the same thing as sources already
read: GDACS follows storms and large earthquakes, and the NWS relays the domestic tsunami messages as alerts of its own.
Questions: how do these appear next to the hazards and the warnings, how is a message that is not an alert kept from looking
like one, how is a forecast (a cone, a track, arrival times) served to a map that only draws points, how are two reports of one
event told to be one, and what does it cost the providers.

## Decision

1. **A storm or a tsunami is a hazard that is a point.** `GET /v1/geojson/hazards` keeps one `Point` per hazard, as before: a
   storm at its current position (`nhc_<id>`, category `HURRICANE`, the wind in km/h as value), a tsunami at the epicentre of
   its earthquake (`tsunami_<centre>_<event>`, category `TSUNAMI`, no value). Their properties are the usual ones, and
   `has_forecast` says whether `GET /v1/geojson/hazards/{id}/forecast` has anything to draw.
2. **The forecast is another route, an RFC 7946 FeatureCollection.** For a storm: `cone` (Polygon, or MultiPolygon across the
   date line), `track` (LineString, or MultiLineString), `position` (one Point per forecast position: `tau_h`, `valid_utc`,
   `wind_kt`, `gust_kt`, `pressure_hpa`, the stage) and `watch_warning` (the coastal lines under a watch or a warning, with
   the NHC's code). For a tsunami: `epicentre` (Point, with the magnitude and the messages in force) and `eta` (one Point per
   place, `eta_utc`). The collection carries who issued it, the advisory, a link to the issuer's own text and the credit. Nothing
   is drawn that the source did not give: a pressure the NHC writes as 9999 is `null`, a place without coordinates has a `null`
   geometry, and a hazard with no forecast answers an empty collection with a `reason` (an unknown id is a 404). The
   coordinates are rounded to 3 decimals (about 100 m); the cone of Hurricane Isaias's advisory 13A is 21 kB, the whole answer
   under 30 kB, far from the 200 kB limit. The rings of a cone are counter-clockwise (RFC 7946) and a cone across the date line is cut in two
   (§3.1.9); the NHC's own files write longitudes beyond 180 degrees instead.
3. **A tsunami message is also a vigilance, with its words untouched.** `GET /v1/vigilances?source=tsunami` lists every message in
   force; `country=US` or `CA` lists the zones that name them, with the other warnings of the country. The domestic messages (the
   NTWC: warning, advisory, watch) name their coasts with UGC zone codes; the international threat messages (the PTWC) name
   countries in words. No shape of a UGC zone is bundled, so none is matched to a point and the coverage says so
   (ADR 0002). An NWS alert that carries the VTEC code of one of these messages is the same message, and is listed once.
4. **Levels, and what is not an alert.**

   | Message | Words of the centre | Level (ours) |
   | --- | --- | --- |
   | Domestic (NTWC) | Warning | red |
   | | Advisory | orange |
   | | Watch | yellow |
   | International threat (PTWC) | Tsunami >3.0 meters | red |
   | | Tsunami 1-3 meters, Tsunami potential threat | orange |
   | | Tsunami 0.3-1 meters | yellow |
   | Storm (NHC), by the wind | 96 kt or more (category 3 and above) | red |
   | | 64 kt (hurricane) | orange |
   | | 34 kt (tropical storm) | yellow |

   **An information statement is not an alert.** The centres issue many (36 of the 40 bulletins of the list on 2026-10-20): they say
   there is no threat, or that a hazard is not determined. They are counted, never downloaded, never shown as a hazard. A
   cancellation is not an alert either, and nor is a message that is a test or an exercise (only `Actual` messages count), or a
   **final** PTWC message, a CAP with no `info`, which ends the event. A threat class that is in no table is no alert rather
   than a guess. A threat message has no end of its own: it counts for three hours after it was sent (ours; the PTWC says it
   updates "in one hour or sooner"), and a domestic one counts until its `expires`. The PTWC says its threat messages are
   issued for information, in support of the UNESCO/IOC system and meant for national authorities, who decide the alert level for
   each country: the API relays them as information, not as an official warning of the public.
5. **Read the list, then the messages, never the "latest" file.** The per-centre files (`PHEBCAP.xml`, `PAAQCAP.xml`) hold only
   the latest bulletin of any product, so an information statement issued after a threat message would hide the threat. The
   list of the latest bulletins (`previous.js`, asked conditionally) says which bulletin of which product is the newest of each
   event, and only the CAP message of those that can be an alert is downloaded, once (it never changes). A text bulletin is
   downloaded once for a threat message, for its table of arrival times (the CAP of a threat message has none).
6. **One event is one hazard, whoever reports it.** A hazard can carry a private event key: a storm's name and season
   (`cyclone:isaias:2026`, shared by the NHC and GDACS, whose event name ends with the year), or the VTEC code of a tsunami
   message (`vtec:PAAQ.TS.0024`, shared by the centre and the NWS alert that relays it). Two hazards with a key are one event
   when the keys are equal and two when they are not, whatever their times and positions: GDACS dates a storm by its first
   day and places it at its last position, the NHC dates it by its latest advisory, and the NWS places a tsunami warning in
   a zone, not at the epicentre. Without a key the earlier rule stands (within 120 s and 100 km). The first-hand source
   (USGS, the NHC, the tsunami centres) gives the title, the position and, for a keyed event, **the level and the wind of now**: GDACS's
   level and wind are those of the storm at its strongest, over its whole life.
7. **Few requests.** The tsunami list is asked conditionally with a cache of 60 s (looked at every 90 s by the scheduler:
   960 requests a day, all but the first a `304` of 239 bytes, 230 kB), and the NHC file with 300 s (240 a day, 116 kB). A
   bundle is downloaded once per advisory (29.5 kB for Isaias's), a tsunami document once. A bundle that could not be read is
   asked for again after ten minutes, also when the file has not changed (a `304`). Neither provider publishes a rate limit (not
   verified), so there is no budget to hold to; the proof is the simulated day of `tests/test_storm_sobriety.py`.

## Consequences

- The site draws storms and tsunamis as points like every other hazard, and fetches a forecast only for the one a visitor opens.
- A client of `/v1/vigilances` sees a new source, `tsunami`, whose areas are codes and words without a shape: it must read
  `coverage` (and `area.localization`) as for the other sources of this kind.
- The first-hand level wins on a keyed event, so a storm that GDACS marks red at its peak and that the NHC says is now a tropical storm
  is a yellow hazard. For an earthquake, the highest level of the sources still wins (they estimate the same instant).
- **Not verified:** that GDACS's name for a storm always ends with the two digits of the year (it did for the five storms of
  the answer of 2026-10-20), and that its tsunami events (type `TS`) are dated by the earthquake time (GDACS had none for the
  Panama earthquake of 2026-10-09: the rule without a key is tested on a record shaped like the real ones). That the PTWC
  messages of other products than the ones recorded (Caribbean, Indian Ocean, other languages) read the same way: only the
  products listed in `app/collectors/tsunami.py` are read. That the NHC's `CurrentStorms.json` keeps the format of the real
  answer with a storm in the Central Pacific or the Atlantic (the recordings are of the eastern Pacific and the Atlantic). That
  the signature of the CAP messages (CAP-TSU 1.1) is valid: it is not checked.
- Not in this lot: the JTWC (western Pacific and Indian Ocean cyclones), the Indian Ocean and Mediterranean tsunami centres,
  and the Copernicus, INGV and ReliefWeb sources.
