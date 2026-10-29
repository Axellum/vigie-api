# Recorded travel advice answers

The real answers of the five ministries, recorded on **2026-10-28 between 20:48:28 and 20:48:33 UTC** and played back by the
tests (no test reaches the network). Each `<name>.json.gz` is the body **as the server sent it**, gzipped here only to keep the
repository small (the server's own compression is undone, nothing else is changed); `<name>.headers.txt` has the address, the time
of the recording and the headers that matter (validators, cache lifetime).

| File | From | What it holds |
| --- | --- | --- |
| `us_travel_advisories` | `https://cadataapi.state.gov/api/TravelAdvisories` | 218 advices (490,774 bytes): 216 placed, "French West Indies" not placed, and two pages for Tonga (`destination.ton` and `tonga-travel-advisory`). `Category` is FIPS (`IZ`, `UK`, `A1`). No validator, `Cache-Control: no-cache` |
| `ca_index_updated` | `https://data.international.gc.ca/travel-voyage/index-updated.json` | 230 destinations, levels in English and French, 64 with regional advisories; `PT-20` (Azores) and `IC` (Canary Islands). `ETag` and `Last-Modified` (a conditional request was answered `304` the same evening) |
| `ca_cta_cap_mx` | `https://data.international.gc.ca/travel-voyage/cta-cap-mx.json` | The page of Mexico: `advisories` (the national and regional risk levels) in English and French, the other sections and the offices |
| `au_destinations_export` | `https://www.smartraveller.gov.au/destinations-export` | 181 items: 179 destinations, "Crisis alert" and "No travel advice" (not destinations). Strings HTML-escaped (`Côte d&#039;Ivoire`); dates without a time zone. `ETag`, `Last-Modified`, `Cache-Control: max-age` |
| `nl_traveladvice_offset0`, `nl_traveladvice_offset200` | `https://opendata.nederlandwereldwijd.nl/v2/sources/nederlandwereldwijd/infotypes/traveladvice?output=json&rows=200&offset=0` and `offset=200` | 200 and 26 destinations (ISO alpha-3, `XKX`, `BQ-BO`, `BQ-SA`, `BQ-SE`), each with its summary "In het kort"; `classification` says "Dit veld wordt niet meer ondersteund" |
| `gb_index` | `https://www.gov.uk/api/content/foreign-travel-advice` | The FCDO index: 226 pages with the date of their latest version (`ETag`, `Cache-Control: max-age=10`) |
| `gb_france`, `gb_mexico`, `gb_ukraine`, `gb_turkey`, `gb_russia`, `gb_cuba`, `gb_cook_islands` | `https://www.gov.uk/api/content/foreign-travel-advice/<page>` (`cook-islands-tokelau-and-niue` for the last) | One page for each case of the alert statuses seen on the 226 pages that day: none (France), all but essential travel to parts (Mexico), both statuses for parts (Ukraine), all travel to parts (Turkey), all travel to the whole country (Russia), all but essential travel to the whole country (Cuba), and a page for three territories (Cook Islands, Tokelau and Niue) |

Licences of the content: see the section "Travel advice" of `SOURCES.md` (OGL v3.0, public domain, OGL – Canada, CC BY, CC0).
