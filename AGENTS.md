# AGENTS.md

Guide for AI coding agents (and humans in a hurry) working on the Vigie API.

## What this is

A FastAPI backend that aggregates world hazards and weather and serves them to the Vigie ESP32-P4
displays (separate repository, `vigie-display`) through a text frame. Read
[`README.md`](README.md) and [`docs/architecture.md`](docs/architecture.md) first.

## Commands

```
python -m pytest                          # all tests, no network
python -m ruff check .                    # lint (CI runs it)
python scripts/fragments.py --check        # the fragments of changelog.d/ and sources.d/ are valid and apply (CI runs it)
python scripts/prochain_adr.py            # the next free ADR number, read on origin/main and in the open pull requests
scripts\run.ps1                           # run the API on Windows (port 8080)
python tools/capture_frame.py out.txt     # write a REAL frame from the live providers
python tools/check_live_feeds.py          # ask each real provider once (the weekly live-feeds workflow does it)
python tools/build_geometries.py          # rebuild app/data (borders, NUTS shapes) from Natural Earth and Wikidata
python tools/fake_instance.py             # a fake instance with warnings you add by hand (to try the Home Assistant integration)
python tools/measure_archive.py           # what the archive of the warnings takes in over a real day, on an instance you started
python -m pytest tests_ha                 # the Home Assistant integration's tests: its own venv, Python 3.14.2+ (docs/home-assistant.md)
```

Python 3.12 or newer. Use PowerShell 5.1-compatible commands when you give a command to the
owner (no `&&`).

## Rules

1. **Never fabricate data.** A value a provider did not supply is unknown: `None` in models,
   `null` in JSON, an empty field in the frame. No default number, no copy of another field, no
   estimate, no label that names a provider that did not answer. If a parser cannot read the
   essential value, it raises (the caller falls back) or skips the item. See
   [ADR 0001](docs/decisions/0001-two-repos-no-fabricated-data-frame-format.md).
2. **The frame contract is [`docs/embedded-frame.md`](docs/embedded-frame.md).** It is the single
   source of truth, pinned by `tests/test_embedded_frame_contract.py`. Changing a column, its
   order, its count or its meaning means: update the document, the generator, the tests and the
   display decoder in the other repository, in the same change. Do not modify the display
   repository from here.
3. **A failing provider never makes the API fail** and never blocks the event loop: keep the
   circuit breaker, the cache and the fallback. Keep the compact `GET /v1/snapshot` under 16 KiB
   (it carries the credit of every item, see rule 11) and the frame under 8 KiB.
4. **Tests never touch the network** (`tests/conftest.py` refuses every `httpx` request). Mock
   the collectors; use real provider payloads as samples.
5. **Everything is in English**: code, comments, messages, documents, commits. The product name
   is "Vigie". No version number in a name (versions are git tags).
6. **No secret in the repository.** No provider needs a key; NASA FIRMS accepts an optional one (`VIGIE_FIRMS_MAP_KEY`, read from the environment; it is in the URL of
   every area API request, so it is redacted in every error, `last_error` and log line: `tests/test_firms_secret.py`, never print it). Contact data (`VIGIE_CONTACT`) and
   the CAP sender are environment variables. Do not write a private repository URL or an invented
   address into code or into data sent to third parties.
7. **Respect the providers' terms** ([`SOURCES.md`](SOURCES.md) lists them; how each source is used is in
   [`docs/data-sources.md`](docs/data-sources.md)): the project is non-commercial (Open-Meteo free API),
   cache aggressively, send an identifying `User-Agent`.
8. **Git**: work on a branch, one commit per topic, stage files one by one, open a pull request
   against `main`; never push to `main`.
9. **Provider calls stay sober.** A request to a provider goes through `BaseCollector._get` (it is
   counted and held to the published quota in `app/collectors/budget.py`); the position is snapped
   to a grid; the number of days is not in a cache key; the TTL comes from the source's own update
   rhythm, with the evidence in [`docs/data-sources.md`](docs/data-sources.md). Do not send a
   request around these. `tests/test_call_sobriety.py` replays a day of 1,000 clients and fails if
   the Open-Meteo quota would be exceeded.
10. **Changelog and sources by fragments.** Do not edit [`CHANGELOG.md`](CHANGELOG.md) in a pull request, nor add or change a row of a
    table of [`SOURCES.md`](SOURCES.md): every parallel pull request used to conflict on them. For a user-visible change add one
    file to [`changelog.d/`](changelog.d/README.md) (`<slug>.<added|changed|fixed|...>.md`, one bullet); for a source row, one file
    to [`sources.d/`](sources.d/README.md) (the section, the row, and the row it replaces if any). The tests read `SOURCES.md` with
    the fragments applied (`sources_page_text()`), CI runs `python scripts/fragments.py --check --base origin/main` and fails a
    pull request that edits `CHANGELOG.md`; a `release/` or `assemble/` branch puts the fragments in with
    `python scripts/fragments.py --write [--release X.Y.Z]`. A change of prose in `SOURCES.md` is still edited there.
11. **Credit every source.** A new source or block of data gets its credit text in `app/attribution.py`,
    an `attribution` field, and a row in [`SOURCES.md`](SOURCES.md) (`tests/test_sources_page.py` checks they
    match). The compact frame has no credit field. Write a text that is not the provider's own as "ours" and
    say what could not be verified: never invent a required wording.
