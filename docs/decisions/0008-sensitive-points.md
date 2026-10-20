# 0008. Sensitive points: what a hazard is close to, and the points the user watches

- Status: accepted (four points are questions for the owner: see "Open points")
- Date: 2026-10-20

## Context

A hazard on a map says where it is. Two things were asked on top of that: that an earthquake or a volcanic event says what it is
close to ("6 km from a fault", "12 km from Etna"), and that the user can watch points of their own (a town, a fault, a volcano) and ask
what happened near each in the last week. The sketches (20 October 2026) kept two of four: **A**, a mention under each hazard, marked
"calculated by Vigie"; **B**, a list of watched points with the recent events within their radius, saying "nothing in the last 7
days" only when the sources of the point answered, else "source unreachable". The banner at the top of a screen (**D**) was left out:
a banner that is not an official alert, at the head of a screen of alerts, would be taken for one. The website asked for a derived
field on the hazard, named in `derived`: `near_places: [{name, kind, distance_km}]`.

Questions: where do the names come from, and under which licence; how far is "near"; what happens when the data is missing; how can
"nothing happened" be said honestly by an API that only knows what it saw; what does it cost.

## Decision

1. **A derived field, never an alert.** `near_places` on a hazard is a list of `{name, kind, distance_km, sources}`, nearest first,
   at most 5, distances rounded to 0.1 km. It is named in the hazard's `derived`. It **never changes a `severity`** (a test plays
   every kind of hazard before and after). `[]` means it was worked out and nothing is near; `null` means it was not (the fault file
   cannot be read, the alert is simulated, or the compact profile). The same rule as the rest of the API: a value nobody supplied is
   unknown, not an empty answer ([ADR 0001](0001-two-repos-no-fabricated-data-frame-format.md)).
2. **Three sets of places, each for the hazards it makes sense for.** The named active faults, for earthquakes and volcanic events,
   when the depth is at most 70 km or unknown. The volcanoes **a hazard source names itself** (a USGS elevated alert, a GDACS eruption),
   for earthquakes. The points the user watches, for every kind of hazard. No list of the world's volcanoes is bundled: the Smithsonian
   Global Volcanism Program's licence is unclear and has not been asked about.
3. **The radii are settings, not sourced facts.** Faults 10 km, volcanoes in alert 20 km, depth 70 km, 5 places, 50 watched points,
   a watched point its own radius (up to 500 km). They are environment variables (`VIGIE_NEAR_*`, `VIGIE_WATCHED_POINTS_MAX`), written
   in the README and `docs/data-sources.md` as choices of this instance. Nobody publishes the distance at which an epicentre is "near"
   a fault, and the catalogues' positions are uncertain by an amount that was not verified for these feeds. 10 km was chosen as a round
   figure well above what the simplification of the traces can move them (0.56 km at most); 70 km is the usual limit between shallow
   and intermediate-depth earthquakes (not verified in a source of this project). A deep earthquake is not compared with a surface
   trace: two earthquakes 162 and 206 km down had a mapped trace within 4 km of their epicentre, and the sentence would have been false
   in spirit.
4. **The fault file is the GEM Global Active Faults Database, under CC BY-SA 4.0, bundled as a file of its own.** `tools/build_faults.py`
   downloads the pinned commit, refuses any other content (git blob id), keeps the traces that have a name (6,090 of 13,696), simplifies
   them (0.005 degree) and writes `app/data/gem-active-faults.geojson.gz` (189 KiB) with its licence, citation, source and a statement
   of the changes in the file itself, in `app/data/gem-active-faults.NOTICE.md` and in `app/data/README.md`. **Share-alike applies to
   that file** (and to anything made from it); the code that reads it stays MIT, as a separate work. That reading of the licence is the
   project's, not a lawyer's. A hazard that names a fault carries the citation in its `attribution` (`gem_faults`).
5. **A trace without a name is not kept.** The mention is "N km from <name>", and a name the database does not give is not made up.
   This leaves a hole that the owner should know about: 7,598 of the 13,696 traces are unnamed, and they are the Mediterranean and Asia
   (datasets that give catalogue numbers only). **The North Anatolian Fault is not named in the file**, nor any fault of Greece.
   Considered and not chosen: keeping them with `name: null` (every client would have to write a sentence about an unnamed fault, for a
   statement the sketches did not ask for, and the 67 earthquakes of 30 days that are near only unnamed traces would say less than they
   seem to); naming them from another dataset (its licence has to be read first).
