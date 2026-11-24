# 0044. Home Assistant: the card as a dashboard resource, and drawn at once from its last answer

- Status: accepted
- Date: 2026-11-24
- Number: given by `scripts/prochain_adr.py` on 2026-11-24.
- Changes point 4 of [ADR 0042](0042-home-assistant-weather-of-the-place-and-card.md) (how the frontend loads the card);
  the rest of 0042 stands.

## Context

ADR 0042 ships the card inside the integration: `card.py` serves `frontend/vigie-card.js` and adds it to the frontend with
`add_extra_js_url`, so there is no resource to add by hand. Two things were seen on a real Home Assistant (2026.11.3) on
2026-11-24:

1. **After a restart, the Vigie card was the only card in error** ("Configuration error") until the page was reloaded. The
   frontend is up in the first stage of a start; a custom integration is set up later (here about 4 s of its own setup, once the
   others of its stage had begun). A page opened in between gets an `index.html` without the card, and the frontend of that
   version does not load an extra module added afterwards (no use of `frontend/subscribe_extra_js` in the 109 files of the
   frontend that page loaded, although Home Assistant answers the command). The cards installed by HACS do not have the
   problem: they are dashboard resources under `/local/community/`, which the frontend serves from its first second.
2. **The card was the last one to appear.** The other cards draw from the states the page already has; this one asks
   `vigie/card/data` once it exists, and draws on the answer. Measured with Chromium on the home dashboard (tiles and this
   card): 37 ms after the tiles on the local network, 230 to 350 ms on a simulated mobile network (60 ms round trip, 12 Mbit/s).
   Home Assistant answers in 3 to 12 ms; the answer weighs about 97 KB (`point` 52 KB, the spot's verdicts 29 KB).

The file itself (121 KB) is not on the critical path: the page loads it long before the dashboard is drawn. It was sent
uncompressed and without a cache lifetime, where `/local` serves a precompressed `.gz` beside a file and caches for 31 days.

## Decision

1. Where the dashboards keep their resources in storage (Home Assistant's default) and the frontend serves the `www` folder
   of the configuration as `/local`, the integration **copies the card to `www/vigie/vigie-card.js`**, with a gzip copy beside
   it, and **lists it as a dashboard resource** (`/local/vigie/vigie-card.js?v=<digest>`, type `module`) at its setup. The
   frontend then has it from the first second of a start, like the cards of HACS. The digest of the file in the URL lets a
   browser keep it until it changes. The resource is created, updated when the card changed, and its doubles removed; any
   other resource is left alone. The file is rewritten only when it differs.
2. **Elsewhere, the way of 0042 stands**: resources in YAML (they cannot be written), no `www` folder when Home Assistant
   started (the frontend serves `/local` only when the folder existed then, so a resource made now would answer 404 until the
   next start), or a copy or resource that fails (a warning in the log). The integration's own path `/vigie/vigie-card.js` is
   still served in every case, so a resource added there by hand keeps working.
3. **The last entry removed takes the resource and the copy with it** (a folder that holds something else is left), and an
   entry added again in the same run of Home Assistant puts them back.
4. **The card keeps the last answer of its request** (an entry and a spot), in memory for the open page and in the browser's
   storage for the next visit, and draws it at once while it asks again. An answer older than 30 minutes is not drawn (the
   integration reads the instance every 10 minutes), and a request that fails shows its error as before, never the answer
   kept.

## Consequences

- After a restart of Home Assistant, a dashboard opened while it starts shows the card like the others; the change needs one
  restart to take effect (a custom integration's code is read at start).
- On a second visit within 30 minutes, the card is drawn with the other cards, then brought up to date; on the first visit it
  still waits for its answer.
- The integration writes into the configuration's `www` folder and the dashboards' resources, which it did not before. Both
  are visible to the user (the folder, and Settings, Dashboards, Resources) and named after the integration.
- The `card` job of the workflow runs with a `www` folder and fails unless the frontend loaded the card from the resource;
  the `install` job, on a Home Assistant that never ran, has none and goes the way of 0042. The cache is tried in Node
  (`tests_ha/card_harness.mjs`).
- Not done: making the answer lighter (the hours of `point` and the spot's verdicts are most of it); not needed once the card
  draws its last answer at once.
