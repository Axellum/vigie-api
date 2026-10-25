# 0011. An unofficial pre-alert for Turkey: a shadow pilot, and the criteria it must meet before anyone sees it

- Status: accepted for the pilot (computed and stored, shown to no one); showing it is decided by the criteria below, then by the owner
- Date: 2026-10-25

## Context

A position in Turkey gets no official warning from Vigie. The Turkish State Meteorological Service (MGM) publishes no feed for third
parties: it is absent from the WMO's Severe Weather Information Centre and from MeteoAlarm, its record in the WMO Register of Alerting
Authorities has no CAP feed, and the JSON service behind its MeteoUYARI map answers `Not allowed by MGM` to any other site. Its terms forbid
copying, republishing, translating or distributing its content "önceden izin alınmadan ve kaynak gösterilmeden" (without prior permission
and without citing the source; https://www.mgm.gov.tr/site/yasal-uyari.aspx, read 2026-10-25). So its warnings cannot be relayed.

The owner asked whether Vigie can compute a pre-alert of its own from open data. The research of 2026-10-25 (kept outside this repository)
found:

- **Forecasts that can be redistributed and cut to Turkey**: NOAA GFS 0.25 degrees (NOMADS grib filter: area and fields chosen by the server;
  four fields of one lead over 35-43 N, 25-46 E weigh 20 KB; "can be used as desired",
  https://registry.opendata.aws/noaa-gfs-bdp-pds/), with an archive of past runs and their `.idx` on AWS (a run of 2024-01-15 read on
  2026-10-25), and ECMWF IFS 0.25 degrees (CC BY 4.0; gusts `10fg`, rain `tp`, snowfall `sf`, `mucape`, `mx2t3`/`mn2t3` in the index of the
  run of 2026-10-25, but whole-world fields only, about 100 MB a run).
- **Observations**: the METAR of 53 Turkish airports were returned by one area request to the NOAA Aviation Weather Center on 2026-10-25
  (gusts, present weather, temperature; no rain amount).
- **No thresholds**: the MGM sets its own per province and district and does not publish them.
- **No archive** of MeteoUYARI that a program can read: the Wayback Machine holds ten captures of its service, all of 2026-04-06, all an
  empty list. The press reports the orange and red warnings with the provinces they cover.
- **Law** (not legal advice): the WMO register records "No express provision for exclusive rights to the Met Service to issue forecasts
  and/or warnings" (https://alertingauthority.wmo.int/authorities.php?recId=169); the MGM law (3254) puts private **observation stations**
  under the MGM's permission (article 27), which Vigie does not run; no text reserving forecasts or warnings to the MGM was found.

A pre-alert that cries wolf, or misses a red warning, would cost the whole API its trust. So the pilot is built in the dark, and the test it
must pass is written here **before any result is seen**, as for the forecast-model measure ([ADR 0010](0010-forecast-model-blend.md)).

## Decision

### What is computed

1. **One country, Turkey, behind a setting that is off by default** (`VIGIE_PREALERT_TR`, a later change). Off, nothing is downloaded.
2. **A level per province (81, `il`) and per local day (Europe/Istanbul, UTC+3)**, today and the next two days, for five phenomena: rain
   (thunderstorms included), wind, snow, heat, cold. Levels: 0 none, 1 "watch", 2 "strong", 3 "severe". The words are ours and are never
   the MGM's colours or the name MeteoUYARI. A province's level is computed on the model grid points that fall in it.
3. **Source: GFS 0.25 degrees** (four runs a day, cut by the NOMADS filter). ECMWF is a second model for later, if the pilot is kept.
   No Open-Meteo call: the pre-alert does not touch the budget of `/v1/point`.
4. **Thresholds**: one national table per phenomenon (a quantity and three values), **chosen on the learning period only** (below). A table
   per province from a climatology is an open point, not part of the pilot.
5. **Shadow**: the levels of every run are stored with the run time, for scoring. **No route, no field, no frame, no Home Assistant entity
   shows them** during the pilot.

### The data the test is scored on

- **Official episodes from the press**: a list, kept in `docs/measurements/`, of the orange and red warnings of the MGM between 2024-01-01
  and the start of the shadow phase, one line per episode (dates of validity, colour, phenomenon, the provinces named for that colour, the
  address of the article). Only the facts are kept (date, colour, phenomenon, provinces); no text of the MGM or of the press is copied.
  - **Learning set**: the episodes of 2024 and 2025. The thresholds may be tuned on them, as often as needed.
  - **Test set**: the episodes of 2026. Scored **once**, with the frozen thresholds.
- **Past forecasts**: the GFS runs of those days from the AWS archive (the same model, read by the same code as the live pilot).
- **The shadow phase**: at least **6 weeks** of live runs with frozen thresholds, extended to 12 weeks at most until each judged
  phenomenon has the events it needs (below). Each week, the orange and red warnings reported by the press are added to the list.
- **METAR** of the Turkish airports for wind and temperature, read once a day during the shadow phase. Rain is not scored on observations
  (no rain amount in a Turkish METAR; a satellite estimate needs an account the project does not have).

A pair is (episode, province named orange or red). A hit at lead J0 is a level 2 or more, for the same phenomenon, in that province, on one
day of the episode at least, from the run of 00 UTC of the first day of the episode; at J1, from the run of 00 UTC of the day before.

### The criteria (fixed on 2026-10-25, before any score)

| # | Test | Data | Passes when |
|---|---|---|---|
| 1 | Detection of official warnings, J0 | test set + shadow-phase episodes | at least **70 %** of the pairs are hits |
| 2 | Detection of official warnings, J1 | same | at least **60 %** of the pairs are hits |
| 3 | Red warnings | same, red pairs only | at least **80 %** at level 2 or more at J0 (judged when there are 5 red pairs or more) |
| 4 | Days of false alarm | shadow phase | of the days when Vigie has level 2 or more in at least one province, at least **50 %** are days with an orange or red warning of the same phenomenon somewhere in Turkey |
| 5 | Over-alerting | shadow phase | at most **3 %** of the province-days at level 2 or more, and at most **0.3 %** at level 3 (all phenomena) |
| 6 | Wind against observations | shadow phase, METAR | for province-days with a gust of 65 km/h (35 kt) or more at an airport of the province: at least **60 %** at wind level 1 or more; of the wind level 2 or more, at least **40 %** with such a gust (judged with 10 observed days or more) |

- Tests 1 and 2 are judged for all phenomena together and reported per phenomenon. A phenomenon with fewer than **10 pairs** in the
  scored data is reported, not judged on its own.
- **A phenomenon is shown only if tests 1, 2, 4 and 5 pass, and 3 and 6 pass where they are judged.** A phenomenon that fails, or that has
  too few pairs to be judged, stays hidden; the others may be shown. Then the owner decides.
- **A threshold changed after the shadow phase starts restarts the phase** (six new weeks). The test set of 2026 is not used to tune.
- The figures are a first bar, not a promise of quality: the hit of test 1 ignores false alarms, which tests 4 and 5 bound only coarsely
  (the press reports warnings, not their absence).

### What showing it would mean (a later change, after the criteria)

A separate block, never mixed with official warnings (ADR 0002): its own route and field, labelled "unofficial" everywhere (API, Home
Assistant, screen), credited "NOAA GFS", with a link to https://www.mgm.gov.tr/meteouyari/turkiye.aspx and a sentence that the official
warnings of Turkey are the MGM's.

## Consequences

- **The pilot adds a dependency**: decoding GRIB2 needs ecCodes (`eccodes` 2.50 on PyPI ships the library; it decoded the NOMADS file of
  2026-10-25 on Windows). It is an optional extra, imported only when the setting is on.
- **Cost of running it**: about 1 MB a run from NOMADS, four runs a day; nothing when the setting is off. The backtest reads whole-world
  fields from the AWS archive by `Range` (about 0.5 to 1 MB a field): a few hundred MB once.
- **Work**: the collector, the province levels and the shadow store (one change); the episode list and the backtest tool (another);
  the scoring after six weeks or more.

## Open points

1. **Thresholds per province** from a climatology (ERA5 or the archive of the forecasts), the way the MGM does it, if the national table
   fails on the coast and in the mountains alike.
2. **ECMWF as a second model**, if GFS alone fails the criteria.
3. **Rain against observations**, if a satellite estimate (NASA IMERG) becomes reachable without an account, or the owner opens one.
4. **The MGM itself**: an e-mail asking for permission to relay MeteoUYARI and for its archive would replace this whole pilot by the
   official warnings. Sending it is the owner's call.
