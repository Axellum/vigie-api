# Recorded answers of the services of the WMO registry

Real answers, recorded on **2026-10-20 at about 17:00 UTC** by one read-only GET of each address the registry of the WMO Severe
Weather Information Centre (<https://severeweather.wmo.int/v2/json/sources.json>) lists for the services that publish on a
national host. Played back by `tests/swic_samples.py` (`FixtureServer`); no test reaches the network.

## What is kept

| Files | From | Kept |
| --- | --- | --- |
| `cap_*.xml` | the CAP documents the feeds below list | the whole document, unchanged (see "Changed" below) |
| `index_*.xml` | the RSS or Atom feed of a service | the head of the feed as sent and **only the entries that match a kept document** (2 or 3, one for Thailand, Kuwait and Norway); the number kept and the number the real feed listed are in `manifest.json` |
| `index_jm_no_active.xml` | Jamaica's feed | all of it: a feed that says "There are no active watches, warnings or advisories" in one entry that links to the feed itself |
| `mirror_rss.xml` | the WMO's own copy of a feed (`severeweather.wmo.int/v2/cap-alerts/<id>/rss.xml`) | the first two items of the one copy of 260 asked that answered (China's); the other 259 answered HTTP 404 |
| `manifest.json` | the addresses | for each index its address, validators and weight on the wire; for each document the address it was read from |
| `profile.json` | all the feeds the registry lists | **numbers only**: what each feed weighed on the wire, whether the server gave a validator, the status it answered, how many entries it listed and how many of them the API would read. It is what `tests/swic_upstream.py` plays back to count requests and bytes (`tests/test_swic_sobriety.py`); the text of the simulated feeds and documents is **synthetic** |

## The families of format

At least one document for each way of writing CAP that was seen on 2026-10-20 (`tests/test_swic_cap.py` checks that each is there):

- two languages in one document, a colour in a parameter, an `Update` that names its predecessor: Spain (`cap_es_aemet_*`)
- severity only, one language, a polygon: Mexico, Argentina (`cap_mx_smn_*`, `cap_ar_smn_*`)
- French and English in one document: Algeria; Arabic and English: Kuwait; Norwegian and English with four polygons: Norway
- codes only, no shape, a `Minor` severity: the Philippines (SAME codes), India (LGD district codes, two languages)
- one document per language, an identifier each (`.EN` / `.RU`, `-EN` / `-RU`): Russia (`cap_ru_en_*`, `cap_ru_ru_*`), Kazakhstan (`cap_kz_*`), Belarus (`cap_by_*`, here `Cancel`s with no `info`)
- status `Test`: Colombia; an area with 67 polygons and ISO 3166-2 codes: Thailand; two warnings in one document: Uruguay; a `Cancel` and an `Alert` with no language: Trinidad and Tobago

No recorded document had a `circle`: the circles are tested with documents written in the tests, labelled synthetic.

## Changed

Nothing in a document. In `index_uy_inumet.xml` the name and the e-mail address of the person named as author of two entries were
replaced by `redacted@example.org (redacted)`; the other e-mail addresses are the role addresses the services publish in their
CAP documents (`sender`, `contact`).

## Licences

The content of each file is the issuer's, under the terms recorded in `SOURCES.md` (one row per service). The files are kept here to
test a reader, not to redistribute warnings.
