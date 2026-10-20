# Recorded Vigicrues answer

The real flow of Vigicrues, recorded on **2026-10-20 at 11:56 UTC** and played back by the tests (no test reaches the
network).

| File | From | Kept |
| --- | --- | --- |
| `InfoVigiCru_subset.geojson` | `https://www.vigicrues.gouv.fr/services/InfoVigiCru.geojson` | 31 of the 337 sections, **re-serialized** (the whole flow is 2.2 MB; its order of keys and spaces are not kept, its values are): the envelope (`RefInfoVigiCru` `20102026_10`, `DtHrInfoVigiCru` `2026-10-20T07:55:15+00:00`), the **19 sections above green** that day (17 yellow, 2 orange: the Vistre `GA32` and the Vidourle `GA6`) and 12 green ones |
| `InfoVigiCru.headers.txt` | the response headers | `Cache-Control: max-age=120`; no `ETag`, no `Last-Modified`, no compression |
| `img_france.headers.txt` | `HEAD https://www.vigicrues.gouv.fr/ftp/img_france.png` at 13:35 UTC, the national map the publication regenerates | `Last-Modified: Tue, 20 Oct 2026 07:55:36 GMT` and an `ETag`: 21 seconds after the recorded flow (`DtHrInfoVigiCru` 07:55:15). The collector asks this instead of the flow |

Watched live the same afternoon: at the next publication the flow became `RefInfoVigiCru` `20102026_16`, `DtHrInfoVigiCru` 13:57:55 UTC, and the image `Last-Modified` 13:58:16 GMT, 21 seconds after again (not
kept as a fixture: the flow weighs 2.2 MB).

The same day, the official bulletin of the territory Grand Delta (`https://www.vigicrues.gouv.fr/bulletin_local/20`,
published 09:55) said the Vistre and the Vidourle stayed in orange vigilance and the Gardon d'Alès was yellow: the
comparison that fixes the table 2 = yellow, 3 = orange. No section was red that day: `tests/test_vigicrues.py` reaches a
red one by changing a level in the recorded flow, and says so.

Licence of the content: Licence Ouverte 2.0 (Etalab), source Vigicrues (SCHAPI). See `SOURCES.md`.
