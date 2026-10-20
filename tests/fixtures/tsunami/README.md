# Recorded tsunami messages

Real answers of the U.S. Tsunami Warning Centers (`https://www.tsunami.gov`), recorded on **2026-10-20** and played back by
the tests (no test reaches the network). Nothing in these files was edited. tsunami.gov compresses what it sends (the list is
16,140 bytes, 1,721 on the wire); the files here are the decompressed documents.

| File | From | What it is |
| --- | --- | --- |
| `previous.js` | `/events/js/previous.js` | The list of the 40 latest bulletins of the two centres, 2026-09-28 to 2026-10-20 10:45 UTC: 36 information statements and the 4 threat messages of the Panama earthquake. `Last-Modified: Tue, 20 Oct 2026 10:45:26 GMT` (the newest bulletin was created at 10:45:23); a request with `If-Modified-Since` of that date is answered `304` with 239 bytes of head. `tests/storm_samples.py` replays it as it grew (an entry is in the list once its `createdDB` time has passed) |
| `PHEB_26282000_<n>_WEPA40_CAP.xml`, `n` = 1 to 4 | `/events/PHEB/2026/10/09/26282000/<n>/WEPA40/PHEBCAP.xml` | The CAP message of the 4 threat messages (product `WEPA40`) of the **Panama earthquake of 2026-10-09** (magnitude 7.6, 17:56:08 UTC, 7.54 N 80.75 W) by the PTWC: 18:02 *Tsunami Potential Threat*, 18:32 *Tsunami 1-3 meters* and *0.3-1 meters*, 18:51 *1-3 meters*, 19:40 the **final** message, a CAP with no `info` |
| `PHEB_26282000_<n>_WEPA40.txt`, `n` = 1 to 4 (the 4th has no table) | `/events/PHEB/2026/10/09/26282000/<n>/WEPA40/WEPA40.txt` | The text bulletin of the same messages, with the table of arrival times (25 places in the first, 4 in the third) |
| `PHEB_26158002_5_WEPA40_CAP.xml`, `PHEB_26158002_5_WEPA40.txt` | `/events/PHEB/2026/06/07/26158002/5/WEPA40/` | The 5th threat message (sent 2026-06-08 02:48 UTC) of the **Mindanao earthquake of 2026-06-07** (23:37 UTC, magnitude 7.8): the one message seen with a class over 3 metres (*Tsunami >3.0 meters* for the Philippines, *0.3-1 meters* for Malaysia). Still on the server on 2026-10-20 |
| `PAAQ_mcl1uw_<n>_WEPA41_CAP.xml`, `n` = 1, 6, 9, 13 | `/events/PAAQ/2012/10/28/mcl1uw/<n>/WEPA41/PAAQCAP.xml` | The **domestic** messages (product `WEPA41`, UGC zone codes and VTEC codes) of the NTWC for the **Haida Gwaii earthquake of 2012-10-28** (magnitude 7.7), which the CAP page of tsunami.gov keeps as a sample: a warning, then a warning with two advisories, then advisories with the warning cancelled, then the cancellation. The only domestic messages of a real event found; they are 14 years old |
| `PHEBCAP_information_vanuatu.xml`, `PAAQCAP_information_alaska.xml` | `/events/xml/PHEBCAP.xml`, `/events/xml/PAAQCAP.xml` | The per-centre "latest" files on 2026-10-20: an information statement each. The collector does not read them (the latest bulletin of any product would hide a threat behind an information statement); they stand for the CAP of an information statement |

Not recorded: a domestic message of 2026 (the list of 2026-10-20 holds none), the CAP of a Spanish or a test message, the
signature check. The message identifiers (`PHEB-3-26282000`), the times and the texts are the centres' own.

Public domain by the NWS disclaimer (see `SOURCES.md`).
