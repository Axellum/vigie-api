# Recorded ECDC feeds

The three RSS feeds of the European Centre for Disease Prevention and Control read by `app/collectors/health.py`, recorded on
**2026-10-28 at 22:03:34 UTC** and played back by the tests (no test reaches the network). Each `<name>.xml.gz` is the body **as the
server sent it**, gzipped here only to keep the repository small; `<name>.headers.txt` has the address, the time of the recording
and the headers that matter (validators, cache lifetime). A request with the recorded `ETag` was answered `304` the same minute.

| File | From | What it holds |
| --- | --- | --- |
| `ecdc_epidemiological_updates` | `https://www.ecdc.europa.eu/en/taxonomy/term/1310/feed` | The ten latest epidemiological updates (an Ebola outbreak in the Democratic Republic of the Congo, worldwide overviews of chikungunya, MERS-CoV, cholera, dengue and mpox...), from 18 March to 19 October 2026 |
| `ecdc_risk_assessments` | `https://www.ecdc.europa.eu/en/taxonomy/term/1295/feed` | The ten latest threat and risk assessments, the first about Ebola cases imported to Kenya and Nigeria (23 October 2026) |
| `ecdc_threats_reports` | `https://www.ecdc.europa.eu/en/taxonomy/term/1505/feed` | The ten latest weekly Communicable disease threats reports (weeks 33 to 42) |

No item has a `category`: the feeds name no country. Licence of the content: CC BY 4.0 (`SOURCES.md`, "Health threats for travellers").
