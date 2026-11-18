# 0031. Hail, gusts, reduced visibility and type of precipitation of the next hours from Météo-France's AROME-PI, read by the gateway and shown as levels by this API

- Status: accepted
- Date: 2026-11-18
- Number: reserved with `scripts/prochain_adr.py` and in the state file of the sessions.

## Context

The owner decided on 2026-11-18 to plug the Météo-France APIs the project subscribes to, a lot at a time. This is the lot of AROME-PI, the "prévision immédiate" model of Météo-France. A point in France already had the rain of the next hour (PIAF) and the days (AROME, ARPEGE); it had nothing on what makes a short trip or an outdoor plan dangerous within hours: hail, strong gusts, fog and the type of precipitation (snow, freezing rain).

What the API gives (`aromepi/1.0`, a WCS 2.0.1 on `public-api.meteofrance.fr`, read on 2026-11-18 with the project's key):

- One run an hour, 24 steps from 15 minutes to 6 hours, 1.3 km. **One step of one parameter per request** (a time range answers 404), as a GRIB2 map with simple packing; the grid of the whole of France is 7 MB.
- The four parameters used: `WIND_SPEED_GUST__SPECIFIC_HEIGHT_LEVEL_ABOVE_GROUND` (at 10 m, `height(10)` is required), `DIAG_GRELE__GROUND_OR_WATER_SURFACE` (hail diagnostic, kg/m2), `VISIBILITY__GROUND_OR_WATER_SURFACE` (m), `PRECIPITATION_TYPE__GROUND_OR_WATER_SURFACE` (code).
- The quota of the key is 50 requests a minute **for all the Météo-France APIs** (PIAF, radar and UV share it). A run is 96 maps.
- A run is published progressively: the first steps appear about 25 minutes after the run's hour, the last ones minutes later. A step that is not there yet answers 404, and a `DescribeCoverage` that lists the run does not say that its last step exists.
- The codes of the type of precipitation are the WMO table 4.201 (1 rain, 3 freezing precipitation, 5 dry snow, 6 wet snow, 7 rain and snow, 8 ice pellets, 9 snow pellets, 10 hail, 11 drizzle, 12 freezing drizzle) and local codes (193 and 201 to 213, "intermittent"), from the Open Data page "Comprendre les diagnostics de type de précipitation dans les modèles AROME" (read on 2026-11-18). The hail diagnostic turns into "small hail" at 8 kg/m2 and "hail" at 16 kg/m2 in that diagnostic of Météo-France.

## Decision

1. **The gateway does the heavy work** (`Axellum/vigie-meteo`, `GET /v1/shortterm?lat=&lon=`): it downloads each run **once** (96 requests, paced at 20 a minute, two at a time, decoded one at a time), reduces each map to blocks of 4 x 4 nodes (about 4 x 3 km: the strongest gust, the highest hail value, the lowest visibility, and the type of the centre node unless a freezing or hail code is in the block), keeps the last three runs on disk (3 MB each) and the newest in memory (about 16 MB), and answers a point from memory in about 35 ms. The cost does not depend on the number of points. A run is kept only when its 96 maps are there; a run that is not complete yet is retried every three minutes, the maps already read are kept between tries. It answers 404 outside the map and 503 without a run younger than three hours.
2. **This API relays and works out levels** (`app/collectors/meteofrance_shortterm.py`): `short_term` of `GET /v1/point`, only for a point in the France box and only with `VIGIE_METEO_GATEWAY_URL`. It checks the answer (it is AROME-PI's, no impossible value, otherwise it is refused as a whole), caches it per cell of the forecast grid for five minutes, and drops the steps already past. Levels:
   - gusts `strong` from 60 km/h and `very_strong` from 90 (**ours**: a gust of 60 km/h is felt on foot, 90 km/h breaks branches; not the department-dependent levels of the wind warnings);
   - hail `small_hail` from 8 kg/m2 and `hail` from 16 (**Météo-France's own thresholds** of its diagnostic);
   - visibility `reduced` under 1,000 m (the usual definition of fog) and `very_reduced` under 200 m (**ours**; the model does not say whether it is fog or heavy precipitation);
   - the type of precipitation as the spans of each type other than `none` (rain, drizzle, freezing, snow, wet snow, rain and snow, ice pellets, snow pellets, hail); a code the table does not list is `unknown`, never guessed.
3. **A forecast, never a warning**: the block is named after the model, carries a `note` saying so, never enters `local_alert_level` (a test holds it), is not in the frame or the integration (no public field of them changes), and the credit of Météo-France is added to the point's. The wording to use is "prévision du modèle", never "alerte".
4. **The compact profile (the default) keeps the levels and leaves out the 24 steps** (about 2.5 KB) so that `GET /v1/point` keeps to its 16 KiB; `compact=false` adds them.
5. **The site** shows one line per signal under the rain of the next hour ("Prévision du modèle, prochaines heures"), and nothing when the model forecasts none.

## Checked

- The pooling, the geometry (the grid is 413 x 263 nodes of 0.04 degree after pooling; a point is read at the node that holds it, the north-south order of the file is flipped), the store, the pacing, the incomplete run that resumes, a failing source that never shows the key, and the route (404, 503, past steps dropped) by unit tests of the gateway (20) and of this API (54).
- A **real run** (2026-11-18 08:00 UTC) downloaded by the gateway: 96 requests, 3.0 MB on disk, answers in about 35 ms; the maps of Lille, Calais and Brest show gusts of 60 to 74 km/h and rain, Marseille a visibility of 9 km at minimum, the others nothing, as the weather of that morning was.
- **On the gateway of the Oracle server** (container limited to 1 GiB, deployed 2026-11-18 at 09:53 UTC): the first run (09:00 UTC) was complete 4 minutes after the start (96 requests at 20 a minute), 3.06 MB on disk; the memory of the container went from 146-170 MB after the restart to 230 MB at the highest of the samples taken during the first download; with the radar and forecast caches warm again the container stood at about 517 MB, rose to **604 MB at the highest of the samples (every 15 s) while the second run (10:00 UTC) was downloaded** and stayed at 580 MB after it, the new run in memory, against 582 MB before the deployment and a limit of 1 GiB: about 40 % of the limit is left; a point is answered in 3 ms; outside the map 404; the disk of the server did not move (23 GB used of 96).
- **Forecast against what the stations measured** (`tools/measure_aromepi.py` of the gateway, DPPaquetObs at 6 minutes, 2026-11-18 from 03 to 08 UTC, runs issued 1, 3 and 6 hours before, 4,176 gust pairs, 833 visibility pairs, 10,261 rain pairs; one morning only):
  - gusts: the model's value (the strongest of a 4 x 4 block) **exceeds the station's** `raf10` by 10 km/h on average (observed mean 34 km/h; RMSE 15.6, 15.7 and 16.2 km/h at 1, 3 and 6 hours). A gust of 60 km/h or more was forecast where one was measured 69 % (1 h), 70 % (3 h) and 72 % (6 h) of the time (hits / (hits + misses)), and 69 to 71 % of the forecasts of 60 km/h were not measured at the station (false alarms / (hits + false alarms)): the block takes the strongest of 16 nodes and a station is one point, so the figure shows that a level is "somewhere around", not "at the door". Of 90 km/h and more there were 33 observations: too few to say anything;
  - visibility under 1,000 m: 23 of the pairs had it measured; 16 of them were forecast at 1 h and 6 h (70 %), 10 at 3 h (43 %), and 58 to 68 % of the forecasts were not measured; too few events to conclude;
  - rain (the model's type is not `none` against 0.2 mm or more measured in 18 minutes): 66 to 68 % found, 56 to 69 % of the forecasts not measured;
  - the forecasts do not get worse from 1 to 6 hours in this sample, which is a sign of the sample's size rather than of the model's skill.

- **On the VPS** (vigie-api `71e4b03`, deployed 2026-11-18 at 10:22 UTC, previous image `d497634` kept): `/v1/health` `ok`, 48 sources of 48 healthy; `GET /v1/point` for Lille carries `short_term` (strong gusts, rain, the credit of Météo-France, no steps in the compact profile, 8.5 KB in all) and for Oslo none; the response time of a point is 0.30 s with or without it (Lille and Oslo, second request), 0.8 s for a new cell of France; no error in the journal of the first minutes. The site (`/data/point.json`, then the page `Près de moi`) shows the line "Prévision du modèle, prochaines heures" for Lille and nothing for Oslo.

## Not verified

- **Hail**: no ground station measures it, so the diagnostic and its thresholds were not compared with anything; they are Météo-France's.
- One morning of one day: the figures above say nothing of a thunderstorm or a fog night; they are repeated when the next stormy day comes.
- The type of precipitation other than rain (snow, freezing): the sample had none.
- The gateway's memory and disk on the production machine over several days, and the load of the 96 requests an hour on the key's quota shared with PIAF, the radar and the UV index (the pacing keeps the total under the 50 a minute; one run measured, not a day).
- The licence of the API: verified afterwards, on 2026-11-18 (lot E). The licence document of the API on the portal (`licence_en`) names the Licence Ouverte Etalab 2.0: reuse, commercial included, with the source and the date of the last update named and the data not altered. `commercial.py` says "allowed" with that document as proof.
- The model against a point away from a station, and in the mountains, where a block of 4 x 3 km mixes valley and ridge.
