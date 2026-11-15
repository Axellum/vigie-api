- **Fires & near places**: added NASA FIRMS active fire detections from the last 24 hours to `near_places` on
  `WILDFIRE` alerts (`kind="fire"`, radius `VIGIE_NEAR_FIRE_KM`, default 50 km), returning `null` when FIRMS is off,
  unread or older than 24 hours, and carrying NASA's attribution and disclaimer on every response that includes them;
  hardened `FireService.nearby()` and `_current()` so empty readings return `null` and overdue partial reads carry
  `stale=true`.
