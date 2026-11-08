# 0016. Verdicts for surf, kite, boat and shore fishing: rules of ours, written in the answer, not a safety service

- Status: accepted as a **first version**. Unlike [ADR 0015](0015-tides-from-a-global-model.md) this one has no measure to pass: a verdict
  is a rule, and the only check that means something is a person looking at the sea. The thresholds below are judgments, not findings; they
  are meant to be adjusted from real use, and an adjustment changes `RULES` in the code, this record and the tests together.
- Date: 2026-11-08

## Context

The owner wants a wall screen (a 7 inch display running Vigie's firmware) whose first thing to say, for a surf spot, is whether to go: surf,
kite or wing or windsurf, boat, shore fishing ("pêche à pied"), in one glance, with the reason in one line and the hours in which to go.

Vigie already holds most of the figures: the swell and the wind hour by hour (`GET /v1/surf/spots/{id}`), the tide computed from EOT20 with
the high and low waters and an **estimated** coefficient ([ADR 0015](0015-tides-from-a-global-model.md)), the official coastal warnings
(`GET /v1/vigilances`, phenomenon `coastal`; in France the "vagues-submersion" vigilance), and a rating of ours for the surf
(`app/services/surf.py`). What it does not have is a **verdict**: a thing to say "go" or "do not go" for an activity.

No official source gives thresholds for these activities. What is official, and used here:

- **The Beaufort scale**, in knots, from the tables of ECCC and the Met Office (the lower bound of each force: 1, 4, 7, 11, 17, 22, 28, 34,
  41, 48, 56, 64).
- **The colours of an official warning**: a red coastal warning is the highest level of the issuer; the API already maps each issuer's own
  wording to `YELLOW`, `ORANGE`, `RED` (`docs/data-sources.md`).
- **The SNSM's advice on the foreshore**: a coefficient of 100 or more is a danger for the people who go on it, and one is back up by low
  water at the latest.

Everything else (the share of points that makes a good surf, the wind that suits a kite, the sea in which a small boat goes out) has no
official number. A federation or a school has its own, and they differ with the level of the person.

## Decision

1. **Three levels**, `good`, `possible`, `poor`, or `null` when too little is known. Not four: a fourth level ("average") has no rule that
   tells it from `possible`.
2. **Every threshold is ours, in the answer** (`rules`), nothing is hidden in the client, and the answer says in `limits` that the verdicts
   know neither the spot nor the person, and in `disclaimer` that this is not for safety.
3. **Hour by hour**, the verdict of an hour is the **lowest** of its criteria, and it says which criterion (`reason`:
   `<criterion>.<state>`, a code the client translates). **An unknown value counts for nothing and is not guessed**, except one an activity
   cannot be judged without: the wind for the kite and the boat, the low water and the wind for the shore fishing, which make the verdict
   `null` (a sea of 0.6 m says nothing of a day on the water if the wind is not known).
4. **The headline** answers "can I go today?": the best verdict among the **daylight** hours of the next 24 hours, with the **longest run of
   consecutive daylight hours** at that verdict as `best_slot` (the first on a tie), and no slot when the best is `poor`. The `detail` is the
   criteria of the first hour of the slot, else of the first daylight hour. Hours are whole hours.
5. **Surf**: the rating of the surf page, by its share of points, 75 % and over `good`, 50 % and over `possible`, less `poor`; `null` with
   fewer than three criteria scored (the rule of the best slot there).
6. **Kite, wing, windsurf**: the wind in knots against **a range the person sets** (default 12 to 25; within 3 knots of it, `possible`);
   the gust gap (mean to gusts, 5 knots or less `good`, 6 to 8 `possible`, more `poor`); a wind from the land side (offshore) is `possible` at
   best, because it carries a rider out to sea. The range is a query parameter: the owner's default is not everyone's.
7. **Boat**: the Beaufort force of the mean wind (4 or less `good`, 5 `possible`, 6 and over `poor`), of the gusts (5 or less, 6 to 7, 8
   and over), and the significant wave height (under 1.25 m, under 2.5 m, else `poor`).
8. **Shore fishing**: judged **only in the two hours before a low water** (at the middle of the hour; the window ends at the low water,
   not after it, since the SNSM says to be back up by then; the two hours are ours), and `poor` outside. Inside: the estimated coefficient of
   the Brest high water nearest in time to that low water (under 50 `poor`, 50 to 69 `possible`, 70 to 99 `good`, **100 and over
   `possible`**, with the SNSM's warning as the reason, not `good`), the wind force (5 or less `good`, 6 `possible`, 7 and over `poor`: one
   force more tolerant than the boat's, a person on foot not being out at sea), and the sea height as above. The coefficient is **never
   presented as SHOM's**.
9. **Warnings cap the verdicts**, on the phenomenon `coastal` only: **red makes every verdict `poor`** (reason `vigilance.red`), **orange keeps
   the boat and the shore fishing at `possible` at best** (`vigilance.orange`), yellow changes nothing. A warning covers an hour if it
   overlaps it. **When the warnings could not be read** (no source answered for the spot, or the call failed), nothing is capped and the answer
   says `vigilance.status: "unknown"`: an empty list is not read as "nothing to fear".
10. **No request is added to a provider.** The swell and the wind are the two answers the surf page reads (kept 3 hours and 1 hour); the
    sunrise and the sunset come from the wind answer; the tide is computed; the warnings are the cached ones of `GET /v1/vigilances`.

## What this does not do

- **Water temperature** is not read today: the collector asks MFWAM for the swell, and a sea surface temperature would be another request
  (not checked here which model gives it). A wetsuit is not advised.
- **The Mediterranean** has no shore fishing by the tide: the coefficient says nothing there. The 51 surf spots are on the Atlantic and the
  Channel, so this does not arise yet; a spot outside them must not get this verdict without a rule for it.
- **Nothing is personal** but the kite's range. A beginner and an expert do not share a surf rating; that is the surf page's limit too.
- **It was not measured.** There is no list of days on which a verdict was right. The check is the owner at the spot, on a day the verdict is
  `good` and on a day it is `poor`, and what he finds goes into the thresholds.

## Consequences

- `GET /v1/activities/{spot_id}?days=&wind_min_kn=&wind_max_kn=` (1 to 3 days, default 2), `Cache-Control: public, max-age=300`;
  `app/services/activities.py`, `app/models/activities.py`, `tests/test_activities.py`.
- `RULES` in `app/services/activities.py` is the text a client shows; the thresholds are constants beside it, and a test checks that the
  text and the main constants say the same.
- The embedded frame and the screen use these verdicts next (`vigie-display`); the frame gets new line tags, and a decoder that does not know
  a tag ignores it ([`docs/embedded-frame.md`](../embedded-frame.md)). Nothing of the existing frame changes.
