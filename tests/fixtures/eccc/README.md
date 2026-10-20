# Recorded ECCC answer

The real answer of MSC GeoMet, recorded on **2026-10-20 at 11:56 UTC** and played back by the tests (no test reaches the
network).

| File | From | Kept |
| --- | --- | --- |
| `weather_alerts_items.json` | `https://api.weather.gc.ca/collections/weather-alerts/items?f=json&limit=1000` | all of it, as the server sent it: 36 alerts in force, one per forecast area, every one **yellow** (23 frost advisories in Ontario and Quebec, 8 wind warnings in the Yukon and British Columbia, 3 snowfall warnings and 1 storm surge warning in the Northwest Territories, 1 blizzard warning in the Yukon) |
| `weather_alerts_items.headers.txt` | the response headers | `Content-Length: 156093`; no `ETag`, no `Last-Modified`, no `Cache-Control` |
| `ended_item.json` | the same URL, asked again at **13:12 UTC** | one item of the 36, the only one with `status_en` `ended` (the frost advisory of Chatham-Kent - Rondeau Park, the same identifier as in the first answer, re-published at 13:06 with `expiration_datetime` an hour later). **Extracted and re-serialized** (the whole answer is not kept again; its values are as sent). The official page of the place (`meteo.gc.ca`, Rondeau Provincial Park) said "Aucune alerte en vigueur" at 13:10 UTC |

Statuses seen: `issued`, `continued` and (13:12 UTC) `ended`. Alert types seen: `warning` and `advisory`. **Not seen:** an
orange or red alert, a statement, a watch, an alert whose `alert_type` is `ended` (the collector reads that property too,
in case). `tests/test_eccc.py` reaches them with **synthetic** items (a copy of a recorded one with a word changed), and
says so.

Checked on the same day against the CAP file of the Datamart for one alert (frost advisory, Chatham-Kent - Rondeau Park):
`validity_datetime` is the CAP `onset`, `expiration_datetime` the CAP `expires`, `event_end_datetime` the CAP
`Event_End_Time`, and `status_en` `continued` goes with a `msgType` Update.

Licence of the content: ECCC Data Services End-use Licence 2.1.1. See `SOURCES.md`.
