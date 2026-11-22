# 0039. Open-Meteo for the world: a self-hosted instance first, the public API as a fallback

- Status: accepted
- Date: 2026-11-22
- Number: given by `scripts/prochain_adr.py` on 2026-11-22.

## Context

Outside France the forecast, the sea, the surf spots and the air quality come from the public Open-Meteo API, whose free
plan allows 10,000 calls a day (`OPEN_METEO_QUOTA`). The more places the site and the screens follow, the closer the API
gets to that ceiling, and the free plan is for non-commercial use only.

Open-Meteo publishes its model data in a public bucket (CC BY 4.0) and its server as open source (AGPLv3): the same
answers can be served by an instance of our own. Measured on 2026-11-22 on a small ARM virtual machine (4 cores, 24 GB):

| | |
| --- | --- |
| Disk | about 83 GB for a pruned set of models (ECMWF IFS 9 km, GFS, the GEFS and ECMWF ensembles, CAMS, ICON-EU and D2, and regional models of North America, Japan and northern Europe) plus the 90 m terrain |
| Continuous sync | half a core, about 500 MB of memory |
| Speed | about 7 requests a second from the disk, 790 to 840 once the data is in memory |
| Quality | 0.99 °C mean error of the temperature at 49 airports (METAR), against 0.97 °C for the public API |

The instance can still stop, lag behind a run, or lack a model the public API has (GloFAS for the floods is not in the
bucket).

## Decision

1. Every Open-Meteo URL can point to a self-hosted instance: `VIGIE_OPEN_METEO_FORECAST_URL` and
   `VIGIE_OPEN_METEO_MARINE_URL` (already there) and `VIGIE_OPEN_METEO_AIR_QUALITY_URL` (new). The flood API stays public.
2. When such a URL is set, the request goes to the instance first (`app/collectors/open_meteo_hosts.py`). It is not counted
   in the public quota.
3. The same request goes to the public API, counted as before, when the instance cannot be reached in 5 seconds (it is then
   left aside for 60 seconds), answers an error or no JSON, or answers without a single value. The public API is the
   fallback, not a second source: the answer used is one or the other, never a mix.
4. `VIGIE_OPEN_METEO_PUBLIC_FALLBACK=0` turns the fallback off (for a test of the instance alone).
5. The credit stays "Open-Meteo" (CC BY 4.0): the data is theirs. The server runs unmodified, so the AGPLv3 asks nothing more
   of this API.

## Consequences

- The public quota is spent only on the fallbacks; with no URL set nothing changes.
- A place the instance answers with nulls (a model it does not sync) costs one request to each.
- The counters `self_hosted_calls` and `public_fallbacks` of each collector say how often the fallback is used (in memory,
  not in the health answer yet).
