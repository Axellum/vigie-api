# ADR 0013: travel advice, the ministries side by side

- Status: accepted
- Date: 2026-10-28

## Context

The product plan of 2026-10-28 (lot D) asks for the places foreign ministries advise their citizens against, from the
official advice. Five ministries publish an open feed (the United Kingdom's FCDO, the U.S. Department of State, Global Affairs
Canada, Australia's Smartraveller, the Netherlands' NederlandWereldwijd); Germany's interface is given on request, and France has
no live feed. Each ministry has its own scale (four numbered steps, four colours, or the FCDO's statuses for the whole country or
for parts of it), names its destinations its own way (FIPS codes, ISO alpha-2 or alpha-3, page names), and describes the areas it
advises against inside a country in words only: none publishes a shape.

## Decision

1. **The ministries stay side by side.** Each advice keeps the ministry's level in its own words, its text untouched, the date of
   its version and the time Vigie read it. No ministry's level is converted, merged or replaced.
2. **One common scale, ours, for comparing only** (1 normal precautions, 2 increased caution, 3 avoid non-essential travel, 4 avoid
   all travel). Each advice gets a range on it (`common_min`, `common_max`): the numbered scales map step for step, the FCDO's
   statuses give a range because it has no step for "normal" or "caution" and a status for parts says nothing of the rest of the
   country. It is listed in `derived` and is never shown as an official level.
3. **A disagreement** is two ministries whose ranges cannot meet; the gap is the number of steps between them. Only advices for a
   whole country are compared (`part` marks an advice for "Gaza" or the "Azores").
4. **No shape is made from a text.** The areas inside a country stay in the ministry's text; a map colours whole countries only.
5. **Vigie archives the versions** it reads (`travel_changes`), from the day an instance first reads the ministries. What came
   before is not known, and the answer says when the archive starts.
6. **A source that is not open is not read.** Germany and France are listed with the reason; the requests to their ministries
   are drafted for the owner to send (access, commercial use for the app to come). Nothing is scraped.

## Consequences

- A visitor can see that the Department of State says "Level 4: Do Not Travel" for Uganda while Smartraveller says "Exercise a
  high degree of caution" (2026-10-28), in their words, with the dates.
- The FCDO costs 226 pages once (15 MB), then only its changes: the pages are kept in the database across restarts.
- A new ministry is a collector, a credit, a line of commercial use, a row of `SOURCES.md`, recorded answers with a README, a
  check in `tools/check_live_feeds.py`, and its scale in the table of `docs/data-sources.md`.
