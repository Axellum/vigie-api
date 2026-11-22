- **Forecast wind for a map**: `GET /v1/wind/field` gives the wind at 10 m forecast for the hour nearest to now by Météo-France's
  AROME (France) and ARPEGE (Europe, world), as three small PNG images (east and north components, about 120 to 280 KB each) that a
  map animates; `GET /v1/wind/field/{level}/{stamp}.png` serves them. Six requests an hour to the portal's WCS, with the key
  already used for the other Météo-France sources (ADR 0038). ecCodes is now behind one lock for every reader of GRIB2 files.
