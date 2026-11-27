- **The Sea page of the Home Assistant card, beach by beach**: up to 6 beaches ticked in the card's editor (spots within 60 km),
  shown as chips; without them, the spot of `spot`. For the one shown: the tide, the coefficient and the water; six tiles (the
  waves and the state of the sea, the swell, the wind onshore or offshore, the time of the strongest current of the baïnes on
  the ocean beaches from Charente-Maritime to the Basque Country, the class of the water of the nearest monitored bathing site,
  the lifeguards in season or the sun); the verdicts of the activities hour by hour over 24 hours; the tide of 24 hours; the
  next hours every 3 hours. The integration reads a beach when a card shows it (new WebSocket command `vigie/card/beach`:
  `/v1/surf/spots/{spot_id}`, `/v1/point` with the sea, `/v1/sea/places`, `/v1/activities/{spot_id}`), keeps it 30 minutes
  (the bathing sites a day) and trims it to what the page draws. No flag of the beach: no open source gives it.
