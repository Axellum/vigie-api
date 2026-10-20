# Recorded MetService answers

Real answers of `alerts.metservice.com`, recorded on **2026-10-20 at 11:55 UTC** (21 October, 00:55, in New Zealand) and
played back by the tests (no test reaches the network).

| File | From | Kept |
| --- | --- | --- |
| `rss_empty.xml` | `https://alerts.metservice.com/cap/rss` | all of it: a channel with its title, its link, its description, its `<copyright>` ("Copyright Meteorological Service of New Zealand Limited, 2026. Licensed under Creative Commons BY 4.0.") and no item |
| `atom_empty.xml` | `https://alerts.metservice.com/cap/atom` | all of it: the same, as Atom |
| `rss.headers.txt`, `atom.headers.txt` | the response headers | `Cache-Control: max-age=300, must-revalidate` and a `Last-Modified` (`Tue, 20 Oct 2026 11:55:21 GMT`) that answers `If-Modified-Since` with a `304` |

**No warning was in force in New Zealand that day: the feed had no item** (the warnings page of metservice.com listed none
either). No real item and no real CAP document has been seen. Nothing here is a MetService warning. The format is
documented by the issuer, in the *MetService CAP Feed User Guide*, version 1.2 of 20 April 2026
(<https://www.metservice.com/files/documents/cap/MetService-CAP-Feed-User-Guide-v1.2.pdf>, read on 2026-10-20), and
`tests/test_metservice.py` builds a feed and CAP documents of its own in the structure of the guide's Appendices A and B
(the area, the identifiers and the texts are invented), and labels them **synthetic** in the file and in each test. The
weekly check (`tools/check_live_feeds.py`) fails the day a real item cannot be read, or is not as the guide says.

Licence of the content: CC BY 4.0, as the feed states. See `SOURCES.md`.
