# Météo-France AROME / ARPEGE wind at 10 m, real answers of the portal's WCS

Six `GetCoverage` answers of the Météo-France API portal, read on 2026-11-21 at about 23:45 UTC, saved as they came (GRIB2,
`grid_simple`), nothing changed. Each is one component (`u` east, `v` north) at 10 m of the run of 2026-11-21 18 UTC, valid on
2026-11-22 at 00 UTC (lead 6 h), cut to a small box with `subset=long(...)` and `subset=lat(...)` so that the files stay small:

| File | WCS service | Box | Grid |
| --- | --- | --- | --- |
| `arome-{u,v}-2026112118-06h.grib2` | `MF-NWP-HIGHRES-AROME-0025-FRANCE-WCS` | 5.5 W-3 W, 47.5-49 N | 101 x 61, 0.025 |
| `arpege-europe-{u,v}-2026112118-06h.grib2` | `MF-NWP-GLOBAL-ARPEGE-01-EUROPE-WCS` | 6 W-0, 45-50 N | 61 x 51, 0.1 |
| `arpege-globe-{u,v}-2026112118-06h.grib2` | `MF-NWP-GLOBAL-ARPEGE-025-GLOBE-WCS` | 0-10 E, 40-48 N | 41 x 33, 0.25 |

The data are under Licence Ouverte 2.0 (Météo-France, see `SOURCES.md`). Used by `tests/test_wind_field.py`.
