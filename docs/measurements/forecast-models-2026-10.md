# Forecast models: does the mean of several beat `best_match`?

*A measure made on 23 October 2026. The tool is [`tools/measure_forecast_models.py`](../../tools/measure_forecast_models.py); every figure
below is written by it (`score --markdown`) from [`forecast-models-2026-10-results.json.gz`](forecast-models-2026-10-results.json.gz).
The decision drawn from it is [ADR 0010](../decisions/0010-forecast-model-blend.md). Nothing in `app/` was changed by this work.*

## 1. Conclusion

Today `OpenMeteoCollector` asks Open-Meteo for its default blend, `best_match`. Over **61 days (21 August to 20 October 2026) and 20 official
stations**, the plain mean of eight models, compared with `best_match` at the same lead time (1 to 5 days) against observations:

- **Daily minimum temperature: better at every lead.** The mean absolute error (MAE) falls by 0.22, 0.30, 0.32, 0.19 and 0.28 degC at
  J+1 to J+5 (1.32 to 1.10 at J+1, 1.80 to 1.52 at J+5); the 95 % interval excludes 0 every time and the gain reaches the useful size
  (0.2 degC) at four leads of five (J+4 is 0.19).
- **Daily maximum temperature: better from J+4, not before.** 0.29 and 0.31 degC at J+4 and J+5, whichever station is left out. At J+1 there
  is no difference (-0.04, interval -0.08 to +0.01); at J+2 and J+3 the gain is certain but under 0.2 degC (-0.13, -0.19); in France at J+1 the
  mean is slightly worse (+0.13 degC).
- **Rain: a lower MAE that does not settle anything.** The mean has the lower MAE at J+1 to J+3 and J+5 (-0.63 to -0.24 mm), but it announces
  36 to 69 % more days of 1 mm than were observed (`best_match`: 4 to 36 % more). Read at the threshold where it announces as many rainy days
  as `best_match`, it still ranks the days better at J+1 to J+4 (critical success index +0.05 to +0.09), by a small margin that one season
  cannot confirm. The probability of precipitation that the API serves cannot be scored at a fixed lead (the archive has none).