12. **Public-facing text stays modest**: a personal project, made with AI, shared in case it helps; no
    promise, no marketing word, no detail of a private network or machine (`tests/test_public_text.py`).
    `POST /v1/simulate` stays off unless `VIGIE_ENABLE_SIMULATION=1`.
13. **Official warnings keep the issuer's words.** A `Vigilance` returns the identifier, times, original level and event,
    headline, description and instruction as the source wrote them; the API's own fields (`status`, `level`,
    `phenomenon`) come from the tables of `docs/data-sources.md`, are named in `derived`, and are `None` when no table
    covers the word. Never translate, shorten or reword an official text, and never guess a level. A shape that
    has no stated licence is not added to `app/data/` ([`SOURCES.md`](SOURCES.md) says which, and why).
    A country has one source: its national service when there is a collector for it (`NATIONAL_SOURCES` in
    `app/services/vigilance.py`), MeteoAlarm's copy only as a stand-in
    ([ADR 0003](docs/decisions/0003-national-source-preferred-rivers-are-lines.md)). A new national source is a collector,
    a credit, a row in `SOURCES.md`, recorded answers in `tests/fixtures/<source>/` with a README, a check in
    `tools/check_live_feeds.py`, and a line in `docs/data-sources.md` for its cache and its request count.
    A service of the WMO registry is **not** a collector: it is a record in `app/collectors/swic_terms.py` (its terms read at an
    address, the credit, `verified` or not). It is `verified` only when its own terms page or an open licence in its own feed was
    read; a bare "public domain" line is not enough, Hong Kong and China are never read, and `VIGIE_WMO_SOURCES` defaults to
    `verified` ([ADR 0006](docs/decisions/0006-wmo-registry-services.md)). A row of `SOURCES.md` goes with each record.
14. **The Home Assistant integration** (`custom_components/vigie`, `tests_ha/`) is code of this repository, in English like
    the rest, with the same rule: a value nobody supplied is unknown, never `none` or a default
    ([ADR 0004](docs/decisions/0004-home-assistant-integration-same-repository.md)). It reads `GET /v1/vigilances`: changing
    a field of that answer means updating what it reads, `tests_ha/payloads.py` and the recorded answer in
    `tests_ha/fixtures/` in the same change (`tests/test_home_assistant_contract.py` fails otherwise). `python -m pytest`
    never collects `tests_ha/` and never installs Home Assistant; the integration has its own CI workflow.
15. **Places near a hazard are ours, and say so.** `near_places` (faults, volcanoes in alert; **never the watched points**, which are
    personal and listed by `/v1/watched-points` only, because the hazards are shared with every client) is calculated by Vigie,
    named in `derived`, and never changes a `severity`; `null` is "not worked out", `[]` is "nothing near". Its radii are settings
    (`PlaceSettings`, `VIGIE_NEAR_*`), not facts: do not present them as sourced. `app/data/gem-active-faults.geojson.gz` is
    **CC BY-SA 4.0** (share-alike), rebuilt only by `tools/build_faults.py` from the pinned commit, with its notice beside it; the code
    stays MIT. A fault the database does not name is not named. A watched point is `quiet` only when every hazard source answered and
    was read without a gap ([ADR 0008](docs/decisions/0008-sensitive-points.md)); the compact `/v1/snapshot` and `/v1/point` carry no
    `near_places`. `POST` and `DELETE /v1/watched-points` are off unless `VIGIE_ENABLE_WATCHED_POINTS_WRITE=1` and must not be reachable
    from the Internet on a public instance.
16. **A country with no source says why, and only what was read.** A reason (`app/services/uncovered.py`, `reason_code` + one sentence) is
    written for a country only with the evidence of what was read, and a row of `SOURCES.md` ("Countries with no source"); any other country is
    `no_source`, which claims nothing more. Do not write that a service forbids something that its terms do not say.
17. **Every route that changes something asks for the admin token when the instance sets one, and a new route cannot escape it.**
    `VIGIE_ADMIN_TOKEN` is checked by a middleware on the request method (`app/security.py`: anything but `GET`, `HEAD` and `OPTIONS` needs
    `Authorization: Bearer <token>`), not by a dependency on each route; nothing to add on a new route, and no route is to be exempted.
    `tests/test_admin_token.py` walks the routes of the application and fails if one with a write method answers without the token. The
    token appears in no log, answer or document (`GET /v1/health` only says `admin_token_set`); never write one in the repository.
18. **Travel advice keeps each ministry's words, side by side** ([ADR 0014](docs/decisions/0014-travel-advice-side-by-side.md)). A level, a
    text and a date are the ministry's, untouched; the country code, `part`, the common scale and the comparison are Vigie's, named in
    `derived`, never an official level. No shape is made from a text. A destination no table of `app/collectors/travel_codes.py` places
    is counted, never guessed. A ministry whose feed is not open (Germany, France) is not scraped: it is listed with the reason.
19. **ADR numbers are never taken from your own checkout.** Two parallel sessions took "the next free number" and four ADRs were
    merged with two numbers (2026-10-28). Run `python scripts/prochain_adr.py` (it reads `origin/main` and every open pull request;
    exit 3 means it could not read them, so do not trust the number), reserve that number where the sessions coordinate before
    writing, and give the file this number from its first commit. `tests/test_fragments.py` fails on two ADR files with one number.
