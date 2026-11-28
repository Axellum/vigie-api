- **The weather of several places at once**: `GET /v1/points?at=lat,lon&at=…` answers, for 1 to 40 places, the weather block of
  `/v1/point` at each (current conditions, rain of the next hour, `hours` coming hours and `days` days), read from the same sources
  and caches, so a map can draw the weather icons of the towns it shows with one request (ADR 0047). Two places of one forecast
  cell cost one reading; a place whose weather cannot be read comes back without it, the others with theirs.
