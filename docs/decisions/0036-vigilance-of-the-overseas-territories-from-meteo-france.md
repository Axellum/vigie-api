# 0036. The official vigilance of the overseas territories, from the other entry points of Météo-France's `DPVigilance`

- Status: accepted
- Date: 2026-11-21
- Number: given by `scripts/prochain_adr.py` on 2026-11-21 (0037 went to the overseas radar mosaics of the same day).

## Context

`DPVigilance/v1/cartevigilance/encours` (ADR of the metropolitan collector, `app/collectors/meteofrance.py`) holds the metropolitan departments only. MeteoAlarm has no feed for the overseas territories, so until now `GET /v1/vigilances` for Guadeloupe, Réunion or New Caledonia answered "country not supported". The overseas warnings are published apart; three entry points were read on 2026-11-21 with the project's key (one request each):

| Entry point | Content | Territories |
| --- | --- | --- |
| `vigilanceom/flux/dernier` | a ZIP of about 1 MB (mostly PDF bulletins) | JSON maps of the Antilles and Guyane (one file per territory or pair), of La Réunion and of Mayotte; the forecasters' bulletins of La Réunion and Mayotte; the level of Saint-Pierre-et-Miquelon as a small XML |
| `polynesie/cartevigilance/encours` | a JSON map | French Polynesia |
| `ncaledonie/cartevigilance/encours` | a JSON map | New Caledonia |

All the maps have the shape of the metropolitan one (`timelaps.domain_ids` of `phenomenon_items` with `color_id` and time slots) with their own phenomenon ids. A territory has one domain of its own (`VIGI971`, the highest level of each phenomenon) and sub-zones (`VIGI971-01`, ...). Real answers of the evening of 2026-11-21: Guadeloupe yellow for rain and thunderstorms, French Polynesia yellow for waves and submersion, the others without a level. Reduced copies are the fixtures of `tests/fixtures/meteofrance_vigilance_overseas/`.

## Decision

1. **One collector for the three feeds** (`app/collectors/meteofrance_overseas_vigilance.py`, `meteofrance_vigilance_overseas`): the same key and `apikey` header as the metropolitan collector, one cache of 5 minutes per feed, so at most three requests every 5 minutes whatever the number of territories asked. A file of the ZIP that cannot be read leaves its territory out; an archive with no readable map is an error (the country is then `unavailable`, never "no warning").
2. **Same routes, same levels, same archive.** The countries `GP`, `MQ`, `GF`, `RE`, `YT`, `MF`, `BL`, `PM`, `PF`, `NC` join the supported countries, in the plan `meteofrance` only (nothing stands in: MeteoAlarm has no data for them). Colours 2, 3 and 4 give yellow, orange and red as in the metropolitan map; the higher ids are listed with `level` null and the id in `level_original`; 0, -1 and 1 are not warnings. The warnings go through the same archive as the other sources.
3. **A warning covers its territory.** The shapes of the sub-zones are not published; each territory's warning carries the geocode `MF_OM` (codes 971 to 978, 987, 988) whose shape is the border of the country of `countries.geojson.gz` widened by 0.25 degree (the margin the country lookup already gives a point at sea, since these warnings speak of the sea: waves, submersion, cyclone). Built by `tools/build_overseas_geocodes.py`, no download. The sub-zones that carry a level are named in the `zones` parameter of the warning.
4. **Off switch**: `VIGIE_METEOFRANCE_VIGILANCE_OVERSEAS=0` (on by default when the key is set). Without the key, `GET /v1/health` lists the source in `disabled_sources`.

## Not verified

- The meaning of the phenomenon ids and of the colours above 4 is not documented in anything read here. The tables follow the open-source integration of the Home Assistant community (`hacf-fr/meteo_france_vigilance`) and the titles of the bulletins of La Réunion and Mayotte; French Polynesia's ids are assumed to be the metropolitan ones.
- No orange, red or cyclone warning was ever observed in these feeds: those paths are tested on altered copies of the real files, not on real events.
- The Saint-Pierre-et-Miquelon XML was read only at level 1 (no warning); the parser of a higher level is the same colour table, untested on a real one.
- The licence: Licence Ouverte 2.0 as the metropolitan dataset; the overseas entry points were not looked up separately on data.gouv.fr.
- Which sub-zone a point belongs to (no shapes): a warning of one sub-zone of an island is served to the whole territory.
