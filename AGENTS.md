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
   circuit breaker, the cache and the fallback. Keep `GET /v1/snapshot` under 12 KiB and the frame
   under 8 KiB.
4. **Tests never touch the network** (`tests/conftest.py` refuses every `httpx` request). Mock
   the collectors; use real provider payloads as samples.
5. **Everything is in English**: code, comments, messages, documents, commits. The product name
   is "Vigie". No version number in a name (versions are git tags).
6. **No secret in the repository.** No provider needs a key. Contact data (`VIGIE_CONTACT`) and
   the CAP sender are environment variables. Do not write a private repository URL or an invented
   address into code or into data sent to third parties.
7. **Respect the providers' terms** ([`docs/data-sources.md`](docs/data-sources.md)): the project
   is non-commercial (Open-Meteo free API), cache aggressively, send an identifying `User-Agent`.
8. **Git**: work on a branch, one commit per topic, stage files one by one, open a pull request
   against `main`; never push to `main`.
9. Add a line to [`CHANGELOG.md`](CHANGELOG.md) (`[Unreleased]`) for any user-visible change.
