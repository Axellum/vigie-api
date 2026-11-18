# 0033. The weather of the overseas territories from Météo-France's AROME Outre-mer

- Status: accepted
- Date: 2026-11-18
- Number: reserved with `scripts/prochain_adr.py` and in the state file of the sessions.

## Context

Part of the plan of 2026-11-18 to plug every Météo-France API the project subscribes to. Lot A (metropolitan France) gave `GET /v1/point` the current conditions and the days of the Météo-France models; the overseas territories stayed on Open-Meteo's public API, which interpolates global models (about 11 km). Météo-France runs a model of 0.025 degree (about 2.8 km) for each of them. What was read on 2026-11-18, with the project's key:

- `DPPaquetAROME-OM/v1`: five models, one per area (`AROME-OM-ANTIL` for Guadeloupe, Martinique, Saint-Martin and Saint-Barthélemy; `-GUYANE`; `-INDIEN` for La Réunion and Mayotte; `-NCALED`; `-POLYN` for French Polynesia), four runs a day (00, 06, 12, 18 h UTC), 49 hourly steps (+0 h to +48 h). The package `SP1` ("current parameters at the surface") holds what Vigie shows, **one GRIB2 file (CCSDS packing, several fields) per step and per area**, 1.5 to 12 MB each: **245 requests and about 1.1 GB per run**.
- A run is listed from about 6 h 20 after its hour (its first step) and complete for every area at about 8 h 00. The rain (`0-1-52`) is a **cumulative since the run's start**; the gust (`0-2-22`) is the maximum of the hour; step 0 has no gust, no cloud cover and no rain. There is **no weather code, no UV index, no rain probability** in the package.
- Licence: the licence document of the API names the Licence Ouverte 2.0 (Etalab): reuse, **commercial included**, with the source named, the date of the last update given (the run) and the data not altered (read on 2026-11-18).
- Quota: 50 requests a minute for the whole key, shared with PIAF, the radar, AROME-PI and the ensembles.
- Overseas **vigilances** are **not** read by Vigie today: its vigilance collector maps the metropolitan departments only. `DPVigilance` has other endpoints for them (`polynesie/` and `ncaledonie/` JSON maps and texts, and a `vigilanceom/flux/dernier` ZIP), with their own formats. That is a lot of its own and was not part of this one (forecasts).

## Decision

1. **The gateway does the work** ([vigie-meteo#12](https://github.com/Axellum/vigie-meteo/pull/12), `GET /v1/overseas?lat=&lon=`): it downloads each run once (10 requests a minute, spread over about 25 minutes, a file lock so that only one of its two processes downloads, resumed area by area after a restart) and keeps, for **windows drawn by hand around the islands** (Guadeloupe and Martinique, Saint-Martin and Saint-Barthélemy, Guyane, La Réunion, Mayotte, New Caledonia, the Society islands, western Tuamotu, Austral islands; about 102,000 nodes), eight series per node in float16 on disk (about 80 MB per run, two runs kept, read with `mmap`). The hour's rain is the difference of two cumulative steps. A point outside the windows (a boat at sea, the Marquesas, Gambier, Wallis-et-Futuna, Saint-Pierre-et-Miquelon, the TAAF) is a 404 and keeps Open-Meteo.
2. **This API reads the gateway** (`app/collectors/meteofrance_overseas.py`), cached 15 minutes per cell of the forecast grid, only for a point inside a coarse box around the territories (a point elsewhere costs no request). It merges like lot A (`merge_france_bundle`): **the model gives the current conditions and the complete local days** (a day is given only when its 24 hourly steps are all there: usually today and tomorrow, two runs being stitched), **Open-Meteo's public API completes** the rain probability, the UV maximum, the later days and the hourly cloud cover. `rain_next_60m` stays `null`: the 15-minute rain of the nowcast has no source overseas (the radar of Météo-France overseas was not added, see below). `weather_provider` says "Météo-France (AROME Outre-mer)", the credit says so, with the Open-Meteo credit when its part is used.
3. **The weather code is ours.** The model gives none: Vigie derives it from the rain of the hour (≥ 0.1 mm drizzle 51, ≥ 0.5 rain 61, ≥ 2.5 mm 63, ≥ 8 mm 65) then, when dry, from the cloud cover (< 20 % clear 0, < 45 % mostly clear 1, < 75 % partly cloudy 2, else overcast 3); a day takes its most severe hourly code. The credit says it is worked out by Vigie, not a Météo-France product.
4. The time zone of a territory is a **fixed offset** (Antilles -4, Guyane -3, Réunion +4, Mayotte +3, New Caledonia +11, French Polynesia -10): none of them has summer time. Local days are cut with it.
5. Off with `VIGIE_METEO_GATEWAY_OVERSEAS=0`, or without `VIGIE_METEO_GATEWAY_URL`: Open-Meteo gives the weather, as before. A gateway that fails, or does not know the point, leaves Open-Meteo too. Budget: `Quota("vigie-meteo-overseas", 3,000 points per 5 minutes)`, a ceiling of our own.

## Checked

- The CCSDS decoder was checked on the real files of the five areas (plausible values everywhere, for instance 28 °C and 1,013 hPa over the Antilles); the tests rebuild such files byte for byte (13 tests in the gateway, 42 here).
- **Cost on the Oracle gateway** (limit 1 GB for the container, about 583 MiB observed before this work): the container's memory, sampled every 20 s during the whole first download, peaked at 518.6 MiB (182 MiB at the start, 469 MiB once finished and serving), below the 583 MiB seen before this work. Disk: 77 MB for a run on disk (`/data/aromeom`), 73 GB free on the machine. The first full run took about 30 minutes (00 h run, 12:09 to 12:39 UTC) at 10 requests a minute at 10 requests a minute.
- On the gateway, `GET /v1/overseas` answers 200 in 3 to 12 ms for Martinique, La Réunion, Guyane, New Caledonia and Tahiti and 404 for Paris; `/v1/health` shows `meteofrance-aromeom` `fresh`. The public proof through `GET /v1/point` is in the pull request.

## Not verified

- **The windows were drawn by hand** and not compared with a land-sea mask of the model: a point on a coast may read a sea node.
- The model against the stations of the territories (only the plausibility of the values was looked at, not a measure against observations as for lot A).
- The thresholds of the weather code are ours and were not calibrated against observations.
- Only about two local days come from the model (two runs stitched); the other days stay Open-Meteo's.
- The overseas radar (`DPRadar`, areas `ANTILLES`, `REUNION`, `NOUVELLE-CALEDONIE`) was **not** added to the radar of ADR 0030: the radar code reads the metropolitan mosaic only and the overseas ones were never opened here (their grid and projection are to be checked), so it is not a simple extension of the tile code.