- **Wind: no.** In France the mean is worse at J+1 to J+3 (+0.35, +0.18, +0.20 m/s); elsewhere and later the differences are 0.1 m/s or
  less. The observed wind (the day's strongest averaged wind) is about 2 m/s above what the models' hourly wind reaches, for every
  competitor, so this score is mostly a common offset.
- **The grid costs `best_match` something at J+1.** Asking at the point snapped to the 0.05 degree grid, as the API does, instead of the
  station itself, adds 0.14 degC to the error of the daily maximum at J+1 (0.20 in France) and 0.09 degC to the minimum; it is gone from J+3.

**Reading.** A multi-model mean is worth it for the minimum temperature at every lead and for the maximum from the fourth day, and for
nothing else shown here. It costs more quota: asking the two temperatures of eight models adds 1.6 weighted calls to the 1.0 of the daily
request, for each cell and each refresh. See the [ADR](../decisions/0010-forecast-model-blend.md) for the decision and what it leaves open.

## 2. Method

### 2.1 The same lead time

The [Previous Runs API](https://open-meteo.com/en/docs/previous-runs-api) of Open-Meteo (`previous-runs-api.open-meteo.com`) returns, for
every hour, what each model predicted 24 h x N earlier: `temperature_2m_previous_day3` is the value predicted 72 hours before. Every
competitor is therefore scored at **exactly** N days ahead for every hour of the day. That is not exactly what the API served on a given morning
(what it calls "J+1" depends on when its cache was last refreshed): it is the clean version of the same question.
Hourly values are in UTC and the wind is in m/s (`wind_speed_unit=ms`, the 10 m wind, not the gusts).

### 2.2 The competitors

| Name | What it is |
|---|---|
| `best_match` | What the API asks today (no `models` parameter), at the exact position of the station. |
| `best_match_grid` | The same, at the position the API really asks for: the station snapped to the 0.05 degree grid (`app/collectors/grid.py`). |
| `ecmwf_ifs025` | ECMWF IFS, 0.25 degree. |
| `ecmwf_aifs025_single` | ECMWF AIFS (machine-learning model), 0.25 degree. |
| `icon_seamless` | DWD ICON: global, Europe and D2 chained. |
| `gfs_seamless` | NOAA GFS, with HRRR over the United States. |
| `meteofrance_seamless` | Meteo-France: ARPEGE world and Europe, AROME France. **Nothing at J+4 and J+5** (1,220 of 1,220 station-days empty). |
| `ukmo_seamless` | UK Met Office: global 10 km and UKV 2 km. |
| `gem_seamless` | Environment Canada: GDPS, RDPS, HRDPS. |
| `jma_seamless` | JMA: GSM and MSM. |
| `mean`, `median` | Mean and median of the eight models of that day. A model that is missing a day is left out of that day (J+4 and J+5 are the mean of seven); the days it is missing are counted below. |
| `weighted_mean` | The mean with weights 1/MAE^2 **learned on the first half of the days** (21 August to 19 September) and **scored on the second half only** (20 September to 20 October). |

`best_match` is, according to [the Open-Meteo documentation](https://open-meteo.com/en/docs), a global backbone of ICON, GFS and IFS with
regional high-resolution models added by region (AROME France HD for France and Western Europe, ICON-D2 for Central Europe, UKV for the
United Kingdom, HRRR for North America, MSM for Japan, and global models only elsewhere). The seamless models above chain the regional and
the global models of a provider the same way (read in the Open-Meteo source, `ForecastapiController.swift`, for Meteo-France, JMA, the
UK Met Office, GEM, GFS and ICON).

*Tried and not kept* (`probe`): `kma_seamless` and `bom_access_global` returned nothing at 1 and 5 days at Paris, at either end of the period;
`cma_grapes_global` returned nothing at 5 days. `ecmwf_ifs` (the 9 km model) answers; it was left out to count ECMWF's IFS once and to stay
within the budget. The regional models are not asked alone: they are inside the seamless ones.

### 2.3 The 20 stations

Only official stations, each with its own code. Twelve in France, from the Meteo-France network, eight elsewhere, airports that report METAR.
Every position is that of a station.

| Code | Station | Group | Time zone | Note | Rain observed |
|---|---|---|---|---|---|
| 75114001 | Paris-Montsouris | France (Meteo-France) | Europe/Paris |  | yes |
| 69029001 | Lyon-Bron | France (Meteo-France) | Europe/Paris |  | yes |
| 13054001 | Marseille-Marignane | France (Meteo-France) | Europe/Paris |  | yes |
| 06088001 | Nice-Cote d'Azur | France (Meteo-France) | Europe/Paris |  | yes |
| 29075001 | Brest-Guipavas | France (Meteo-France) | Europe/Paris |  | yes |
| 67124001 | Strasbourg-Entzheim | France (Meteo-France) | Europe/Paris |  | yes |
| 31069001 | Toulouse-Blagnac | France (Meteo-France) | Europe/Paris |  | yes |
| 33281001 | Bordeaux-Merignac | France (Meteo-France) | Europe/Paris |  | yes |
| 59343001 | Lille-Lesquin | France (Meteo-France) | Europe/Paris |  | yes |
| 20148001 | Bastia | France (Meteo-France) | Europe/Paris |  | yes |
| 74056001 | Chamonix | France (Meteo-France) | Europe/Paris | mountain valley, 1042 m | yes |
| 17300009 | La Rochelle-Ile de Re | France (Meteo-France) | Europe/Paris | Atlantic coast | yes |
| EDDF | Frankfurt (Germany) | World (METAR) | Europe/Berlin |  | no |
| LEMD | Madrid-Barajas (Spain) | World (METAR) | Europe/Madrid |  | no |
| EGLL | London Heathrow (UK) | World (METAR) | Europe/London |  | no |
| JFK | New York JFK (US East Coast) | World (METAR) | America/New_York |  | yes |
| CYYZ | Toronto Pearson (Canada) | World (METAR) | America/Toronto |  | no |
| NZAA | Auckland (New Zealand) | World (METAR) | Pacific/Auckland |  | no |
| RJTT | Tokyo Haneda (Japan) | World (METAR) | Asia/Tokyo |  | no |
| TJSJ | San Juan (Puerto Rico, tropical) | World (METAR) | America/Puerto_Rico |  | yes |

### 2.4 The period

The data asked go from **2026-08-20 to 2026-10-21** (63 days); the **scored days are 2026-08-21 to 2026-10-20 (61 days)**, because the
window of a day reaches into the day before and the day after. The period is short because of the budget (below), not because of the archive:
the Previous Runs API keeps most models since January 2024.

### 2.5 Observations: where they come from

| | France | Elsewhere |
|---|---|---|
| Source | Meteo-France, [daily climatological data](https://www.data.gouv.fr/datasets/donnees-climatologiques-de-base-quotidiennes), files `Q_<department>_latest-2025-2026_RR-T-Vent.csv.gz` | METAR and SPECI reports archived by the [Iowa Environmental Mesonet](https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py) (IEM) |
| Licence | Licence Ouverte 2.0 (Etalab) | Public domain; the IEM asks for credit to the Iowa Environmental Mesonet of Iowa State University |
| Freshness | Updated daily; the files read on 23 October 2026 had been written at 08:44-08:45 GMT that day | Reports to the day before |
| Quantities | `TX`, `TN`, `RR` (rain), `FXY` (the day's maximum of the hourly maxima of the wind averaged over 10 minutes, at 10 m, in m/s) | Temperature and wind (`sknt`, knots) of every report; rain from the routine reports' `p01i` (inches) at two stations only |
| Day | Windows in UTC, see below | The **local** calendar day of the station |

**The windows of Meteo-France were found, not assumed.** The field description says only that `RR` runs from 06 UTC of D to 06 UTC of D+1 and
says nothing about `TN` and `TX`. The daily values were rebuilt from the hourly files of three stations: they match to 0.000 over
`TX` (06 UTC D to 06 UTC D+1), `TN` (18 UTC D-1 to 18 UTC D), `RR` (06 UTC D to 06 UTC D+1) and `FXY` (the UTC day). The same rebuilt over
the UTC calendar day would be wrong by up to 0.38 degC on `TN` and 0.90 mm on `RR`:

**The daily file of Meteo-France rebuilt from its hourly file (mean absolute difference, over the window used by the tool / over the UTC calendar day)**

| Station | Days | TX (degC) | TN (degC) | RR (mm) | FXY (m/s) |
|---|---|---|---|---|---|
| Paris-Montsouris | 61 | 0.000 / 0.000 | 0.000 / 0.380 | 0.000 / 0.252 | 0.000 / 0.000 |
| Lyon-Bron | 60 | 0.000 / 0.143 | 0.000 / 0.362 | 0.000 / 0.895 | 0.000 / 0.000 |
| Toulouse-Blagnac | 61 | 0.000 / 0.011 | 0.000 / 0.266 | 0.000 / 0.590 | 0.000 / 0.000 |

Forecasts are folded into the same windows from their hourly values: the maximum (`TX`, wind), the minimum (`TN`) or the sum (rain) of the
hours of the window, and a window with a missing hour is a missing forecast. Observations from METAR need 90 % of the hours of the day.

**Why METAR and not the daily data of the world.** Daily climate data for the whole world were not usable for this period: the seven NOAA GHCN-Daily
files opened (23 October 2026) stopped at very different dates (Heathrow, Tokyo and Singapore on 2025-08-24, Madrid on 2026-08-29, Auckland on 2026-10-15, Frankfurt in 1984, Toronto in 2013);
the NCEI hourly archive (ISD, `global-hourly`) returned no row for 2026 in the two requests made. METAR was the freshest open archive found. Its weaknesses:
the temperature is a whole degree outside the United States; the maximum of a series of reports is a little under the true maximum; **rain
is only in the METAR of the United States** (the `p01i` of the other airports is `0.00` throughout, which is not a measure), so rain is scored
at 14 stations (12 in France, JFK and San Juan).

### 2.6 What is scored, and when a gain is "real"

- **TX, TN, wind:** MAE and bias (forecast minus observation). **Rain:** MAE of the daily total; at the threshold of 1 mm, the detection rate
  (share of the rainy days that were forecast), the false alarm ratio (share of the forecast rainy days that stayed dry), the frequency (forecast
  rainy days per observed one) and the critical success index (CSI).
- **For each lead, the difference with `best_match` on the same pairs**, with a **95 % interval by paired bootstrap over days**: 4,000
  resamples (seed 20261021) of whole days, **every station of a day together**, because the stations of a day share its weather. Subsets: all
  stations, France, world.
- **A gain is "real" when the interval excludes 0 and the estimate reaches a useful size**, fixed before the first score: **0.2 degC** for
  TX and TN. The reasons: it is the order of the gap that the observations themselves leave (the hourly sampling hides 0.32 to 0.45 degC of the true maximum
  and 0.17 to 0.24 of the true minimum at the three stations checked, see the appendix; METAR is rounded to the degree outside the United States), and the
  order of what snapping the position to the grid costs on TX at J+1 (0.14 degC), a loss the API already accepts to share calls. Below 0.2 degC the tool
  writes "better (below threshold)". For rain and wind the sizes are conventions of this project, not sourced facts: 0.2 mm, 0.2 m/s, 0.02 for the rates, 0.01 for the Brier score.
- About 60 intervals are drawn: a few may exclude 0 by chance at the 95 % level. The conclusions rest on patterns (the same sign at every lead,
  in both groups, whichever station is left out), not on one cell.
- **No weighting was learned on the data it is scored on**: the weights of `weighted_mean` come from the first half and are scored on the second.

### 2.7 Budget and politeness

The free API allows [10,000 calls a day, 5,000 an hour and 600 a minute](https://open-meteo.com/en/terms), and the address also serves another
Vigie instance. The tool books every request in a ledger **before** it leaves and refuses one that would pass 1,500 weighted calls (all the runs together);
it keeps at most 300 weighted calls a minute and two requests a second, identifies itself in its User-Agent (no address of anyone), and caches
every answer. A request weighs its variables times its models over 14 days ([`app/collectors/weights.py`](../../app/collectors/weights.py), with the models
multiplying the variables as in Open-Meteo's own code). **Total: 43 requests, 1,356.2 weighted calls** (40 requests of data for 1,350.0, and 6.2 for the three probes). IEM was asked
one request at a time, at least 1.2 s apart (the service limits each address to one request a second); Meteo-France's files are ordinary downloads.

## 3. Results

Differences are *competitor minus `best_match`* in MAE: negative is better. The brackets are the 95 % interval.

### 3.1 Temperatures

**Daily maximum temperature (degC): best_match against the mean of the 8 models, all stations**

| Lead | Pairs | MAE best_match | MAE mean | Mean - best_match | Reading | Median - best_match | Bias best_match / mean |
|---|---|---|---|---|---|---|---|
| J+1 | 1220 | 1.20 [1.14, 1.25] | 1.16 [1.11, 1.21] | -0.04 [-0.08, +0.01] | no difference shown | -0.05 [-0.10, -0.00] | -0.79 / -0.94 |
| J+2 | 1220 | 1.42 [1.35, 1.49] | 1.29 [1.23, 1.35] | -0.13 [-0.19, -0.08] | better (below threshold) | -0.13 [-0.18, -0.07] | -0.90 / -1.03 |
| J+3 | 1220 | 1.56 [1.48, 1.65] | 1.37 [1.30, 1.44] | -0.19 [-0.26, -0.12] | better (below threshold) | -0.17 [-0.23, -0.10] | -0.82 / -1.03 |
| J+4 | 1220 | 1.78 [1.68, 1.89] | 1.49 [1.41, 1.58] | -0.29 [-0.36, -0.22] | better | -0.25 [-0.32, -0.18] | -1.02 / -1.06 |
| J+5 | 1220 | 1.94 [1.81, 2.07] | 1.62 [1.52, 1.73] | -0.31 [-0.40, -0.24] | better | -0.30 [-0.37, -0.22] | -1.01 / -1.04 |

**Daily minimum temperature (degC): best_match against the mean of the 8 models, all stations**

| Lead | Pairs | MAE best_match | MAE mean | Mean - best_match | Reading | Median - best_match | Bias best_match / mean |
|---|---|---|---|---|---|---|---|
| J+1 | 1220 | 1.32 [1.25, 1.38] | 1.10 [1.04, 1.15] | -0.22 [-0.27, -0.18] | better | -0.24 [-0.29, -0.20] | +0.45 / +0.54 |
| J+2 | 1220 | 1.54 [1.47, 1.60] | 1.24 [1.18, 1.30] | -0.30 [-0.36, -0.25] | better | -0.30 [-0.35, -0.25] | +0.81 / +0.60 |
| J+3 | 1220 | 1.63 [1.55, 1.70] | 1.31 [1.24, 1.38] | -0.32 [-0.37, -0.27] | better | -0.31 [-0.35, -0.26] | +0.76 / +0.65 |
| J+4 | 1220 | 1.59 [1.51, 1.67] | 1.39 [1.31, 1.48] | -0.19 [-0.25, -0.14] | better (below threshold) | -0.17 [-0.22, -0.12] | +0.44 / +0.55 |
| J+5 | 1220 | 1.80 [1.70, 1.91] | 1.52 [1.43, 1.62] | -0.28 [-0.34, -0.22] | better | -0.26 [-0.33, -0.20] | +0.35 / +0.53 |

The mean has a lower MAE than **each of its members** at every lead for both temperatures. The tables of the appendix show why it is not just
the best model: the members err in different directions (the bias of `jma_seamless` on TX is about -1.9 degC, `gfs_seamless` on TN +1.3 to +1.5 degC,
`gem_seamless` on TN -0.5 to -0.9 degC) and the mean cancels part of it.

France and the world apart:

**Daily maximum temperature (degC): mean minus best_match (MAE), France and the world apart**

| Lead | All stations | France | World |
|---|---|---|---|
| J+1 | -0.04 [-0.08, +0.01] no difference shown (n=1220) | +0.13 [+0.07, +0.20] worse (below threshold) (n=732) | -0.29 [-0.36, -0.23] better (n=488) |
| J+2 | -0.13 [-0.19, -0.08] better (below threshold) (n=1220) | +0.02 [-0.05, +0.08] no difference shown (n=732) | -0.36 [-0.45, -0.28] better (n=488) |
| J+3 | -0.19 [-0.26, -0.12] better (below threshold) (n=1220) | -0.09 [-0.18, +0.01] no difference shown (n=732) | -0.34 [-0.43, -0.26] better (n=488) |
| J+4 | -0.29 [-0.36, -0.22] better (n=1220) | -0.20 [-0.29, -0.12] better (n=732) | -0.42 [-0.52, -0.32] better (n=488) |
| J+5 | -0.31 [-0.40, -0.24] better (n=1220) | -0.24 [-0.35, -0.14] better (n=732) | -0.42 [-0.53, -0.32] better (n=488) |

**Daily minimum temperature (degC): mean minus best_match (MAE), France and the world apart**

| Lead | All stations | France | World |
|---|---|---|---|
| J+1 | -0.22 [-0.27, -0.18] better (n=1220) | -0.12 [-0.17, -0.07] better (below threshold) (n=732) | -0.38 [-0.47, -0.28] better (n=488) |
| J+2 | -0.30 [-0.36, -0.25] better (n=1220) | -0.31 [-0.37, -0.24] better (n=732) | -0.29 [-0.38, -0.21] better (n=488) |
| J+3 | -0.32 [-0.37, -0.27] better (n=1220) | -0.32 [-0.38, -0.25] better (n=732) | -0.32 [-0.39, -0.25] better (n=488) |
| J+4 | -0.19 [-0.25, -0.14] better (below threshold) (n=1220) | -0.11 [-0.18, -0.04] better (below threshold) (n=732) | -0.32 [-0.40, -0.24] better (n=488) |
| J+5 | -0.28 [-0.34, -0.22] better (n=1220) | -0.22 [-0.31, -0.14] better (n=732) | -0.38 [-0.47, -0.28] better (n=488) |

In France `best_match` at J+1 comes from a high-resolution model (AROME France HD, per the documentation) and the mean does not beat it on TX there;
from J+4 the mean wins in France too (-0.20 and -0.24 degC). Outside France the mean wins at every lead, by a large margin at one
station: San Juan, where `best_match` (global models only there, per the documentation) has a TX bias of -2.16 degC at J+1 against -0.28 for the mean
(MAE 2.16 against 0.61). The leave-one-out table below shows what each station changes.

### 3.2 Rain

**Daily rain (mm): mean minus best_match (MAE), France and the world apart**

| Lead | All stations | France | World |
|---|---|---|---|
| J+1 | -0.63 [-0.95, -0.35] better (n=854) | -0.37 [-0.61, -0.16] better (n=732) | -2.19 [-4.03, -0.73] better (n=122) |
| J+2 | -0.35 [-0.53, -0.17] better (n=854) | -0.33 [-0.54, -0.14] better (n=732) | -0.47 [-1.09, +0.10] no difference shown (n=122) |
| J+3 | -0.35 [-0.57, -0.17] better (n=854) | -0.29 [-0.52, -0.10] better (n=732) | -0.72 [-1.32, -0.13] better (n=122) |
| J+4 | -0.13 [-0.31, +0.05] no difference shown (n=854) | -0.13 [-0.32, +0.07] no difference shown (n=732) | -0.15 [-0.72, +0.33] no difference shown (n=122) |
| J+5 | -0.24 [-0.48, -0.02] better (n=854) | -0.24 [-0.47, -0.02] better (n=732) | -0.28 [-1.01, +0.40] no difference shown (n=122) |

**Rain days of 1 mm or more, the mean against best_match (stations with a rain observation)**

| Stations | Lead | Pairs / rainy days | Detection best_match / mean | Change | False alarm ratio best_match / mean | Change | Rainy days forecast per observed, best_match / mean | CSI best_match / mean |
|---|---|---|---|---|---|---|---|---|
| all | J+1 | 854 / 205 | 0.71 / 0.89 | +0.18 [+0.12, +0.24] | 0.32 / 0.35 | +0.02 [-0.02, +0.07] | 1.04 / 1.36 | 0.53 / 0.60 |
| all | J+2 | 854 / 205 | 0.70 / 0.88 | +0.19 [+0.14, +0.23] | 0.37 / 0.37 | -0.00 [-0.04, +0.03] | 1.11 / 1.40 | 0.49 / 0.58 |
| all | J+3 | 854 / 205 | 0.66 / 0.91 | +0.25 [+0.18, +0.32] | 0.40 / 0.37 | -0.03 [-0.07, +0.02] | 1.10 / 1.45 | 0.46 / 0.59 |
| all | J+4 | 854 / 205 | 0.75 / 0.89 | +0.15 [+0.09, +0.21] | 0.43 / 0.45 | +0.02 [-0.02, +0.05] | 1.31 / 1.61 | 0.48 / 0.52 |
| all | J+5 | 854 / 205 | 0.73 / 0.89 | +0.17 [+0.10, +0.23] | 0.46 / 0.47 | +0.01 [-0.03, +0.05] | 1.36 / 1.69 | 0.45 / 0.50 |
| fr | J+1 | 732 / 153 | 0.71 / 0.89 | +0.18 [+0.12, +0.24] | 0.32 / 0.35 | +0.03 [-0.02, +0.08] | 1.04 / 1.36 | 0.53 / 0.60 |
| fr | J+2 | 732 / 153 | 0.68 / 0.85 | +0.17 [+0.12, +0.23] | 0.37 / 0.38 | +0.01 [-0.03, +0.05] | 1.07 / 1.36 | 0.49 / 0.56 |
| fr | J+3 | 732 / 153 | 0.61 / 0.88 | +0.27 [+0.19, +0.36] | 0.43 / 0.38 | -0.04 [-0.10, +0.02] | 1.06 / 1.43 | 0.42 / 0.57 |
| fr | J+4 | 732 / 153 | 0.75 / 0.88 | +0.13 [+0.06, +0.20] | 0.44 / 0.47 | +0.03 [-0.02, +0.07] | 1.33 / 1.64 | 0.47 / 0.50 |
| fr | J+5 | 732 / 153 | 0.71 / 0.87 | +0.16 [+0.08, +0.24] | 0.50 / 0.49 | -0.00 [-0.05, +0.05] | 1.41 / 1.72 | 0.42 / 0.47 |
| world | J+1 | 122 / 52 | 0.71 / 0.88 | +0.17 [+0.06, +0.29] | 0.33 / 0.34 | +0.02 [-0.06, +0.10] | 1.06 / 1.35 | 0.53 / 0.61 |
| world | J+2 | 122 / 52 | 0.75 / 0.98 | +0.23 [+0.12, +0.35] | 0.39 / 0.35 | -0.04 [-0.09, +0.01] | 1.23 / 1.52 | 0.51 / 0.64 |
| world | J+3 | 122 / 52 | 0.81 / 0.98 | +0.17 [+0.07, +0.29] | 0.33 / 0.35 | +0.01 [-0.06, +0.09] | 1.21 / 1.50 | 0.58 / 0.65 |
| world | J+4 | 122 / 52 | 0.75 / 0.94 | +0.19 [+0.07, +0.33] | 0.41 / 0.39 | -0.02 [-0.09, +0.05] | 1.27 / 1.54 | 0.49 / 0.59 |
| world | J+5 | 122 / 52 | 0.77 / 0.96 | +0.19 [+0.09, +0.32] | 0.35 / 0.40 | +0.05 [-0.03, +0.12] | 1.19 / 1.62 | 0.54 / 0.58 |

The mean announces many more rainy days than were observed; a mean of forecasts spreads the rain, so it detects more of the rainy days for that
reason alone. Read at the threshold where it announces **as many rainy days as `best_match`** (set on the same days, which flatters the mean a little):

**The same, the mean read at the threshold where it announces as many rainy days as best_match (stations with a rain observation)**

| Stations | Lead | Threshold of the mean (mm) | Detection best_match / mean | Change | False alarm ratio best_match / mean | Change | CSI best_match / mean | Change of CSI |
|---|---|---|---|---|---|---|---|---|
| all | J+1 | 1.81 | 0.71 / 0.76 | +0.05 [-0.02, +0.12] | 0.32 / 0.27 | -0.05 [-0.10, -0.00] | 0.53 / 0.59 | +0.06 [+0.00, +0.12] better |
| all | J+2 | 1.84 | 0.70 / 0.75 | +0.05 [-0.01, +0.11] | 0.37 / 0.33 | -0.04 [-0.08, -0.01] | 0.49 / 0.55 | +0.05 [+0.01, +0.10] better |
| all | J+3 | 1.87 | 0.66 / 0.75 | +0.09 [+0.02, +0.15] | 0.40 / 0.32 | -0.08 [-0.13, -0.03] | 0.46 / 0.55 | +0.09 [+0.04, +0.15] better |
| all | J+4 | 1.63 | 0.75 / 0.81 | +0.06 [+0.02, +0.11] | 0.43 / 0.38 | -0.05 [-0.09, -0.01] | 0.48 / 0.54 | +0.06 [+0.03, +0.10] better |
| all | J+5 | 1.67 | 0.73 / 0.75 | +0.02 [-0.04, +0.08] | 0.46 / 0.45 | -0.01 [-0.06, +0.03] | 0.45 / 0.46 | +0.02 [-0.03, +0.06] no difference shown |
| fr | J+1 | 1.91 | 0.71 / 0.78 | +0.07 [+0.00, +0.14] | 0.32 / 0.25 | -0.07 [-0.12, -0.02] | 0.53 / 0.62 | +0.09 [+0.03, +0.14] better |
| fr | J+2 | 1.97 | 0.68 / 0.73 | +0.05 [-0.03, +0.12] | 0.37 / 0.32 | -0.04 [-0.09, +0.00] | 0.49 / 0.54 | +0.05 [-0.01, +0.10] no difference shown |
| fr | J+3 | 1.93 | 0.61 / 0.73 | +0.12 [+0.04, +0.19] | 0.43 / 0.31 | -0.11 [-0.18, -0.05] | 0.42 / 0.54 | +0.13 [+0.06, +0.19] better |
| fr | J+4 | 1.66 | 0.75 / 0.82 | +0.07 [+0.01, +0.13] | 0.44 / 0.38 | -0.05 [-0.11, -0.01] | 0.47 / 0.54 | +0.07 [+0.02, +0.12] better |
| fr | J+5 | 1.57 | 0.71 / 0.75 | +0.03 [-0.04, +0.11] | 0.50 / 0.47 | -0.02 [-0.08, +0.03] | 0.42 / 0.45 | +0.03 [-0.03, +0.08] no difference shown |
| world | J+1 | 1.59 | 0.71 / 0.71 | +0.00 [-0.14, +0.14] | 0.33 / 0.33 | +0.00 [-0.09, +0.09] | 0.53 / 0.53 | +0.00 [-0.11, +0.11] no difference shown |
| world | J+2 | 1.61 | 0.75 / 0.77 | +0.02 [-0.11, +0.15] | 0.39 / 0.38 | -0.02 [-0.08, +0.05] | 0.51 / 0.53 | +0.02 [-0.07, +0.11] no difference shown |
| world | J+3 | 1.64 | 0.81 / 0.83 | +0.02 [-0.08, +0.12] | 0.33 / 0.32 | -0.02 [-0.09, +0.06] | 0.58 / 0.60 | +0.02 [-0.06, +0.11] no difference shown |
| world | J+4 | 1.60 | 0.75 / 0.81 | +0.06 [-0.08, +0.19] | 0.41 / 0.37 | -0.04 [-0.12, +0.04] | 0.49 / 0.55 | +0.05 [-0.05, +0.16] no difference shown |
| world | J+5 | 1.76 | 0.77 / 0.77 | +0.00 [-0.14, +0.14] | 0.35 / 0.35 | +0.00 [-0.09, +0.09] | 0.54 / 0.54 | +0.00 [-0.11, +0.11] no difference shown |

The ranking of the days is better at J+1 to J+4 over all the stations, and in France at J+1, J+3 and J+4; it is not shown at J+5 nor for the two world stations. A mean of rain fields is smoother than any one of them, and an absolute error favours a smooth forecast of intermittent rain (a sharp forecast
that misplaces a shower by a day pays twice), so the MAE above favours the mean in advance. **The probability of
precipitation** (`precipitation_probability_max`, which the API serves) **cannot be scored at a fixed lead**: the Previous Runs API returned nothing for it, at five
leads, for `best_match` (`probe`). The Brier score below compares the share of the eight models that say "1 mm or more", read as a probability, with the
yes/no forecast of `best_match`. A probability is expected to beat a yes/no, so it says nothing about the mean; it is given for information.

**Brier score of a yes/no forecast of 1 mm (best_match) and of the share of the models that say yes**

| Lead | Pairs | Brier yes/no best_match | Brier share of models | Difference |
|---|---|---|---|---|
| J+1 | 854 | 0.151 | 0.091 | -0.060 [-0.080, -0.040] |
| J+2 | 854 | 0.172 | 0.100 | -0.073 [-0.092, -0.054] |
| J+3 | 854 | 0.187 | 0.101 | -0.086 [-0.113, -0.061] |
| J+4 | 854 | 0.197 | 0.115 | -0.082 [-0.101, -0.062] |
| J+5 | 854 | 0.217 | 0.129 | -0.088 [-0.114, -0.063] |

### 3.3 Wind

**Daily maximum 10 m wind (m/s): mean minus best_match (MAE), France and the world apart**

| Lead | All stations | France | World |
|---|---|---|---|
| J+1 | +0.22 [+0.18, +0.26] worse (n=1219) | +0.35 [+0.30, +0.41] worse (n=731) | +0.02 [-0.05, +0.08] no difference shown (n=488) |
| J+2 | +0.10 [+0.07, +0.13] worse (below threshold) (n=1219) | +0.18 [+0.13, +0.23] worse (below threshold) (n=731) | -0.02 [-0.07, +0.04] no difference shown (n=488) |
| J+3 | +0.12 [+0.08, +0.17] worse (below threshold) (n=1219) | +0.20 [+0.14, +0.25] worse (below threshold) (n=731) | +0.01 [-0.06, +0.07] no difference shown (n=488) |
| J+4 | +0.00 [-0.03, +0.04] no difference shown (n=1219) | -0.05 [-0.11, +0.01] no difference shown (n=731) | +0.09 [+0.03, +0.14] worse (below threshold) (n=488) |
| J+5 | +0.01 [-0.04, +0.05] no difference shown (n=1219) | -0.06 [-0.11, +0.00] no difference shown (n=731) | +0.10 [+0.03, +0.17] worse (below threshold) (n=488) |

The bias of `best_match` is -2.09 m/s at J+1 and that of the mean -2.38: the observation is the day's strongest wind averaged over 10 minutes (France) or the strongest
wind of the day's reports (elsewhere), and the models give an instantaneous hourly wind. The two models with the smallest offsets, `meteofrance_seamless` and
`gfs_seamless`, score best (MAE 1.74 and 1.96 m/s at J+1 against 2.18 for `best_match`); the mean wins at only 7 to 11 of the 20 stations. This score measures an
offset more than skill; it is no reason to put the mean on the wind.

### 3.4 The grid

**The grid: best_match asked at the point snapped to the 0.05 degree grid, minus best_match at the station (MAE)**

| Quantity | Stations | J+1 | J+2 | J+3 | J+4 | J+5 |
|---|---|---|---|---|---|---|
| tx | all | +0.14 [+0.12, +0.17] | +0.02 [+0.00, +0.04] | +0.01 [-0.00, +0.02] | -0.02 [-0.03, -0.01] | -0.02 [-0.03, -0.01] |
| tx | fr | +0.20 [+0.17, +0.24] | +0.01 [-0.01, +0.03] | +0.01 [-0.01, +0.03] | -0.03 [-0.04, -0.01] | -0.03 [-0.05, -0.01] |
| tx | world | +0.06 [+0.03, +0.08] | +0.03 [+0.00, +0.06] | +0.01 [-0.01, +0.02] | -0.00 [-0.02, +0.01] | -0.00 [-0.02, +0.02] |
| tn | all | +0.09 [+0.05, +0.12] | +0.02 [-0.01, +0.04] | +0.01 [-0.01, +0.03] | +0.05 [+0.03, +0.07] | +0.05 [+0.03, +0.07] |
| tn | fr | +0.13 [+0.09, +0.17] | -0.05 [-0.08, -0.03] | -0.04 [-0.07, -0.01] | +0.03 [+0.01, +0.05] | +0.03 [+0.01, +0.05] |
| tn | world | +0.02 [-0.03, +0.05] | +0.13 [+0.08, +0.17] | +0.09 [+0.05, +0.12] | +0.09 [+0.05, +0.12] | +0.09 [+0.05, +0.13] |
| rr | all | -0.04 [-0.14, +0.04] | -0.02 [-0.08, +0.05] | +0.02 [-0.01, +0.05] | -0.01 [-0.04, +0.03] | +0.00 [-0.02, +0.03] |
| rr | fr | +0.04 [-0.02, +0.10] | -0.01 [-0.08, +0.05] | +0.02 [+0.00, +0.05] | -0.02 [-0.05, +0.01] | -0.01 [-0.03, +0.02] |
| rr | world | -0.55 [-1.15, -0.07] | -0.04 [-0.18, +0.09] | +0.01 [-0.13, +0.16] | +0.06 [-0.05, +0.17] | +0.06 [-0.04, +0.16] |
| ws | all | -0.10 [-0.12, -0.07] | -0.15 [-0.18, -0.12] | -0.03 [-0.06, -0.01] | -0.04 [-0.05, -0.03] | -0.02 [-0.03, -0.01] |
| ws | fr | +0.06 [+0.03, +0.10] | +0.04 [+0.00, +0.07] | +0.01 [-0.03, +0.04] | -0.01 [-0.02, +0.00] | -0.01 [-0.02, +0.00] |
| ws | world | -0.34 [-0.38, -0.30] | -0.43 [-0.49, -0.36] | -0.10 [-0.12, -0.07] | -0.09 [-0.11, -0.07] | -0.05 [-0.07, -0.02] |

The API asks Open-Meteo for the station snapped to a 0.05 degree grid (`VIGIE_GRID_FORECAST_DEG`), so that two clients near each other share one call.
The snap moves a point by at most 0.025 degree in each coordinate. For the high-resolution models of J+1 that is enough to cost 0.14 degC on TX (0.20 in France) and
0.09 degC on TN; from J+3 it has no effect on temperature. (On the wind the snapped point is *better* at the airports of the world, by 0.34 and 0.43 m/s at J+1 and J+2; this was not looked into.)

### 3.5 Weights learned on the first half

**Weights learned on 2026-08-21 to 2026-09-19, scored on 2026-09-20 to 2026-10-20 only (all stations)**

| Quantity | Lead | MAE best_match | MAE mean (difference) | MAE weighted mean (difference) | Reading |
|---|---|---|---|---|---|
| tx | J+1 | 1.17 | 1.12 (-0.05) | 1.04 (-0.13 [-0.18, -0.08]) | better (below threshold) |
| tx | J+2 | 1.32 | 1.24 (-0.08) | 1.17 (-0.15 [-0.21, -0.08]) | better (below threshold) |
| tx | J+3 | 1.45 | 1.32 (-0.13) | 1.26 (-0.19 [-0.28, -0.10]) | better (below threshold) |
| tx | J+4 | 1.67 | 1.40 (-0.27) | 1.36 (-0.30 [-0.41, -0.20]) | better |
| tx | J+5 | 1.76 | 1.49 (-0.27) | 1.46 (-0.30 [-0.41, -0.19]) | better |
| tn | J+1 | 1.35 | 1.11 (-0.24) | 1.06 (-0.29 [-0.35, -0.23]) | better |
| tn | J+2 | 1.52 | 1.24 (-0.27) | 1.23 (-0.29 [-0.38, -0.19]) | better |
| tn | J+3 | 1.65 | 1.32 (-0.33) | 1.30 (-0.35 [-0.43, -0.26]) | better |
| tn | J+4 | 1.59 | 1.38 (-0.21) | 1.37 (-0.22 [-0.30, -0.14]) | better |
| tn | J+5 | 1.82 | 1.54 (-0.28) | 1.53 (-0.29 [-0.38, -0.20]) | better |
| rr | J+1 | 2.74 | 2.23 (-0.50) | 2.21 (-0.53 [-0.85, -0.26]) | better |
| rr | J+2 | 2.71 | 2.38 (-0.33) | 2.38 (-0.34 [-0.64, -0.08]) | better |
| rr | J+3 | 3.01 | 2.48 (-0.54) | 2.48 (-0.53 [-0.88, -0.25]) | better |
| rr | J+4 | 2.99 | 2.78 (-0.22) | 2.77 (-0.23 [-0.52, +0.08]) | no difference shown |
| rr | J+5 | 3.16 | 2.86 (-0.30) | 2.86 (-0.30 [-0.58, -0.04]) | better |
| ws | J+1 | 2.06 | 2.26 (+0.21) | 2.07 (+0.01 [-0.04, +0.06]) | no difference shown |
| ws | J+2 | 2.26 | 2.33 (+0.08) | 2.16 (-0.09 [-0.14, -0.05]) | better (below threshold) |
| ws | J+3 | 2.27 | 2.35 (+0.08) | 2.19 (-0.08 [-0.15, -0.02]) | better (below threshold) |
| ws | J+4 | 2.49 | 2.50 (+0.01) | 2.39 (-0.10 [-0.16, -0.04]) | better (below threshold) |
| ws | J+5 | 2.48 | 2.47 (-0.02) | 2.38 (-0.10 [-0.16, -0.03]) | better (below threshold) |

The weights bring a little more (TX at J+1: -0.13 against -0.05 for the plain mean; the wind stops being worse and is slightly better from J+2). Thirty days of learning in one
season is not enough to adopt them: they are an indication of what a second period should test, not a result. (The weights themselves are not stored in the results file.)

### 3.6 Is it one station?

**Is the gain of the mean the work of one station?**

| Quantity | Lead | Mean - best_match | Leaving one station out | Stations where the mean has the lower MAE |
|---|---|---|---|---|
| tx | J+1 | -0.04 | -0.07 (without 33281001) to +0.04 (without TJSJ) | 11 of 20 |
| tx | J+2 | -0.13 | -0.17 (without 69029001) to -0.06 (without TJSJ) | 13 of 20 |
| tx | J+3 | -0.19 | -0.21 (without 13054001) to -0.12 (without TJSJ) | 15 of 20 |
| tx | J+4 | -0.29 | -0.31 (without 67124001) to -0.22 (without TJSJ) | 16 of 20 |
| tx | J+5 | -0.31 | -0.33 (without 06088001) to -0.26 (without TJSJ) | 17 of 20 |
| tn | J+1 | -0.22 | -0.26 (without 20148001) to -0.18 (without JFK) | 13 of 20 |
| tn | J+2 | -0.30 | -0.34 (without 20148001) to -0.26 (without 13054001) | 14 of 20 |
| tn | J+3 | -0.32 | -0.37 (without 20148001) to -0.28 (without 13054001) | 16 of 20 |
| tn | J+4 | -0.19 | -0.23 (without 20148001) to -0.16 (without 74056001) | 14 of 20 |
| tn | J+5 | -0.28 | -0.31 (without 13054001) to -0.23 (without 74056001) | 16 of 20 |
| rr | J+1 | -0.63 | -0.72 (without 06088001) to -0.37 (without JFK) | 12 of 14 |
| rr | J+2 | -0.35 | -0.39 (without 06088001) to -0.27 (without 13054001) | 11 of 14 |
| rr | J+3 | -0.35 | -0.38 (without 31069001) to -0.29 (without JFK) | 14 of 14 |
| rr | J+4 | -0.13 | -0.18 (without 06088001) to -0.09 (without 74056001) | 10 of 14 |
| rr | J+5 | -0.24 | -0.30 (without 13054001) to -0.15 (without 74056001) | 11 of 14 |
| ws | J+1 | +0.22 | +0.14 (without TJSJ) to +0.28 (without RJTT) | 8 of 20 |
| ws | J+2 | +0.10 | +0.02 (without TJSJ) to +0.16 (without RJTT) | 10 of 20 |
| ws | J+3 | +0.12 | +0.05 (without TJSJ) to +0.17 (without RJTT) | 7 of 20 |
| ws | J+4 | +0.00 | -0.09 (without TJSJ) to +0.04 (without EDDF) | 11 of 20 |
| ws | J+5 | +0.01 | -0.08 (without TJSJ) to +0.04 (without RJTT) | 11 of 20 |

For TX at J+4 and J+5 and for TN at every lead the gain keeps its sign, and nearly its size, whichever station is left out, and the mean has the lower MAE at 13 to 17 of the 20
stations. For TX at J+1 the sign depends on San Juan (-0.04 with it, +0.04 without it).

**How many models are behind the mean**

| Lead | Models in the mean (average) | Station-days with fewer than all the models |
|---|---|---|
| J+1 | 8.00 of 8 | 0 of 1220 |
| J+2 | 8.00 of 8 | 0 of 1220 |
| J+3 | 8.00 of 8 | 0 of 1220 |
| J+4 | 7.00 of 8 | 1220 of 1220 |
| J+5 | 7.00 of 8 | 1220 of 1220 |

## 4. Limits

- **One season, 61 days.** No snow, no cold-air pools, no winter storms; the southern hemisphere is only in its late winter and spring (Auckland). The result for another season is not known.
- **Twenty stations:** twelve in France (one mountain valley, Chamonix, whose errors are large for everyone), eight airports elsewhere. Rain at 14. Nothing in Africa, South America or inland Asia.
- **The observations are proxies.** The hourly values hide part of the true extreme (appendix: 0.32 to 0.45 degC on TX, 0.17 to 0.24 on TN, three French stations); the bias is not the
  same *for each* competitor: in France the TX bias of the mean is more negative than that of `best_match` (-1.15 against -0.79 degC at J+1, pooled over the 12 stations), so a correction of the gap would help the mean (not computed).
  METAR gives whole degrees outside the United States and the maximum of its reports; the United States' 2-minute wind differs from the 10-minute wind elsewhere.
- **Rain** comes from gauges (France) and from the `p01i` of two airports, against grid cells; a METAR day needs 90 % of its hours reported, otherwise it is left out. A few heavy events weigh a lot in a MAE.
- **Wind** is dominated by an offset (3.3). **Probability of precipitation** is not measurable at a fixed lead.
- **Only J+1 to J+5** were measured. Today (J+0) and J+6 to J+9, which the API also serves, were not; the Previous Runs API keeps lead times up to seven days.
- **The model set is the one the budget allowed** (eight). ECMWF's 9 km model, the other regional models and the models with a short archive were not asked.
- **The archive is a record of past runs, and models change version over time.** Over 61 days that matters little but is not ruled out; a longer period would have to check it.
- **The weights of the requests are computed** with the project's rule and Open-Meteo's published code; Open-Meteo shows no counter, so what its servers booked is not seen.
- **The recent days of Meteo-France are "filtered", not yet validated** (quality code 9); nothing was dropped as doubtful (code 2).

## 5. Sources checked

Read on 23 October 2026 (the Open-Meteo source at commit `ae5caa86` of the same day):

- Open-Meteo, Previous Runs API: <https://open-meteo.com/en/docs/previous-runs-api> (fixed lead offsets of 1 to 7 days, `_previous_dayN` = value predicted 24 h x N before; most models since January 2024).
- Open-Meteo, Weather Forecast API: <https://open-meteo.com/en/docs> (the `best_match` regions; temperature and wind instantaneous, precipitation the sum of the preceding hour).
- Open-Meteo, terms and pricing: <https://open-meteo.com/en/terms> (10,000 calls a day, 5,000 an hour, 600 a minute, non-commercial use, CC BY 4.0) and <https://open-meteo.com/en/pricing> (what counts as several calls).
- Open-Meteo source: <https://github.com/open-meteo/open-meteo>, `Sources/App/Helper/Writer/ForecastApiResult.swift` (`calculateQueryWeight`) and `Sources/App/Controllers/ForecastapiController.swift` (variables times models; the models behind each name).
- Meteo-France, daily climatological data: <https://www.data.gouv.fr/datasets/donnees-climatologiques-de-base-quotidiennes> and the field list <https://meteofrance.s3.sbg.io.cloud.ovh.net/data/synchro_ftp/BASE/QUOT/Q_descriptif_champs_RR-T-Vent.csv>; hourly data: <https://www.data.gouv.fr/datasets/donnees-climatologiques-de-base-horaires>. Licence Ouverte 2.0.
- Iowa Environmental Mesonet, ASOS/METAR: <https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py> (the parameters and the limits are read in the source of the service, <https://github.com/akrherz/iem>, `pylib/iemweb/request/asos.py`: report types 1, 3 and 4, and a 1-second throttle per address since 2026-04-21) and <https://mesonet.agron.iastate.edu/disclaimer.php> (public domain, credit appreciated).
- Rejected for this period: NOAA GHCN-Daily (<https://www.ncei.noaa.gov/pub/data/ghcn/daily/>), NCEI `global-hourly` (<https://www.ncei.noaa.gov/access/services/data/v1>), see 2.5.

## 6. Reproduce

```
python tools/measure_forecast_models.py plan      # what would be asked and what it weighs
python tools/measure_forecast_models.py probe     # which models answer, and the probability of precipitation (about 6 weighted calls)
python tools/measure_forecast_models.py fetch     # the data (about 1,350 weighted calls the first time, none afterwards)
python tools/measure_forecast_models.py score --markdown tables.md
```

The cache (the answers and the ledger) is in the temporary folder, `vigie-forecast-models-cache`; `--cache-dir` moves it. `--end-date` and `--days` move the period
(another season, another year): the Previous Runs API keeps most models since January 2024. On Windows the time zones need `pip install tzdata`.

## Appendix: all the tables

**Daily maximum temperature (degC): each competitor, MAE (bias), all stations**

| Competitor | J+1 | J+2 | J+3 | J+4 | J+5 |
|---|---|---|---|---|---|
| best_match | 1.20 (-0.79) | 1.42 (-0.90) | 1.56 (-0.82) | 1.78 (-1.02) | 1.94 (-1.01) |
| best_match_grid | 1.34 (-0.98) +0.14 | 1.44 (-0.91) +0.02 | 1.57 (-0.82) +0.01 | 1.77 (-1.01) -0.02 | 1.92 (-0.99) -0.02 |
| ecmwf_ifs025 | 1.40 (-1.02) +0.20 | 1.52 (-1.07) +0.10 | 1.58 (-1.02) +0.02 | 1.63 (-0.91) -0.16 | 1.79 (-0.87) -0.15 |
| ecmwf_aifs025_single | 1.63 (-1.43) +0.43 | 1.76 (-1.54) +0.34 | 1.81 (-1.56) +0.25 | 1.90 (-1.58) +0.12 | 2.04 (-1.61) +0.11 |
| icon_seamless | 1.24 (-0.76) +0.04 | 1.36 (-0.79) -0.06 | 1.52 (-0.80) -0.04 | 1.67 (-0.83) -0.11 | 1.85 (-0.79) -0.09 |
| gfs_seamless | 1.21 (-0.54) +0.01 | 1.34 (-0.48) -0.08 | 1.43 (-0.40) -0.13 | 1.63 (-0.42) -0.15 | 1.86 (-0.42) -0.08 |
| meteofrance_seamless | 1.20 (-0.63) -0.00 | 1.37 (-0.78) -0.05 | 1.51 (-0.66) -0.05 | n/a | n/a |
| ukmo_seamless | 1.18 (-0.57) -0.02 | 1.37 (-0.75) -0.05 | 1.52 (-0.78) -0.04 | 1.65 (-0.73) -0.13 | 1.80 (-0.71) -0.14 |
| gem_seamless | 1.40 (-0.67) +0.20 | 1.52 (-0.89) +0.10 | 1.65 (-1.12) +0.09 | 1.72 (-1.08) -0.06 | 1.89 (-1.06) -0.05 |
| jma_seamless | 2.12 (-1.91) +0.92 | 2.18 (-1.92) +0.76 | 2.24 (-1.91) +0.68 | 2.28 (-1.85) +0.49 | 2.47 (-1.84) +0.53 |
| mean | 1.16 (-0.94) -0.04 | 1.29 (-1.03) -0.13 | 1.37 (-1.03) -0.19 | 1.49 (-1.06) -0.29 | 1.62 (-1.04) -0.31 |
| median | 1.15 (-0.90) -0.05 | 1.30 (-1.02) -0.13 | 1.39 (-1.01) -0.17 | 1.53 (-1.06) -0.25 | 1.64 (-1.04) -0.30 |

**Daily minimum temperature (degC): each competitor, MAE (bias), all stations**

| Competitor | J+1 | J+2 | J+3 | J+4 | J+5 |
|---|---|---|---|---|---|
| best_match | 1.32 (+0.45) | 1.54 (+0.81) | 1.63 (+0.76) | 1.59 (+0.44) | 1.80 (+0.35) |
| best_match_grid | 1.40 (+0.61) +0.09 | 1.56 (+0.72) +0.02 | 1.64 (+0.62) +0.01 | 1.64 (+0.35) +0.05 | 1.85 (+0.26) +0.05 |
| ecmwf_ifs025 | 1.34 (+0.01) +0.02 | 1.39 (-0.01) -0.15 | 1.47 (-0.02) -0.15 | 1.61 (+0.01) +0.02 | 1.82 (-0.06) +0.02 |
| ecmwf_aifs025_single | 1.46 (+0.05) +0.14 | 1.50 (+0.05) -0.04 | 1.55 (+0.06) -0.07 | 1.65 (+0.15) +0.06 | 1.75 (+0.20) -0.05 |
| icon_seamless | 1.31 (+0.56) -0.01 | 1.47 (+0.52) -0.07 | 1.56 (+0.46) -0.07 | 1.70 (+0.33) +0.11 | 1.92 (+0.26) +0.12 |
| gfs_seamless | 2.13 (+1.34) +0.82 | 2.12 (+1.51) +0.58 | 2.16 (+1.50) +0.53 | 2.24 (+1.47) +0.65 | 2.31 (+1.29) +0.51 |
| meteofrance_seamless | 1.19 (+0.45) -0.13 | 1.85 (+1.32) +0.31 | 1.98 (+1.39) +0.35 | n/a | n/a |
| ukmo_seamless | 1.77 (+1.39) +0.45 | 1.67 (+1.08) +0.13 | 1.77 (+1.09) +0.14 | 1.88 (+1.13) +0.30 | 2.01 (+1.14) +0.21 |
| gem_seamless | 1.80 (-0.79) +0.48 | 1.98 (-0.94) +0.44 | 1.82 (-0.57) +0.19 | 1.98 (-0.53) +0.39 | 2.14 (-0.56) +0.34 |
| jma_seamless | 1.77 (+1.30) +0.45 | 1.77 (+1.27) +0.23 | 1.84 (+1.28) +0.22 | 2.03 (+1.32) +0.44 | 2.25 (+1.40) +0.44 |
| mean | 1.10 (+0.54) -0.22 | 1.24 (+0.60) -0.30 | 1.31 (+0.65) -0.32 | 1.39 (+0.55) -0.19 | 1.52 (+0.53) -0.28 |
| median | 1.08 (+0.55) -0.24 | 1.24 (+0.62) -0.30 | 1.32 (+0.67) -0.31 | 1.42 (+0.57) -0.17 | 1.54 (+0.56) -0.26 |

**Daily rain (mm): each competitor, MAE (bias), all stations**

| Competitor | J+1 | J+2 | J+3 | J+4 | J+5 |
|---|---|---|---|---|---|
| best_match | 2.35 (-0.11) | 2.22 (-0.31) | 2.33 (-0.52) | 2.32 (-0.33) | 2.60 (-0.26) |
| best_match_grid | 2.31 (-0.12) -0.04 | 2.20 (-0.34) -0.02 | 2.35 (-0.51) +0.02 | 2.31 (-0.32) -0.01 | 2.61 (-0.26) +0.00 |
| ecmwf_ifs025 | 2.08 (+0.08) -0.28 | 2.17 (-0.10) -0.05 | 2.22 (-0.30) -0.11 | 2.37 (-0.00) +0.05 | 2.50 (-0.35) -0.10 |
| ecmwf_aifs025_single | 1.87 (+0.18) -0.49 | 1.94 (+0.19) -0.28 | 1.94 (+0.07) -0.39 | 2.20 (+0.21) -0.12 | 2.46 (+0.15) -0.14 |
| icon_seamless | 1.98 (-0.71) -0.37 | 1.96 (-0.68) -0.26 | 2.20 (-0.73) -0.13 | 2.28 (-0.80) -0.05 | 2.56 (-0.50) -0.05 |
| gfs_seamless | 2.34 (-0.28) -0.01 | 2.07 (-0.47) -0.15 | 2.31 (-0.57) -0.01 | 2.41 (-0.49) +0.09 | 2.54 (-0.94) -0.07 |
| meteofrance_seamless | 1.98 (+0.02) -0.37 | 2.21 (-0.27) -0.01 | 2.25 (-0.61) -0.08 | n/a | n/a |
| ukmo_seamless | 1.94 (-0.44) -0.41 | 2.30 (+0.06) +0.08 | 2.29 (-0.13) -0.04 | 2.80 (-0.04) +0.48 | 2.94 (-0.26) +0.34 |
| gem_seamless | 2.25 (+0.07) -0.10 | 2.18 (-0.01) -0.03 | 2.30 (-0.34) -0.03 | 2.62 (-0.33) +0.30 | 2.57 (-0.23) -0.03 |
| jma_seamless | 1.99 (-0.30) -0.36 | 2.23 (-0.25) +0.01 | 2.29 (-0.29) -0.03 | 2.65 (-0.26) +0.33 | 2.85 (-0.32) +0.25 |
| mean | 1.72 (-0.17) -0.63 | 1.87 (-0.19) -0.35 | 1.97 (-0.36) -0.35 | 2.19 (-0.25) -0.13 | 2.36 (-0.35) -0.24 |
| median | 1.65 (-0.51) -0.70 | 1.76 (-0.53) -0.46 | 1.85 (-0.71) -0.48 | 2.01 (-0.74) -0.31 | 2.10 (-1.03) -0.50 |

**Daily maximum 10 m wind (m/s): each competitor, MAE (bias), all stations**

| Competitor | J+1 | J+2 | J+3 | J+4 | J+5 |
|---|---|---|---|---|---|
| best_match | 2.18 (-2.09) | 2.38 (-2.28) | 2.38 (-2.28) | 2.62 (-2.55) | 2.57 (-2.43) |
| best_match_grid | 2.08 (-1.93) -0.10 | 2.23 (-2.11) -0.15 | 2.34 (-2.26) -0.03 | 2.58 (-2.50) -0.04 | 2.55 (-2.39) -0.02 |
| ecmwf_ifs025 | 2.97 (-2.95) +0.78 | 3.02 (-3.00) +0.64 | 3.03 (-3.00) +0.65 | 3.03 (-3.00) +0.42 | 3.01 (-2.94) +0.44 |
| ecmwf_aifs025_single | 2.99 (-2.98) +0.80 | 3.06 (-3.06) +0.68 | 3.13 (-3.12) +0.75 | 3.16 (-3.15) +0.54 | 3.14 (-3.11) +0.57 |
| icon_seamless | 2.54 (-2.49) +0.35 | 2.68 (-2.64) +0.30 | 2.67 (-2.64) +0.29 | 2.72 (-2.65) +0.10 | 2.63 (-2.46) +0.06 |
| gfs_seamless | 1.96 (-1.73) -0.23 | 1.99 (-1.74) -0.39 | 2.05 (-1.75) -0.33 | 2.09 (-1.74) -0.53 | 2.15 (-1.80) -0.42 |
| meteofrance_seamless | 1.74 (-1.46) -0.44 | 1.88 (-1.57) -0.50 | 1.92 (-1.59) -0.46 | n/a | n/a |
| ukmo_seamless | 2.39 (-2.30) +0.20 | 2.49 (-2.42) +0.11 | 2.58 (-2.51) +0.21 | 2.55 (-2.46) -0.07 | 2.53 (-2.41) -0.04 |
| gem_seamless | 2.27 (-2.22) +0.09 | 2.32 (-2.24) -0.06 | 2.36 (-2.26) -0.01 | 2.48 (-2.36) -0.14 | 2.47 (-2.29) -0.10 |
| jma_seamless | 3.02 (-2.90) +0.83 | 3.03 (-2.91) +0.65 | 3.02 (-2.87) +0.64 | 2.97 (-2.82) +0.35 | 2.96 (-2.74) +0.39 |
| mean | 2.40 (-2.38) +0.22 | 2.48 (-2.45) +0.10 | 2.50 (-2.47) +0.12 | 2.62 (-2.59) +0.00 | 2.58 (-2.53) +0.01 |
| median | 2.49 (-2.47) +0.30 | 2.57 (-2.54) +0.19 | 2.59 (-2.57) +0.22 | 2.71 (-2.68) +0.09 | 2.65 (-2.61) +0.08 |

**Stations: MAE of best_match and of the mean (TX and TN)**

| Station | TX J+1 | TX J+3 | TX J+5 | TN J+1 | TN J+3 | TN J+5 |
|---|---|---|---|---|---|---|
| 06088001 Nice-Cote d'Azur | 1.11 / 1.02 | 1.07 / 0.98 | 0.98 / 1.04 | 0.71 / 0.51 | 0.85 / 0.73 | 1.03 / 1.03 |
| 13054001 Marseille-Marignane | 1.04 / 1.53 | 1.36 / 1.64 | 2.37 / 1.88 | 1.79 / 1.71 | 3.05 / 2.00 | 1.61 / 1.95 |
| 17300009 La Rochelle-Ile de Re | 0.79 / 0.90 | 1.26 / 1.20 | 1.78 / 1.42 | 0.83 / 0.69 | 1.86 / 0.90 | 1.31 / 1.21 |
| 20148001 Bastia | 1.30 / 1.29 | 1.40 / 1.37 | 1.49 / 1.55 | 1.34 / 1.80 | 1.39 / 2.00 | 1.69 / 1.92 |
| 29075001 Brest-Guipavas | 1.07 / 1.28 | 1.40 / 1.32 | 1.74 / 1.57 | 1.11 / 1.24 | 1.69 / 1.47 | 1.78 / 1.67 |
| 31069001 Toulouse-Blagnac | 1.22 / 0.89 | 1.30 / 1.06 | 2.05 / 1.57 | 0.92 / 0.82 | 1.27 / 1.14 | 1.92 / 1.48 |
| 33281001 Bordeaux-Merignac | 0.88 / 1.49 | 1.38 / 1.53 | 2.30 / 2.02 | 1.02 / 0.90 | 1.93 / 1.15 | 1.76 / 1.41 |
| 59343001 Lille-Lesquin | 1.16 / 1.21 | 1.69 / 1.59 | 1.88 / 1.64 | 1.55 / 0.76 | 1.02 / 0.97 | 1.51 / 1.52 |
| 67124001 Strasbourg-Entzheim | 1.40 / 1.79 | 2.10 / 2.15 | 2.51 / 2.46 | 0.86 / 1.03 | 1.23 / 1.65 | 1.91 / 1.90 |
| 69029001 Lyon-Bron | 0.92 / 1.20 | 1.37 / 1.52 | 2.33 / 1.97 | 1.06 / 1.16 | 1.23 / 1.18 | 1.82 / 1.36 |
| 74056001 Chamonix | 2.73 / 2.58 | 3.25 / 2.52 | 3.05 / 2.58 | 4.44 / 3.92 | 4.88 / 3.95 | 5.26 / 3.95 |
| 75114001 Paris-Montsouris | 0.75 / 0.75 | 1.53 / 1.21 | 1.66 / 1.52 | 0.97 / 0.62 | 1.42 / 0.86 | 1.59 / 1.15 |
| CYYZ Toronto Pearson (Canada) | 1.16 / 0.87 | 1.37 / 1.26 | 2.24 / 1.73 | 1.71 / 0.76 | 1.09 / 0.86 | 1.70 / 1.20 |
| EDDF Frankfurt (Germany) | 1.14 / 1.46 | 1.75 / 1.89 | 2.10 / 2.13 | 1.16 / 1.33 | 1.41 / 1.62 | 2.23 / 2.27 |
| EGLL London Heathrow (UK) | 0.99 / 0.99 | 1.57 / 1.23 | 1.59 / 1.41 | 0.74 / 0.76 | 1.47 / 0.85 | 1.52 / 1.07 |
| JFK New York JFK (US East Coast) | 1.01 / 0.71 | 1.73 / 1.09 | 2.18 / 1.37 | 1.85 / 0.73 | 1.68 / 1.10 | 1.94 / 1.21 |
| LEMD Madrid-Barajas (Spain) | 1.10 / 0.99 | 1.26 / 1.17 | 1.67 / 1.53 | 1.77 / 0.83 | 2.08 / 1.17 | 1.92 / 1.16 |
| NZAA Auckland (New Zealand) | 0.89 / 0.89 | 1.01 / 0.99 | 1.09 / 1.01 | 0.86 / 0.78 | 0.96 / 0.91 | 1.12 / 1.07 |
| RJTT Tokyo Haneda (Japan) | 1.18 / 0.75 | 1.32 / 1.03 | 1.64 / 1.38 | 1.02 / 0.89 | 1.48 / 1.05 | 1.63 / 1.23 |
| TJSJ San Juan (Puerto Rico, tropical) | 2.16 / 0.61 | 2.09 / 0.70 | 2.13 / 0.69 | 0.67 / 0.70 | 0.56 / 0.59 | 0.80 / 0.64 |

**What the hourly sampling hides of the daily extremes (Meteo-France hourly files)**

| Station | Days | True TX minus hourly maximum (degC) | Hourly minimum minus true TN (degC) |
|---|---|---|---|
| Paris-Montsouris | 61 | +0.38 | +0.17 |
| Lyon-Bron | 59 | +0.32 | +0.24 |
| Toulouse-Blagnac | 61 | +0.45 | +0.19 |
