# CANDHIS (Cerema) recorded answers

`list-type0-2026-11-09.json`, `list-type1-2026-11-09.json` and `list-type2-2026-11-09.json`: the three answers of
`https://candhis.cerema.fr/API/v1/getCampListeTR.php?type=0`, `type=1` and `type=2` (every buoy of a kind: its latest hourly
real-time record), as the API answered them on 2026-11-09 at 19:07 UTC (the `Date` header of the answers), unchanged
(Licence Ouverte 2.0, Candhis, Cerema). The key goes in the `Authorization` header, so it is in none of these files.

What they hold that a test relies on:

- type 2 has 28 buoys, type 0 has 2 (one dated 2026-11-08 00:00: a buoy that has stopped, still answering its last record) and type 1 has 1
  (dated 2026-11-09 14:00);
- `999.9999` is how a value the buoy has not got is written (the direction of Bonifacio, the sea temperature of Ile d'Yeu Nord);
- `04403` (Plateau du Four) is dated `2026-11-09 19:30`, 23 minutes after the server's clock: the date is kept as given, the age is never
  below zero;
- the header differs by kind (`H1/3 (m)` or `Hm0 (m)`, `TH1/3 (s)` or `T02 (s)`, `T. au pic (s)` for the non-directional ones only).

Used by `tests/test_candhis.py` and `tests/test_live_feeds_tool.py` to replay without the network.
