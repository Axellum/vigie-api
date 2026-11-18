# 0032. Probabilities of rain, snow, gusts, hail and storm from Météo-France's ensemble forecasts, read by the gateway and given beside, not merged with, the day's rain probability

- Status: accepted
- Date: 2026-11-18
- Number: reserved with `scripts/prochain_adr.py` and in the state file of the sessions.

## Context

The owner decided on 2026-11-18 to plug the Météo-France APIs the project subscribes to, a lot at a time. This is the lot of the **ensemble forecasts**: Météo-France runs its models 35 times with slightly different starts and the spread of the 35 answers says how sure the forecast is. A point in France had one number of that kind, `daily[].rain_probability_pct`, and it is Open-Meteo's: the highest **hourly** probability of the day, for **any** amount of rain. Nothing said how likely a real downpour, a strong gust or hail were.

What the two APIs give (WCS 2.0.1 on `public-api.meteofrance.fr`, read on 2026-11-18 with the project's key):

- **PE-AROME** (`pearome/1.0`, 2.5 km): 4 runs a day (03, 09, 15 and 21 h UTC), hourly steps to +51 h. Météo-France **already computes the probabilities**: one map per threshold and per step, in percent. The ones used: rain of three hours above 1 mm and above 5 mm ("3 hours liquid precipitation more than 1mm probability", the title of the coverage), snow above 1 mm, gusts above 30 and 50 knots, hail, and a strong shower or thunderstorm (reflectivity above 40 dBZ). One step of one product per request, 0.8 MB each: 7 products x 17 steps of 3 hours = 119 requests a run.
- **PE-ARPEGE** (`pearpege/1.0`, 0.1 degree on Europe): 4 runs a day (00, 06, 12 and 18 h UTC). **One service per member** (`PEARP000` to `PEARP034`), and values, not probabilities: the rain of the last 24 hours of each member at +24, +48, +72 and +96 h. The probability of a threshold is the share of the 35 members that reach it, counted by the gateway (140 requests a run).
- The quota is 50 requests a minute **for the whole key**, shared with PIAF, AROME-PI, the radar and the UV index.
- **A flaw of the PE-ARPEGE service, measured**: with a latitude and longitude `subset` it answers `400 "Lat and long parameters are not allowed"` to a valid request about two times out of three (27 of 40 on one coverage, up to 7 in a row), and never without the `subset` (20 of 20). The message is the same as the one of a real refusal, so a reader that gives up after a few tries takes a published run for an unpublished one (it did, on the first real run). PE-AROME did not show it in the real run below (119 requests in 602 s, which is the pacing, so almost no replay).
- The licence document of each API on the portal (read on 2026-11-18) names the **Licence Ouverte Etalab 2.0**: reuse, commercial included, with the source and the date of the last update named and the data not altered. The same document was read for the other APIs of the project, AROME-PI included (the lot D said "not verified"; it is corrected in this lot).

## Decision

1. **The gateway does the heavy work** (`Axellum/vigie-meteo`, `GET /v1/probabilities?lat=&lon=`): it downloads each run **once**, paced (12 and 10 requests a minute), two at a time, decoded one at a time, and keeps the last runs on disk (`pearome/` and `pearp/`, the file of a run is written whole or not at all, a file lock lets one process download). PE-AROME maps are reduced to blocks of 2 x 2 nodes (0.05 degree, the mean: a probability averages, unlike a gust). **PE-ARPEGE is read without a bounding box**, the whole of Europe (0.8 MB a map, about 110 MB a run), and cropped to the France box on the gateway, because of the flaw above. A run that is not complete is never served.
2. **This API relays** (`app/collectors/meteofrance_ensemble.py`): `probabilities` of `GET /v1/point`, only for a point in the France box and only with `VIGIE_METEO_GATEWAY_URL`. The answer is checked as a whole (the right model, windows of three and 24 hours, every value 0 to 100, a time zone on every date) and refused otherwise, cached per cell of the forecast grid for ten minutes, and the windows already past are dropped. The block has `short_range` (PE-AROME, windows of three hours), `long_range` (PE-ARPEGE, windows of 24 hours, rolling, not calendar days), `rain_peak_next_24h` (the largest `rain_1mm_pct` of the windows that end within 24 hours, the soonest when tied), the credit and a `note`.
3. **`daily[].rain_probability_pct` is not changed and not merged.** The two answer different questions (the highest hour of the day for any amount, against an amount in a window of three or 24 hours), so they can differ, and a merged number would be neither. Where they disagree, **`daily[].rain_probability_pct` is the one the daily table keeps showing; `probabilities` is the one to read for an amount, a gust, hail or a storm.** The `note` and the description of the field say so.
4. **A forecast, never a warning**: never part of `local_alert_level` (a test holds it), not in the embedded frame, not in the Home Assistant integration (they read `/v1/vigilances`, which does not change). The credit of Météo-France is added to the point's.
5. **The compact profile (the default) keeps the next 24 hours of PE-AROME** (8 windows) and all of PE-ARPEGE, to keep `GET /v1/point` under 16 KiB; `compact=false` gives the 17 windows.
6. **The site is not changed in this lot** (the table of the days and the card of the weather are the owner's choice: three sketches are proposed apart). Nothing is shown until he picks one.

## Checked

- Unit tests: the gateway (reduction, geometry, the choice of runs, the store written whole and read back, the readers against a fake WCS, the intermittent 400, the crop of a wider grid, the route and the health) and this API (the answer's validation, the summary, the peak, the compact profile, a single block or one wholly past, the collector, the point, the budget, the credit, the commercial line).
- A **real PE-AROME run** (2026-11-18 03 h UTC) downloaded by the gateway: 119 requests in 602 s (the pacing), 1.4 MB on disk, 151 to 170 MB of resident memory at the highest, the file read back in a new process (`np.savez_compressed(handle, **arrays)` with numpy 2.5.5; the keys are keywords and the file is read whole). At Paris, rain of 1 mm: 13, 11, 19, 27, 17, 3, 71 and 100 % for the windows ending at 06, 09, 12, 15, 18, 21, 00 and 03 h.
- A **real PE-ARPEGE run** (2026-11-18 06 h UTC), read without a bounding box: 140 requests in 842 s (the pacing), no request refused, the 35 members counted into 4 thresholds and 4 steps on the 107 x 167 nodes of the France box. At Lille the probability of 1 mm over the 24 hours ending 2026-11-19 06 h UTC was 100 %, of 5 mm 77 %, of 10 mm 20 %, of 20 mm 0 %, and the PE-AROME windows of that morning gave 97 % for 1 mm in the three hours to 12 h UTC. The answer of the gateway for Lille is kept as the fixture of the API tests (`tests/fixtures/meteofrance_ensemble/`), Oslo answers 404.
- **Against what the stations measured** (`tools/measure_ensemble.py`, DPPaquetObs hourly, 18 November 2026 from 03 to 09 UTC, about 2,000 stations, 5,173 pairs per line; **one morning only**, the observations older than about a day are refused by the service): the probability of the block of 5 km that holds a station against the rain the station measured in the same three hours. The probabilities **rank the cases right** (a higher forecast, a higher share of rains) but are **too high for one exact spot**: rain of 1 mm, runs of 3 to 8 hours earlier, forecasts of 50-80 % came true at the station 11 % of the time (65.5 % forecast on average), of 80-100 % 53 % of the time (93.8 %), of 20-50 % 4 % of the time; the Brier score, 0.28, is worse than always forecasting the sample's own frequency (0.17). Runs 15 to 20 hours earlier were not better (0.29). For 5 mm: 0.079 against 0.048 (3 to 8 h). **Reading the windows as starting at their step, not ending at it, was worse** (Brier 0.36), so the windows end at their step. The reading to give is a chance for the area around the point, not for a point; the note of the API says so.

## Not verified

- **PE-ARPEGE** against the stations: the archive of its members goes back four days at most, and the 24-hour windows are rolling. Only that the 35 members and the thresholds are counted as designed was checked (against a fake service with known members, and the real run above ran to the end).
- The quality of snow, gusts, hail and storm: no ground measure used.
- One morning of one day for PE-AROME: the figures above say nothing of a stormy day or a clear one; they are repeated when the next one comes.
- Whether the probabilities of PE-AROME are computed by Météo-France over a **neighbourhood** (which would explain the excess at a point): the portal's documentation does not say.
- Several days of memory and disk on the production machine, and the load on the key's quota (the pacing keeps the total under 50 a minute).
- The licence was read in the document of each API on the portal, not asked of Météo-France.

## Consequences

- One more optional field on `GET /v1/point`; nothing existing changes (the frame, the integration, `daily`).
- The gateway downloads about 110 MB for PE-ARPEGE and 120 MB for PE-AROME four times a day: an outbound read of a few hundred MB a day at the server of Oracle.
- Rollback: the gateway has a switch for each reader (`METEO_PEAROME_ENABLED=0`, `METEO_PEARP_ENABLED=0`) and the API has none to add: without the gateway answer the block is absent. The previous images stay on the servers.
