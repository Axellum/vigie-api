- **Performance**: the hazards of the world are fused, annotated with their nearby places and written to the history once per
  reading of their sources, no longer on every request. While every source hands back the same cached list (and the watched
  zones, the recent fires and the simulation are unchanged), the last result is served again; the history's "last seen active"
  time is still refreshed once a minute. A warm `GET /v1/point` drops from about 18 ms to about 2 ms of work (profiled locally).
