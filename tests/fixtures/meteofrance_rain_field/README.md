# Météo-France AROME / ARPEGE 1-hour total precipitation, real answers of the portal's WCS

Three `GetCoverage` answers of the Météo-France API portal (`TOTAL_PRECIPITATION__GROUND_OR_WATER_SURFACE___<run>_PT1H`),
read on 2026-11-22 at about 12:55 UTC, saved verbatim (GRIB2, `grid_simple`, `shortName="tp"`, `units="kg m**-2"`,
`stepType="accum"`, 1-hour step ending at 2026-11-22T14:00:00Z). Each was cut to a small box with `subset=long(...)`
and `subset=lat(...)` so that the test fixtures stay small while containing both wet (`>= 0.1 mm/h`) and dry pixels:

| File | WCS service | Run | Box | Grid |
| --- | --- | --- | --- | --- |
| `arome-tp-2026112209-05h.grib2` | `MF-NWP-HIGHRES-AROME-0025-FRANCE-WCS` | 2026-11-22 09 UTC | 5.5 W-3 W, 47.5-49 N | 101 x 61, 0.025 |
| `arpege-europe-tp-2026112206-08h.grib2` | `MF-NWP-GLOBAL-ARPEGE-01-EUROPE-WCS` | 2026-11-22 06 UTC | 6 W-0, 45-50 N | 61 x 51, 0.1 |
| `arpege-globe-tp-2026112206-08h.grib2` | `MF-NWP-GLOBAL-ARPEGE-025-GLOBE-WCS` | 2026-11-22 06 UTC | 0-10 E, 40-48 N | 41 x 33, 0.25 |

The data are under Licence Ouverte 2.0 (Météo-France, see `SOURCES.md`). Used by `tests/test_rain_field.py`.
