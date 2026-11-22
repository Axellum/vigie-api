- **Home Assistant: the weather of the place** (integration 0.5.0, ADR 0042): a new option, "Weather of the place and
  dashboard card", on by default, adds a weather entity (current weather and days of `/v1/point`, hours once the API gives
  them) and three sensors: rain in the next hour (unknown when one step is unknown, never 0), strongest gust of the next hours
  and next high tide. Three commands on Home Assistant's WebSocket (`vigie/card/entries`, `vigie/card/data`,
  `vigie/card/spots`) give a dashboard card what the entry has read, with the measured wind of the nearest Météo-France
  station, the normals of the month and the verdicts of a spot nearby. Unticked, the entry reads the warnings and the
  earthquakes only.
