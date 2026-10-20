# Recorded feeds

Real answers of the providers, recorded on **2026-10-20** and played back by the tests (no test reaches the network).
They are kept as the providers sent them: an entry is dropped or kept, never edited.

| File | From | Kept |
| --- | --- | --- |
| `atom_france.xml`, `atom_iceland.xml`, `atom_switzerland.xml`, `atom_germany.xml`, `atom_belgium.xml`, `atom_italy.xml`, `atom_norway.xml` | `https://feeds.meteoalarm.org/feeds/meteoalarm-legacy-atom-<country>` | the head of the feed, then the entries of a few CAP documents (France: 19 entries of 7 documents, among them an orange and a red flood warning; Switzerland: polygons; Germany: `EMMA_ID` codes only; Belgium: NUTS2; Italy: documents the server no longer has) |
| `atom_empty.xml` | the feed of Denmark | all of it: a valid feed without any warning |
| `cap_france_b77d3bc0.xml`, `cap_germany_581d4ba4.xml`, `cap_iceland_9c454e4a.xml` | `https://feeds.meteoalarm.org/api/v1/warnings/feeds-<country>/<id>` | all of each document (France in 2 languages, Germany in 8) |
| `nws_active_severe_extreme.json` | `https://api.weather.gov/alerts/active?status=actual&severity=Extreme,Severe` | 7 of the 18 alerts of the moment: the 2 with a polygon and 5 that name zones |

Two things in the French feed are worth knowing when reading a test: four orange flood warnings (the Hérault, the Aveyron,
the Gard, the Lozère) had ended twelve hours before the feed was read and were still listed, and the warnings for Corsica
mix a red, an orange and several yellow levels for the same département. The tests that need the warnings to be in force
move the simulated clock to `RECORDED_AT` in `tests/vigilance_samples.py`.

Licence of the content: MeteoAlarm "terms equivalent to CC BY 4.0, with additional requirements" (credit and notice in
the responses of the API), NOAA: public domain. See `SOURCES.md`.
