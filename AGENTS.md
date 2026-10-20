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
scripts\run.ps1                           # run the API on Windows (port 8080)
python tools/capture_frame.py out.txt     # write a REAL frame from the live providers
python tools/check_live_feeds.py          # ask each real provider once (the weekly live-feeds workflow does it)
python tools/build_geometries.py          # rebuild app/data (borders, NUTS shapes) from Natural Earth and Wikidata
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
6. **No secret in the repository.** No provider needs a key. Contact data (`VIGIE_CONTACT`) and
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
10. Add a line to [`CHANGELOG.md`](CHANGELOG.md) (`[Unreleased]`) for any user-visible change.
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
