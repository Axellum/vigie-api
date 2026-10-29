# The archive of the official warnings over a real day: what it takes in, and what it weighs

*A measure made from 2026-10-28 20:53 UTC to 2026-10-29 21:03 UTC (24.2 hours), with the code of
Axellum/vigie-api#31 (the archive) and an empty database. The driver is [`tools/measure_archive.py`](../../tools/measure_archive.py)
(`--every 600`): every 10 minutes it asked `GET /v1/vigilances?country=XX` for each of the 49 countries the instance serves and for
the tsunami messages, then read `GET /v1/vigilances/archive/stats`. Its 146 rounds are in
[`vigilance-archive-2026-10-rounds.jsonl.gz`](vigilance-archive-2026-10-rounds.jsonl.gz) (one line of JSON per round); the other
figures were read in the database and in `GET /v1/providers` at the end. The instance ran with its scheduler on (`VIGIE_SCHEDULER=1`),
NASA FIRMS and RainViewer off.*

## 1. Conclusion

- **About 1,300 new warnings and 4.8 MB a day** on disk, for every country the instance serves read every 10 minutes. At that rate a
  year is about 1.8 GB.
- **No answer failed**: 7,154 answers, all `200`. A round took 17.9 s (median), 75 s for the first (cold caches).
- **Every hour of the day was read** for each of the 52 pairs of source and country (`reads`): a period without a warning in this
  archive is a period when the source listed none.
- **The issuer's words are there for every warning that was in force while the API looked**, except 3. The 451 MeteoAlarm warnings
  without their words had all ended before the first round saw them (a feed still lists a warning for a while after its end), and the
  archive asks for no document of its own (section 4).
- The measure found a fault, fixed in the same change: a pass that did not hold a warning's CAP document any more (it had left the
  cache, or the instance had restarted) rewrote the archived warning without its words. It now keeps them (section 4).

This is one day at the end of October. Storms, heat waves and the American spring can give several times more warnings; the figures
are to be read again on a real instance with `GET /v1/vigilances/archive/stats`.

## 2. What came in

| | Count |
|---|---|
| Warnings archived (one per message and area) | **2,232** |
| ... already listed at the first round (the stock) | 924 |
| ... new during the 24 hours | **1,308** |
| Distinct messages (CAP identifiers) | 1,257 |
| Countries with at least one warning | 37 of 49 |

By source: MeteoAlarm 1,021, DWD 550, the services of the WMO registry 374, NWS 187, ECCC 73, MetService 27 (no Vigicrues or tsunami
warning was in the feeds). By level: yellow 1,886, orange 281, red 23, no level 42 (the source's word is in no table, for example
"special weather statement" in Canada).

`GET /v1/vigilances/archive/stats`, per UTC day: 1,131 warnings first seen on the 28th (from 20:53, with the stock), 1,102 on the
29th (until 21:03).

## 3. What it weighs

| | Bytes |
|---|---|
| Database at the end of the first round | 2,070,456 |
| Database at the end (`database_bytes`, WAL included) | 6,918,200 |
| ... of which the database file / the WAL file, read on disk after the last round | 6,914,048 / 61,832 |
| **Growth over 24 hours** | **4,847,744** |
| Warnings as served (2,232 payloads, zlib) | 2,977,258, 1,333 a warning on average (7,637,446 as JSON: 2.6 times less) |
| Shapes of the areas (640, each kept once; 1,546 warnings have one) | 916,245 |

The rest is the table of the rows, its indexes and `reads` (104 rows). One warning costs about 3.1 KB in all.

## 4. The issuer's words

The national services (DWD, ECCC, NWS, MetService, the WMO services) put their words in each warning: every one of their 1,211
warnings has them. A MeteoAlarm warning has them only when the instance read its CAP document, which it does for a warning an answer
returns (at most 40 new documents per request); 567 of 1,021 have them.

| MeteoAlarm warnings without their words | |
|---|---|
| Already ended when first seen, all at the first round | 451 |
| In force or to come while seen | 3 |

