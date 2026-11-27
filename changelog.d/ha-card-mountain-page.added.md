- **The Mountain page of the Home Assistant card**: stations chosen in the card's editor (a name, a position on a map, the
  altitude of the base and of the top), shown as chips. For each: a sentence on the next snow or rain, the avalanche danger, the
  freezing level and the next fresh snow; the avalanche bulletin in force with its levels by altitude and the aspects of its
  problems on a rose; 24 hours at the top and at the base; 7 days at the top. The integration reads a station when a card asks for
  it (new WebSocket command `vigie/card/mountain`: `/v1/point?elevation=` at the top and at the base, `/v1/avalanches`), keeps it
  10 minutes and trims it to what the page draws. On a phone, the bar of the pages shows icons when the names do not fit.
