# 0026. A self-hosted weather gateway behind Vigie, Vigie the only entry point

- Status: accepted
- Date: 2026-11-16

## Context

Two of the project's services read Météo-France:

- **Vigie** (this API): the warnings (`DPVigilance`), the 6-minute observations (`DPPaquetObs`), the avalanche bulletins
  (`DPBRA`), and everything else the site, the screens and the Home Assistant integration show.
- **vigie-meteo** ([Axellum/vigie-meteo](https://github.com/Axellum/vigie-meteo)), a small FastAPI gateway on a free cloud
  machine, next to a self-hosted Open-Meteo that syncs only Météo-France models (AROME HD, AROME HD 15 minutes, ARPEGE Europe,
  MFWAM waves, currents, sea temperature). It also read the warnings and the observations, so two services fetched the same
  data under the same key, with two caches that could disagree.

PIAF, Météo-France's 5-minute rain nowcast, answers one point per request and per lead time (WCS): it fits a gateway that
caches it, not a request path of Vigie.

## Decision

1. **Vigie stays the only entry point.** The site, the screens and Home Assistant read Vigie; nothing reads the gateway
   directly. The warnings and the observations are read by Vigie only.
2. **The gateway is Vigie's internal weather source**, optional (`VIGIE_METEO_GATEWAY_URL`): today it gives the PIAF rain of
   the next hour in France (`app/collectors/meteo_gateway.py`, vigie-api#85). Every value read through it keeps the fallback of
   rule 3: when the gateway is down, falls back to another model, or has no full hour, Vigie keeps Open-Meteo's answer.
3. **The duplicates are removed from the gateway**: its warning routes (`/v1/vigilances`, `/v1/vigilances/geojson`), its
   observation route (`/v1/observations`) and those blocks of `/v1/weather` and `/v1/compact`. No client called them (the
   service's own log, the monitoring, the Home Assistant configuration and the project's repositories were read on 2026-11-16).
4. **Moving Vigie's current conditions to the gateway's Open-Meteo is not done** (measured below). It is reconsidered when a
   commercial use needs data under Licence Ouverte instead of Open-Meteo's free API, and only once the missing fields are filled.

## Measured (2026-11-16, about 21:45 UTC, one measurement at night)

The current conditions Vigie asks for, at 16 French towns, compared with the nearest Météo-France station (`DPPaquetObs`, 15 km
at most; 15 stations with a temperature, 11 with a wind):

| Source | Temperature, mean absolute error | Wind, mean absolute error |
| --- | --- | --- |
| Open-Meteo public API (`best_match`) | 0.69 °C | 4.5 km/h |
| Gateway's Open-Meteo, AROME HD | 0.79 °C | 4.0 km/h |
| Gateway's Open-Meteo, AROME HD 15 minutes | 0.59 °C | no wind |
| Gateway's Open-Meteo, ARPEGE Europe | 1.41 °C | 5.7 km/h |

- The public API already uses Météo-France's models in France: the gateway is not more accurate (one measurement cannot tell
  more).
- AROME HD on the gateway gives **no `pressure_msl`, no `weather_code` and no `uv_index`** (`null`), three fields Vigie shows;
  its `minutely_15` rain is hourly data split in quarters.
- ARPEGE Europe stops at about 4 days: the 10-day forecast stays on the public API.
- The public quota is not a constraint (a few hundred weighted calls a day out of 10,000).

## Consequences

- One reader per Météo-France dataset; the gateway does one job (the data that needs a server near the models).
- The PIAF rain depends on a machine of the project: when it stops, Vigie falls back to Open-Meteo without an error shown.
- **Not verified:** the licence and the terms of PIAF (the portal's catalogue says it is free of charge and names no licence),
  and a measurement over several days and times of day.
