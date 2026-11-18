# 0034. The last days a station measured, from Météo-France's daily climatological data

- Status: accepted
- Date: 2026-11-18
- Number: reserved with `scripts/prochain_adr.py` and in the state file of the sessions.

## Context

The owner decided on 2026-11-18 to plug the Météo-France APIs the project subscribes to, a lot at a time. `GET /v1/climate/normals` (ADR 0027) says what is normal for a month at the nearest station; nothing said what the station **measured** yesterday. `DPClim/v1` ("Données climatologiques") gives the daily, hourly and 6-minute archive of every station of Météo-France, France and overseas. What was read on 2026-11-18, with the project's key:

- The API is **asynchronous**: `GET /commande-station/quotidienne?id-station=&date-deb-periode=&date-fin-periode=` answers 202 with an order number, then `GET /commande/fichier?id-cmde=` answers 204 while the file is made and 201 with a CSV. For Lille-Lesquin (59343001), the file was ready on the second poll, 1.6 s after the order.
- The CSV has `;` for separator, `,` for decimal, one row per day and, for each parameter, a quality code column (`RR;QRR;TN;QTN;...`, 150 columns). The columns kept: `RR` (rain over 24 hours, from 06:00 UTC that day to 06:00 UTC the next), `TN`, `TX`, `TM` (minimum, maximum, mean of the hourly temperatures), `FFM` (mean wind over 10 minutes, at 10 m), `FXI` (highest instantaneous wind), in °C, mm and m/s. The quality code is documented in the daily-data description on data.gouv.fr: `9` filtered, `0` protected, `1` validated, `2` doubtful, under verification.
- Limits (the API's documentation): one year at most per order; yesterday is only available **from 11:30 UTC**; an unknown station is an error on the order (a 404 was seen for a number that does not exist, the documentation says 400); a station without a measure on the period is an error 500 on the file.
- Licence: the licence document of the API names the Licence Ouverte (Etalab): reuse, **commercial included**, with the source and the date of the data named (read on 2026-11-18).
- Quota: 50 requests a minute for the whole key, shared with everything the gateway reads (PIAF, the radar, AROME-PI, the ensembles, AROME-OM).

## Decision

1. **The gateway reads it** ([vigie-meteo#13](https://github.com/Axellum/vigie-meteo/pull/13), `GET /v1/station-daily?station=&days=`): one order per station and per day at most (the last 14 days of the station are kept in its SQLite cache, refreshed when the last available day advances), orders spread out (6 a minute per process) and refused with a 503 past a 10-second wait, a station without data kept empty for the day, a doubtful value (quality 2) left out (`null`) and named in `doubtful`. A request for a day is one cache read.
2. **This API chooses the station and composes** (`GET /v1/climate/recent?lat=&lon=&days=3&max_distance_km=50`, `app/services/climate_recent.py`): the nearest station of the climate sheets that **still measures** (the same stations as `/v1/climate/normals`, so the two routes can be read together), then the next two nearest when it has nothing for the days asked. The answer gives the station (id, name, distance), the days (`rain_mm`, `tmin_c`, `tmax_c`, `tmean_c`, `wind_mean_ms`, `gust_ms`, `doubtful`), `last_day`, when the gateway asked the portal (`fetched_at_utc`) and `normals_month` (mean maximum and minimum of the month of the last day, from the sheet): a day is set against a mean, not against "the normal of the day". `status`: `available`, `no_recent_data`, `outside_coverage`, `not_configured`, `unavailable`.
3. Cached one hour here per station (the gateway's own entry changes once a day); the budget is `Quota("vigie-meteo-station-daily", 300 per 3,000)` of our own.
4. The credit is Météo-France's, with "Licence Ouverte 2.0" written because the licence document of the API was read (see `SOURCES.md`); the choice of the station and its distance are Vigie's (`derived`).

## Checked

- Against the real portal, from the gateway's collector: Lille-Lesquin (59343001) and a station of Martinique (97209004) answered the days of 15 to 17 November 2026 (Lille: 22.3 mm on the 17th, 5.3 °C to 15.5 °C); an unknown number is "no data", not an error.
- The tests replay a real file (Lille-Lesquin, 10 to 17 November) through the gateway's parser and the real answers through this API's collector.

## Not verified

- A station of the climate sheets that the daily archive does not list: the next one answers, but the proportion of stations without recent daily data was not measured (two of two tried had data).
- The quality of the most recent day: yesterday's values are read the day after, `9` (filtered) or `1`; they can change when the climatologist validates them. The gust of 17 November at Lille was `2` (doubtful) and is withheld.
- The quota under a crowd: each new station costs three to four portal requests once a day; a crowd asking for many different stations the same morning is spread (6 orders a minute per process) and the excess gets a 503 (`unavailable`).
