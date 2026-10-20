# 0003. National services: the national source answers for its country, a river is a line

- Status: accepted (three points are questions for the owner: see "Open points")
- Date: 2026-10-20

## Context

MeteoAlarm relays the warnings of 39 European countries, the NWS those of the United States
([ADR 0002](0002-official-warnings-kept-intact-and-localized-offline.md)). Four national sources were added: the
Deutscher Wetterdienst (Germany), Environment and Climate Change Canada, MetService (New Zealand) and Vigicrues (the
river flood vigilance of France). Two questions follow. When a country has its own service **and** a MeteoAlarm feed
(Germany), the same warning would be in the answer twice. And a river flood vigilance is not an area but a section of
river, a line.

## Decision

1. **The national source is asked for its country, and MeteoAlarm's copy of it is not.** Germany: the DWD. The DWD gives
   the same warnings with the polygon of every district and the texts in eight languages; MeteoAlarm's German feed has
   `EMMA_ID` codes only, which no bundled shape can match. If the DWD gives no answer, MeteoAlarm stands in for Germany
   and the answer says so; its copies are not tracked for the pushes (they would be the same warnings under another
   identifier). Countries with no national collector keep MeteoAlarm; Canada and New Zealand have no MeteoAlarm feed.
2. **France has two sources, because they say two things:** MeteoAlarm a colour per département, Vigicrues a colour per
   section of river. They overlap on one thing, the flood. The rule: a French MeteoAlarm "Flooding" warning is left out of
   an answer when a Vigicrues section of that same answer crosses its département at the same level or higher; otherwise
   it stays (no section in reach, a lower one, Vigicrues not answering). "Rain-flood" is another vigilance and always
   stays. The rule is applied to the pushes too, and a warning that a section now says better is not "ended".
3. **A river section is a line.** A position is matched when it lies within `river_radius_km` of it: 5 km by default, 50 at
   most. The distance is returned (`distance_km`), so a client can filter. The geometry is the flow's own (simplified).
4. **A source is read the way its own rules allow, and no more often than they ask.** ECCC is read through MSC GeoMet, not
   through the Datamart CAP files: the usage policy forbids requesting directory listings to find new data and asks for
   the AMQPS notification service for systematic retrieval. The DWD and MetService are asked conditionally (`ETag` /
   `Last-Modified`, a `304` costs a few hundred bytes). Vigicrues offers no validator, no compression and no lighter
   endpoint, and its flow weighs 2.2 MB: the collector asks (`HEAD`) the national map image that the same publication
   regenerates, every 15 minutes (5 while a section is orange or red), and reads the flow only when the image changed (at
   the latest after 12 hours). The image is an inference from the site, not a contract: a weekly check watches it, and
   without a usable image the flow is read every hour (20 minutes in a flood).
5. **Nothing is guessed from a format not yet seen.** MetService had no warning on the day it was read, so no real item
   has been read; but its user guide (v1.2, April 2026) documents the feed, and the reading follows the guide (the
   `ColourCode` parameter for the level, the `<event>` word for the phenomenon). An item that cannot be read is counted
   and said, and the weekly live check fails the day it meets one that is not as the guide says. The words that give a
   level or a phenomenon are in tables; a word that is in none, or a colour the headline contradicts, gives `null`.
6. **A source that did not answer is not credited,** and an outage of one source of a followed point does not mute the
   others (what it had said of the point stands, it ends nothing).

## Consequences

- Germany's warnings can now be matched to a position (before: listed by country only).
- Canada and New Zealand are added to the bundled country borders: the file goes from 196 to 335 KiB (Canada's coast).
- A French position costs one more source (Vigicrues: 3 to 4 reads of the 2.2 MB flow a day, and 88 to 240 looks of 330 bytes: 7 to 9 MB a day).
- The same flood can still be said by MeteoAlarm's département and a section of a lower colour: both are kept on purpose.

## Open points (for the owner)

- **ECCC through the Datamart.** An AMQPS client (a dependency and an open connection) would give the full CAP messages
  (sender, instruction, message type) and follow the usage policy to the letter; GeoMet is used meanwhile.
- **Canada's border.** 131 KiB of the 335; a tolerance of 0.03 degree would make it 64 KiB, at the price of the border
  with the United States being drawn within about 3 km instead of 1 km.
- **Vigicrues' weight** (settled): it was 100 MB a day calm and 300 MB in a flood for 337 sections of which a handful
  matter. No lighter endpoint carries the level, so bundling the lines would save nothing; the flow is now read on its
  publications only, which is 7 to 9 MB a day. What is left is the image signal (an inference), which the weekly check
  watches.
