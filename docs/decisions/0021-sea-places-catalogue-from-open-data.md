# 0021. A catalogue of the places of the coast, built offline from open data, with no forecast of its own

- Status: accepted. The first step of the sea pages beyond the 51 surf spots; the verdict of an activity at a place that is not a surf spot is **not** decided here.
- Date: 2026-11-08

## Context

The sea pages answer "can I go in the water here, for my activity?" ([ADR 0016](0016-activity-verdicts-from-rules-of-ours.md)), but only
for the 51 surf spots. A swimmer, a diver, a kayaker or a skipper does not stand on a surf spot: they stand at a port, at a slipway, at a
bathing site, over a dive site, in an anchorage. Before any verdict can be given there, the project needs to know **where these places
are**, from sources that can be named and reused.

Three open sources cover most of it, and none covers all of it:

- the **Sandre** list of the ports of the French maritime space (Licence Ouverte), 671 ports, 446 of them in metropolitan France with a
  "Plaisance" activity;
- the **sanitary quality of the bathing sites** (data.gouv.fr, Licence Ouverte 2.0), 3,340 sites, 2,054 of them coastal;
- **OpenStreetMap** (ODbL 1.0), the only one with slipways (4,766 elements for France), anchorages, dive sites and the marinas that the
  Sandre does not list.

## Decision

1. **A bundled file, built offline** (`app/data/sea-places.json.gz`, `tools/build_places.py`), as the surf spots and the buoys are. The API
   reads nothing from the sources when it answers: the same answer, no quota, no outage. It is rebuilt by hand, and the file says the date
   of the OpenStreetMap data it holds (`osm_data_utc`).
2. **Five kinds, and no more for now**: `port`, `slipway`, `anchorage`, `dive`, `bathing`. Generic beaches (`natural=beach`) are not in:
   the monitored bathing sites say where swimming is checked, and a beach nobody monitors is a place the project would be guessing about.
   Business listings (diving centres, schools, shops, rentals) are not in.
3. **A place is on the coast**: metropolitan France with Corsica, **within 2 km of the OpenStreetMap coastline**. A lake, a river or a quarry
   is not a sea place. Together with the merge of close places (point 4), this leaves 17 of the 54 dive sites OpenStreetMap lists in
   metropolitan France (the others are karst springs, quarries and lakes) and 1,439 of the 4,340 slipways. The 2 km is a setting of ours,
   not a finding.
4. **One source per kind, completed only where it is missing**: the Sandre for the ports, with the OpenStreetMap marinas added when they
   are not within 1.5 km of a Sandre port (68 of them); OpenStreetMap for the slipways, anchorages and dive sites; the bathing-site file for
   the bathing sites. A marina whose name begins like a school, a beach or a mooring is not a port; a slipway marked private, closed, for
   customers or destination only is left out; an anchorage or a mooring marked private is left out, and a mooring is kept only when it is
   declared or open. Two places of the same kind a few hundred metres apart are one (the Sandre first, a named element before an unnamed one).
5. **Nothing is invented where the sources are silent.** No fee, depth, number of berths, state or opening hours: the tags are too rare and
   too uneven to say. A slipway that OpenStreetMap does not mark closed is listed and may be closed in fact; the answer says so (`note`).
   Most slipways (1,227 of 1,439) and anchorages (214 of 249) have no name: they are given `near`, the nearest named locality of
   OpenStreetMap within 6 km (worked out by Vigie, named in `derived`), and the site writes "slipway near X". A locality called "16th
   arrondissement" is not used as a name.
6. **The bathing class is context, never a verdict.** `quality` is the class of the water over the four years 2021 to 2024, as the dataset
   calculates it. It says nothing of today's water, the waves, the current or the jellyfish, and the answer says so. The dataset is a
   **republication by a third party** (TACCT) of the Ministry of Health's results with the official method, not the ministry's own file;
   the credit names both, and the official site stays the reference.
7. **Each place has a stable id** (`<kind>-<name or locality>-<four hex of the source reference>`), checked unique at build time, and keeps
   its `source` and `ref` (the Sandre code, the OpenStreetMap element, the bathing site) so that it can be traced.
8. **Licences**: the file is a database made from OpenStreetMap and is shared under the ODbL with its own notice
   (`app/data/sea-places.NOTICE.md`); the Sandre and bathing rows keep their Licence Ouverte. Each answer carries the credits of the sources
   of the places it holds. All three are `allowed` for commercial use in `app/commercial.py`.
9. **The route** `GET /v1/sea/places` filters by kind (`type`, repeatable), by distance from a point (`near_lat`, `near_lon`, `radius_km`,
   nearest first, with `distance_km`), by box (`bbox`, `west,south,east,north`) and by a word of the name, the locality or the commune (`q`,
   without accents, capitals, hyphens or apostrophes). It pages (`limit`, `offset`) and says how many match by kind. `GET /v1/sea/places/{id}`
   gives one. The answer is the same for a day (`Cache-Control: public, max-age=86400`): it changes with a release.

## What this does not decide

- **A forecast or a verdict per place.** None is given here. Giving one needs a wave and wind model at the place, and the project's
  decision on the shared Open-Meteo budget (the 10,000 requests a day shared by every Vigie endpoint) is still open: a forecast for 4,000
  places is not something to add without it. The first step the sources allow is the verdict on the grid cell of the place, which asks
  nothing more of the providers than the surf overview already does; it is for a later lot.
- **Profiles of boats and of other activities** (design categories of the Recreational Craft Directive, paddle, light sailing): they need
  states added to the embedded frame, which the screen decodes with a fixed vocabulary, and are written as their own decision.
- **Whether a place may be used.** An anchorage is a charted area, not an authorisation to anchor; a bathing site that is not listed is not a
  site where swimming is forbidden.

## Result (measured on 2026-11-08)

From the answers cached on that day (OpenStreetMap data of 2026-11-08 11:17 UTC, Sandre file of 2023-12-19, bathing file of 2026-07-09):
**4,071 places**: 506 ports (438 Sandre, 68 OpenStreetMap), 1,439 slipways, 249 anchorages and mooring zones, 17 dive sites, 1,860 bathing
sites (1,426 excellent, 320 good, 64 sufficient, 45 poor, 5 not classified); 144 KiB gzipped. 56 places have no locality within 6 km.
Checked in `tests/test_build_places.py` (what is kept, dropped and merged, on synthetic sources; the same bytes at every build) and
`tests/test_sea_places.py` (the real file: every kind present, ids unique, coordinates in metropolitan France, known ports found, the
search, the paging, the credits, the errors). **Not checked**: that every listed slipway can be used, and that the 2 km to the coastline
leaves out no sea place that sits further up an estuary.