6. **The distance is to the mapped surface trace**, worked out on a flat map scaled to the latitude and then as a great circle
   (`line_distance_km`, already used for river sections), and **checked against an independent computation** (Vincenty on the WGS84
   ellipsoid to a dense sampling of the original traces) on real earthquakes and on 30 days of them, to within the simplification
   (-0.47 to +0.46 km). It does not say that an earthquake happened on a fault.
7. **A watched point is a row of SQLite beside the zones** (`/v1/watched-points`: name, kind, position, radius; at most 50). Like the zones,
   the routes have no authentication.
8. **"Nothing happened" is a claim that needs proof** (the principle of [ADR 0003](0003-national-source-preferred-rivers-are-lines.md),
   point 6: a source that did not answer says nothing). A point with no event is `quiet` only if all seven hazard sources answered at the
   last reading and each has been read **without a gap** longer than 15 minutes (`VIGIE_QUIET_MAX_GAP_S`) for as long as the claim goes
   back. The history holds only what the API saw, so `quiet_since_utc` is the start of the window or, when the sources have been read for
   less, the start of the shortest run (`window_covered: false`): an instance started an hour ago cannot say "nothing in a week". The
   runs are kept in SQLite. An answer served from the cache after a failure is not an answer. Otherwise the state is `unknown` and names
   the sources that did not answer. No source is excused for a region: the API has no verified map of what each could report where.
9. **Events** come from the history of hazards and from those active now: an earthquake by the time it happened, any other hazard by
   the last time it was seen active; a simulated alert never counts; a hazard is compared by its position.
10. **The compact profile carries no places.** `GET /v1/snapshot` and `GET /v1/point` are held to 16 KiB with the credit of every item,
    and the realistic snapshot of the tests was at 14,457 bytes: a place and its citation on each of 15 hazards would not fit. They
    answer `near_places: null` (14,937 bytes). `compact=false`, `/v1/geojson/hazards`, `/v1/history` and the stream carry them.
11. **A number JSON cannot carry is a 422, not a 500.** Python's parser accepts `NaN`; the validation error that quotes it could not be
    encoded and the API answered 500 on any route with a number. One handler writes such a value as text. (Found while validating the
    new routes; it applied to `/v1/zones` as well.)

## Consequences

- A hazard weighs about 100 bytes more per place in the full profiles, plus the citation (about 200 bytes) once per hazard near a fault.
- Cost, measured on the author's PC: loading the file 31 to 36 ms and 4.2 MB once at start; 15 microseconds per earthquake; all the hazards
  of a request (56, the merged feeds of 2026-10-20) 2.5 ms, or 4.3 ms with 50 watched points.
- The database gets two tables (`watched_points`, `source_runs`); an older database gets them when it starts.
- The fault data is from 2021 and the repository has not changed since; the files of the tool pin it, and a newer version is a decision.
- Of 656 earthquakes M4.5+ in 30 days, 43 had a named fault under them, most aftershocks of the M 7.7 of Panama (2026-10-09). The
  feature will say little on most days, and nothing for the faults of the Mediterranean.

## Open points

- **The Smithsonian GVP volcano list** (about 1,200 Holocene volcanoes): the licence is unclear. Writing to the Global Volcanism Program is a
  public action of the owner. Until then Etna is named only while GDACS or the USGS report it, or when someone adds it as a watched point of kind `volcano`.
- **Unnamed faults.** Keep them with `name: null`, or find a second dataset that names the Mediterranean and Asian faults (its licence first)?
- **Public instances.** The list of watched points is public and unauthenticated, like `/v1/zones`, and the name of a point appears in
  the `near_places` of the hazards near it. A home must not be watched on an instance others can read; a switch that turns the routes off,
  or a key, is a decision for whoever runs one.
- **The compact profile.** If a display needs the places, the budget has to be argued again (fewer hazards, one place, a shared citation).
