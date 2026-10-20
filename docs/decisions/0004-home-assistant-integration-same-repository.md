# 0004. Home Assistant integration: in this repository, polled, the push is a hint

- Status: accepted (the owner decided the location; the rest is the author's, to be confirmed on a real installation)
- Date: 2026-10-20

## Context

The plan of the opening of the project (lot 4) wants Home Assistant to be a client like another, without a screen: a
sensor for the level of the official warnings of a place, and an event to start automations. The API already has what is
needed (`GET /v1/vigilances`, and `WS /v1/stream` with `follow`). Questions: where does the integration live, how does it
get its data, how are the entities honest about what is unknown, and how does it tell a new warning from a re-issued one.

## Decision

1. **The integration lives in this repository** (`custom_components/vigie`, `hacs.json` at the root), not in a repository
   of its own. One version, one place for the recorded answers, and the contract is checked against the API's own models in
   the API's CI (`tests/test_home_assistant_contract.py`). HACS wants one integration under `custom_components/` of a
   repository and accepts other code beside it. The cost: the root of the repository carries HACS files, and the API's
   README is what HACS shows.
2. **Its tests and CI are separate.** A recent Home Assistant needs a newer Python (2026.10.2: 3.14.2) than the API's jobs
   (3.12 and 3.13), and its test plugin does not run on Windows. So `tests_ha/` has its own requirements and
   `home-assistant.yml` its own job, with hassfest and the HACS validation; `ci.yml` is not touched, and `pytest` of the
   API never collects `tests_ha/`.
3. **Poll, with the push as a hint.** A `DataUpdateCoordinator` reads `/v1/vigilances` at the interval of the entry
   (5 minutes, 1 to 60). When `/v1/health` says the instance runs its scheduler, the integration also follows the place on
   `/v1/stream`, and a `vigilance_push` for that place makes it **read again at once**. The pushed message is not used as
   data: one place computes the states and the events, with the user's river radius. An instance without a scheduler never
   pushes (and a connection would make it build its whole snapshot), so it is polled only. The poll never stops.
4. **Unknown is not none.** The level is unknown when no source covers the place, when every covering source is
   unavailable, when a warning cannot be graded, and when sources list warnings that cannot be placed on a map. `none` means
   a source answered, nothing was found and nothing is hidden. An unreachable instance makes the entities unavailable, a
   third state. This is [ADR 0001](0001-two-repos-no-fabricated-data-frame-format.md) and
   [ADR 0002](0002-official-warnings-kept-intact-and-localized-offline.md), applied to a client.
5. **Phenomena are an attribute, not twelve sensors.** A place sees two or three of the twelve groups at a time. One
   `by_phenomenon` attribute keeps the entity list short and is read by a template.
6. **The event compares reads, by a key that is not the identifier.** A warning is recognised by source, country,
   phenomenon, the source's own word for it and its area codes, because MeteoAlarm re-issues a warning under a new
   identifier when its level changes. The key is the API's own for its pushes, plus the source's word (so that the end of
   a red "rain-flood" does not hide behind a yellow "flooding"). The first read is a baseline, a source that is down ends
   nothing, and a source that answers for the first time sets its own baseline: the rules of `poll_changes`.
7. **A fake instance for trying it.** `POST /v1/simulate` injects hazards, not warnings, so it cannot make
   `/v1/vigilances` say anything. `tools/fake_instance.py` serves the two routes with warnings chosen by hand, all
   labelled `SIMULATION`, without touching a provider or the real API.
8. **Bounded attributes.** At most 20 warnings and 12,000 bytes of attributes, under the 16 KiB of the recorder, with
   `omitted` saying how many were left out; the long texts stay in the API.

## Consequences

- The integration is installed by copying a folder today (HACS adds public repositories only); HACS becomes possible when
  the repository is public, with a release (HACS takes the latest GitHub release as the version).
- The HACS validation action refuses a private repository, so its job is skipped until the repository is public, and
  not verified until then. Its `brands` and `topics` checks cannot pass yet (no brand image, no topics) and are ignored.
- In France the API drops a MeteoAlarm "flooding" warning that a river section says at the same level or higher, so a
  client that compares reads can see an `ended` for it next to a `new` for the section. The API's own push avoids it
  because it knows which warning it dropped; the client does not.
- Not yet checked: a run on a real Home Assistant installation (the tests run against 2026.10.2 in CI), and a long run
  against an instance that does push.
