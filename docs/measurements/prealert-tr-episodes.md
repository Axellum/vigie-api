# Official orange warnings of Turkey, from the press (2024 to October 2026)

`prealert-tr-episodes.csv` is the list the unofficial pre-alert pilot for Turkey is scored on
([ADR 0011](../decisions/0011-unofficial-pre-alert-turkey-shadow-pilot.md)). It is data for a measure, not part of the application.

## What it holds

One line per **episode**: an orange warning of the Turkish State Meteorological Service (MGM) as a newspaper reported it, with the provinces
the article names **for that colour**.

| Column | Meaning |
|---|---|
| `id` | E01, E02... in time order |
| `start`, `end` | the local days (Europe/Istanbul) the warning was for, read from the article ("bugün", "yarın", a weekday); not the time the MGM issued it, which no article gives |
| `level` | `orange` (no red warning between 2024-01-01 and 2026-10-24 was found with a date and provinces that could be checked) |
| `phenomena` | ours: `rain` (kuvvetli yağış, sağanak, gök gürültülü sağanak), `wind` (fırtına, lodos), `snow` (kar); several when the article names several. A pair is a hit when the pre-alert has level 2 or more for any of them |
| `provinces` | ISO 3166-2 codes (TR-01 to TR-81), the same order as `province_names` |
| `province_names` | the Turkish names, as Wikidata labels them (CC0) |
| `source` | the article the line was read in |
| `flag` | `disputed` (another article of the same day disagrees, or the article contradicts itself: see `note`), `revision_of:<id>` (probably a new issue of the same warning: its pairs are not counted again) |
| `note` | ours, in English |

Only facts are kept: the dates, the colour, the phenomenon and the provinces. No sentence of the MGM or of the press is copied.

## How it was made (2026-10-25)

- A web research read each article in full (the date on the page, not the date a search engine gave), kept only those that name **both**
  the colour and the provinces of that colour, and left out lists that mix yellow and orange. A warning reported by several articles is
  one line.
- Two lines were checked again by hand on 2026-10-25 against their article (E29, E35): the provinces and the day match.
- The province names were turned into codes with the 81 provinces of Wikidata (instances of "province of Turkey" with an ISO 3166-2 code).

## What it is, in numbers

| Set | Episodes | Pairs (episode, province) |
|---|---|---|
| Learning (2024) | 13 | 72 |
| Learning (2025) | 14 | 50 |
| Test (2026, up to 2026-10-24) | 8 (E36 is a revision of E35) | 49 |

Rain is 25 episodes, snow 9 (with the mixed ones), wind 1, heat and cold 0, red 0.

## Limits

- **It is not the MGM's archive.** It holds what the press chose to report: the warnings of big cities and of floods are more likely to be
  in it. A warning the press did not report is missing, so this list cannot measure false alarms (ADR 0011 bounds them by other tests).
- **Dates are days, not hours.** A warning "until tomorrow noon" counts for both days.
- **Wind, heat, cold and red** have too few pairs to be judged on their own (ADR 0011: under 10 pairs a phenomenon is reported, not judged);
  the shadow phase adds the episodes of its weeks.
- E05 and E26 are `disputed`; they stay in, and the scoring reports the result with and without them.
