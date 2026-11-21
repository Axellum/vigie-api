- **Radar of Météo-France over the Antilles, La Réunion and New Caledonia** (`GET /v1/radar/{area}` and `GET /v1/radar/{area}/tiles/{epoch}/{z}/{x}/{y}.png` with `area` `france`, `antilles`, `reunion` or `new-caledonia`; `DPRadar` mosaics `ANTILLES`, `REUNION`, `NOUVELLE-CALEDONIE`, same product as the metropolitan one,
  read and drawn by the vigie-meteo gateway, which must be updated first): for a point inside the grid of one of them (including Mauritius, in La Réunion's),
  `rain_nowcast.radar_tile_url_template` now points to that area's newest image, with Météo-France's credit. Guyane, Mayotte, French Polynesia and
  Saint-Pierre-et-Miquelon have no radar mosaic in `DPRadar`. Same licence as the metropolitan mosaic (ADR 0037).