After the first round, no warning that ended before it was seen came in: with every country read every 10 minutes, a warning is seen
while in force, so its document is read. The words of a warning that ended before anyone asked for its country are lost to this
archive; reading those documents would mean requests that no answer needs (for the 451 warnings of the first round, then almost
none), which the archive does not send today.

**The fault.** The archive rewrote a warning when anything it compares had changed, and "the document is held" was one of those
things. A document leaves the cache when no cached feed links to it any more, and a restarted instance holds none: the next pass
(the scheduler's, for example) then wrote the warning again, without its words, until an answer read the document again. The archive
now never replaces a warning that has its words by one that has not (`tests/test_vigilance_archive.py`,
`test_the_words_once_archived_are_never_lost`); the last sighting still moves.

## 5. What reading every country every 10 minutes cost the providers

Counted by the instance over its last 24 hours (`GET /v1/providers`, `calls_24h`). The scheduler was on: it keeps fresh the 8 countries
asked in the last hour (`RECENT_COUNTRIES_MAX`, `RECENT_COUNTRIES_TTL_S`) every 90 seconds, so part of these requests are its own.

| Provider | Requests in 24 h | Note |
|---|---|---|
| MeteoAlarm Atom feeds | 8,114 | one feed per country, cached 180 s |
| MeteoAlarm CAP documents | 644 | each document once (cached 6 h) |
| Services of the WMO registry | 1,305 | quota of the API: 50,000 a day |
| Tsunami warning centres | 927 | |
| NWS | 490 | also read for the hazards |
| MetService | 168 | |
| DWD | 144 | |
| ECCC | 144 | |
| Vigicrues | 96 | |

No request was refused by a quota (`budget_refusals` 0 everywhere). The other providers of `GET /v1/providers` (earthquakes,
GDACS, volcanoes, weather) are the hazards and the forecast of the scheduler, not the archive.

This is what a public instance would send if it kept the world view of `GET /v1/events` fresh on its own (the open point of
[ADR 0012](../decisions/0012-events-and-impact.md)): about 9,000 requests a day to MeteoAlarm. Today an instance reads a country only
when it is needed or asked for, and its archive holds only those countries.

## 6. The world view of `GET /v1/events`, asked on demand

Measured on 2026-10-29 at about 21:10 UTC on a fresh instance of the same code (empty database, scheduler off): `GET /v1/events` without
a country three times, 20 seconds apart, then `?compact=true&min_impact=60`. Memory is the working set of the server process.

| | Time | Answer | Memory after | Provider requests |
|---|---|---|---|---|
| Fresh instance | | | 116 MiB | |
| World view, cold caches | 29.8 s | 2.07 MB, 94 events | 146 MiB | 274: 37 MeteoAlarm feeds, 40 CAP documents, 180 WMO, 17 others |
| World view, warm (twice) | 6.3 s, 7.1 s | 2.1 to 2.2 MB | 156 MiB | 75 more CAP documents (40 new ones at most per request), 3 others |
| `compact=true&min_impact=60`, warm | 0.5 s | 45 KB | | |

On 2026-10-28 the same cold view took 66 s (MeteoAlarm answered in 15 s on average then). The feeds are cached 180 s: a world view
asked more than 3 minutes after the last one is cold again for MeteoAlarm. Keeping it fresh means reading every country at that rhythm
(the cost of section 5 for every 10 minutes) and keeping the computed answer, which the API does not do today.

## 7. How long to keep it (a proposal; the owner decides)

- **Keep everything** (`VIGIE_ARCHIVE_KEEP_DAYS=0`, the default) for at least a year: the archive is worth most when a season can be
  compared with the same season a year before, and a year is about 1.8 GB at this day's rate.
- Read `GET /v1/vigilances/archive/stats` once a month. If the database passes 5 GB, set `VIGIE_ARCHIVE_KEEP_DAYS=730` (two years).
- If the disk is wanted for something else: `VIGIE_ARCHIVE_KEEP_DAYS=365` keeps one year, about 1.8 GB at this day's rate.
