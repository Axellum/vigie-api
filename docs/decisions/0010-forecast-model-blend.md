# 0010. Forecast models: the mean of eight for the minimum and the far maximum, `best_match` for the rest

- Status: proposed (the measure is done and its figures are in the report; whether to ship it is the owner's call on the quota: see "Open points")
- Date: 2026-10-23

## Context

The forecast of Vigie (`OpenMeteoCollector`) asks Open-Meteo for its default blend, `best_match`, with no `models` parameter. Open-Meteo
serves many models, and averaging several is the usual way to lower the error of a forecast. Whether it does here, at the lead times a
client reads (tomorrow to five days), and for which quantity, was a belief, not a measure. A change of the forecast must come with a gain
read against observations, and it has a cost: asking N models multiplies the variables of the request by N
([`docs/data-sources.md`](../data-sources.md), "Weight of the Open-Meteo calls").

The measure is [`docs/measurements/forecast-models-2026-10.md`](../measurements/forecast-models-2026-10.md), made by
[`tools/measure_forecast_models.py`](../../tools/measure_forecast_models.py): the Previous Runs API at a fixed lead of 1 to 5 days,
`best_match` and eight models and their mean and median, **61 days** (21 August to 20 October 2026), **20 official stations** (12 in France
read against Meteo-France's daily files, 8 elsewhere against METAR), a 95 % interval by bootstrap over whole days, and **1,356.2 weighted
calls** of the 1,500 allowed. The figures below are from that report; read it for the method and the limits.

What it found, mean of the eight minus `best_match`, mean absolute error (negative is better), all stations:

| | J+1 | J+2 | J+3 | J+4 | J+5 |
|---|---|---|---|---|---|
| Daily minimum (degC) | -0.22 | -0.30 | -0.32 | -0.19 | -0.28 |
| Daily maximum (degC) | -0.04 (no difference) | -0.13 | -0.19 | -0.29 | -0.31 |

The interval excludes 0 everywhere except the maximum at J+1. A gain counts as real above **0.2 degC** (fixed before scoring): the minimum
clears it at four leads of five (J+4 is 0.01 short), the maximum at J+4 and J+5 only. In France alone the maximum at J+1 is slightly
**worse** (+0.13), and the minimum gain is smaller (-0.12 at J+1, -0.11 at J+4, both under 0.2). Rain: the mean has the lower MAE but
announces 36 to 69 % more rainy days than observed. Wind: the mean is worse in France at J+1 to J+3 and the score is dominated by an offset
between the observation and every model. The probability of precipitation cannot be scored at a fixed lead (the archive has none for
`best_match`).

## Decision

1. **Adopt the plain mean of the eight models for the daily minimum temperature, J+1 to J+5.** The sign never changes: at every lead, in
   France and elsewhere, and whichever station is left out (leave-one-out range -0.37 to -0.16); the mean has the lower MAE at 13 to 16 of the
   20 stations. J+4 (-0.19) is just under the useful size and is kept with the others, not carved out, because its interval is
   -0.25 to -0.14 and a rule with a hole at one lead is harder to keep than a rule with a margin that is stated.
2. **Adopt it for the daily maximum at J+4 and J+5 only.** -0.29 and -0.31, and -0.31 to -0.22 and -0.33 to -0.26 whichever station is
   left out. At J+1 to J+3 the maximum stays `best_match`: no difference at J+1, a certain but small gain at J+2 and J+3 (under 0.2), and a loss
   in France at J+1.
3. **Keep `best_match` for everything else**: the wind, the amount of rain, the weather code, the probability of precipitation, the UV,
   the current conditions and the 15-minute rain, and the days that were not measured (today, J+6 and later). The reasons are in the report:
   the mean over-announces rain days (its frequency of "1 mm or more" is 1.36 to 1.69 times the observed one against 1.04 to 1.36 for `best_match`),
   its better MAE is partly the smoothing of an intermittent field, the wind score measures an offset, and nothing was measured for the rest.
4. **Not adopted: the weighted mean and the median.** The median is the mean to within 0.04 degC on the temperatures. The weighted mean
   (weights learned on the first 30 days, scored on the last 31) is a little better on the maximum at J+1 (-0.13 against -0.05) but rests on
   one season and thirty days of learning; it is a thing to test on a second period, not a thing to ship.
5. **The implementation is another change, not this one.** It is behind a setting, and the owner decides its default (point 1 of "Open
   points"). Preconditions, each shown by the measure or by the code:
   - the weight rule `open_meteo_weight` (`app/collectors/weights.py`) ignores the `models` parameter; Open-Meteo multiplies the variables by the
     number of models. Shipped as it is, the budget of the API would count **an eighth** of what Open-Meteo counts. The rule is fixed first;
   - the models are the eight of the measure, in one constant. `meteofrance_seamless` has nothing at J+4 and J+5 (the mean there is of seven,
     which is what was measured); a day where no model answers falls back to `best_match`. How many models are enough was not measured;
   - only the two temperatures are asked of the models (`temperature_2m_max`, `temperature_2m_min`): the weight is 1.6 for the request, and
     asking the four measured variables or all seven would weigh 3.2 and 5.6 for nothing that was adopted;
   - a day takes its minimum from the mean and its maximum from `best_match` at J+1 to J+3: the two do not come from the same model, and
     the maximum must stay at or above the minimum; whoever implements it checks that and says what it does when they cross (not measured).

## Consequences

- **Nothing in `app/` changes with this ADR.** It adds a measurement tool (`tools/measure_forecast_models.py`), its tests (no network), the
  results file the report is written from (`docs/measurements/forecast-models-2026-10-results.json.gz`, about 80 KB) and the report. The
  figures of the results file are the same on every run (only its generation time changes).
- If the setting is turned on, the forecast gets about 0.2 to 0.3 degC closer on the minimum at every lead and 0.3 on the maximum from the
  fourth day (the errors of the measure, against the stations of the measure, in autumn).
- **Its price, computed from the weight rule and the refresh of the daily forecast (an hour):** the daily request weighs 1.0, the temperatures
  of eight models 1.6, so **2.6 per refresh instead of 1.0, and 62.4 weighted calls a day per cell instead of 24 (+38.4)**. The forecast of a
  cell costs about 130 a day today (`docs/data-sources.md`: 96 x 1.1 + 24 x 1.0); the models add **about 30 %** to that. The free tier is 10,000
  a day and the budget lets 90 % through.
- The tool follows the rules of the project for its own calls: a ledger of weighted calls booked before each request, a ceiling that
  refuses the one that would pass it, a pace under the limits of the free API, a User-Agent that names the tool and no person. A new run
  for another season costs about 1,350 weighted calls the first time and nothing from the cache afterwards.
- The parts a reader would otherwise take on trust (the windows of the daily files, the weight rule, the pairing by day, the bootstrap)
  each have a test that needs no network, and an independent recomputation of every forecast and observation cell of the run found no
  difference.

## Open points

1. **The quota is the owner's decision.** +38.4 weighted calls a day per cell, on an address that other programs may share. A cheaper way
   exists and was **not measured**: the models update every 3 to 6 hours (`docs/data-sources.md`), so asking them every 3 hours would weigh
   12.8 a day per cell and every 6 hours 6.4, at the price of a staler mean. It needs its own measure of what staleness costs.
2. **One season.** 61 days of late summer and autumn. No winter, no snow, no cold-air pool, a southern hemisphere in late winter and spring.
   The tool takes `--end-date` and `--days`: a run over another season (about 1,350 weighted calls) would confirm or undo the rule. The
   recommended order is a second period before the default of the setting is changed.
3. **Which models.** The eight are what the budget allowed. ECMWF's 9 km model, the other regional models and the models with a short archive
   were not asked. `jma_seamless` and `ecmwf_aifs025_single` have a cold bias on the maximum of -1.8 to -1.9 and -1.4 to -1.6 degC: whether leaving them out
   of the mean helps was not tested (the mean beats each of its members, with them in).
4. **The probability of precipitation** cannot be measured at a fixed lead with this archive. Measuring it needs the API's own forecast kept
   each day and compared with the observation later (a prospective log), which is a change of the application, not of a tool.
5. **Rain.** Read at the threshold where the mean announces as many rainy days as `best_match`, it ranks the days better at J+1 to J+4 (critical
   success index +0.05 to +0.09 over all the stations), with intervals that stay close to 0 and no help from the two stations of the world. That
   is a result to confirm on more days and more stations with a rain gauge before anything depends on it.
6. **Wind.** The observation (the strongest averaged wind of the day) and the models (an instantaneous hourly wind) differ by about 2 m/s for
   every competitor; comparing the models on wind needs an observation or a forecast of the same kind (a daily maximum of gusts, for instance).
7. **A rule per region.** In France alone the minimum gain at J+1 and J+4 is under the useful size and the maximum at J+1 is worse; elsewhere
   the gain on the maximum is larger at every lead. One rule for the whole world was kept because the sign of the minimum never changes
   and the maximum rule starts at J+4, where France agrees; a rule per region would be a decision for a second period.
