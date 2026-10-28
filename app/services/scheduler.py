"""Background scheduler and differential push detector (``app/services/scheduler.py``).

This service:

1. Refreshes the world snapshot periodically without blocking (``run_single_cycle``).
2. Detects every **new alert** or **severity increase** (``detect_new_or_upgraded_hazards``:
   ``GREEN -> YELLOW -> ORANGE -> RED``, or a higher magnitude / intensity).
3. Pushes urgent alerts over WebSocket (``/v1/stream``) together with the updated embedded
   frame (``embedded_frame``) for the displays.
4. Watches the official weather warnings (MeteoAlarm, NWS) of the watched zones and of the positions the
   clients follow, and pushes the changes (``vigilance_push``): a warning that appears, whose level goes up,
   or that ends. The feeds of the countries that are needed are kept fresh (a cached feed costs no request).
5. Keeps the travel advice of the ministries fresh, in a loop of its own (``run_travel_cycle``, every ``TRAVEL_INTERVAL_S``): each new
   version of an advice is archived even when nobody asks, and the first read of the FCDO's 226 pages never delays a cycle.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any, Optional

from app.models.schemas import SEVERITY_RANK, HazardAlert
from app.services.aggregator import AggregatorService
from app.services.embedded_frame import serialize_embedded_frame
from app.services.vigilance import FollowedPoint

# How often the travel advice is looked at: each ministry is asked only when its cache (an hour) is over, so a look costs nothing
# most of the time.
TRAVEL_INTERVAL_S = 600


def _value_increased(previous: Optional[float], current: Optional[float]) -> bool:
    """True when both values are known and the value went up."""
    return previous is not None and current is not None and current > previous


def detect_new_or_upgraded_hazards(
    previous_by_id: dict[str, HazardAlert],
    current_hazards: list[HazardAlert],
) -> list[HazardAlert]:
    """Return the alerts that are new or whose severity / intensity increased."""
    changed: list[HazardAlert] = []
    for h in current_hazards:
        prev = previous_by_id.get(h.id)
        if prev is None:
            changed.append(h)
        elif SEVERITY_RANK[h.severity] > SEVERITY_RANK[prev.severity]:
            changed.append(h)
        elif _value_increased(prev.value_primary, h.value_primary) or (
            h.tsunami_warning and not prev.tsunami_warning
        ):
            changed.append(h)
    return changed


class BackgroundAlertScheduler:
    """Asynchronous scheduler of periodic collection and WebSocket push notification."""

    def __init__(
        self,
        aggregator: AggregatorService,
        ws_hub: Any,
        interval_seconds: int = 90,
    ) -> None:
        self.aggregator = aggregator
        self.ws_hub = ws_hub
        self.interval_seconds = max(10, int(interval_seconds))

        self.running: bool = False
        self.cycles_count: int = 0
        self.last_run_utc: Optional[int] = None
        self.last_push_utc: Optional[int] = None
        self.last_new_or_upgraded_count: int = 0
        self.last_error: Optional[str] = None
        self.last_vigilance_changes_count: int = 0
        self.last_vigilance_error: Optional[str] = None
        self._known_hazards: dict[str, HazardAlert] = {}
        self._task: Optional[asyncio.Task[None]] = None
        self._travel_task: Optional[asyncio.Task[None]] = None
        self.last_travel_run_utc: Optional[int] = None
        self.last_travel_error: Optional[str] = None

    def followed_points(self) -> list[FollowedPoint]:
        """The positions whose official warnings are watched: the watched zones (everyone is told), then the clients' own."""
        points = [
            FollowedPoint(
                key=f"zone:{zone['id']}",
                lat=float(zone["lat"]),
                lon=float(zone["lon"]),
                label={"zone_id": str(zone["id"]), "zone_name": str(zone["name"])},
                audience="all",
            )
            for zone in self.aggregator.monitored_zones
        ]
        client_points = getattr(self.ws_hub, "followed_points", lambda: set())()
        points.extend(
            FollowedPoint(key=f"point:{lat},{lon}", lat=lat, lon=lon, label={}, audience="followers")
            for lat, lon in sorted(client_points)
        )
        return points

    async def run_vigilance_cycle(self) -> int:
        """Look at the official warnings of the followed positions and push what changed; returns how many changes."""
        try:
            points = self.followed_points()
            await self.aggregator.vigilances.refresh(points)
            changes = await self.aggregator.vigilances.poll_changes(points)
            for point, change in changes:
                message = change.model_dump(mode="json")
                if point.audience == "all":
                    await self.ws_hub.broadcast(message)
                else:
                    await self.ws_hub.send_to_followers((point.lat, point.lon), message)
            self.last_vigilance_changes_count = len(changes)
            self.last_vigilance_error = None
            return len(changes)
        except Exception as exc:
            self.last_vigilance_error = f"{type(exc).__name__}: {exc}"
            return 0

    async def run_single_cycle(self) -> dict[str, Any]:
        """Run one full cycle: collect, compute the difference, push over WebSocket if needed."""
        now_utc = int(time.time())
        try:
            snapshot = await self.aggregator.build_snapshot()
            diff = detect_new_or_upgraded_hazards(self._known_hazards, snapshot.hazards)
            self._known_hazards = {h.id: h for h in snapshot.hazards}

            self.cycles_count += 1
            self.last_run_utc = now_utc
            self.last_new_or_upgraded_count = len(diff)
            self.last_error = None

            if diff:
                self.last_push_utc = now_utc
                frame_str = serialize_embedded_frame(snapshot=snapshot)
                await self.ws_hub.broadcast(
                    {
                        "event": "alert_push",
                        "server_utc": now_utc,
                        "global_status": snapshot.global_status.model_dump(),
                        "new_or_upgraded_count": len(diff),
                        "new_or_upgraded_hazards": [h.model_dump() for h in diff],
                        "embedded_frame": frame_str,
                    }
                )

            vigilance_changes = await self.run_vigilance_cycle()

            return {
                "status": "ok",
                "cycles_count": self.cycles_count,
                "last_run_utc": self.last_run_utc,
                "total_hazards": len(snapshot.hazards),
                "new_or_upgraded_count": len(diff),
                "new_or_upgraded_ids": [h.id for h in diff],
                "vigilance_changes_count": vigilance_changes,
                "max_severity": (
                    snapshot.global_status.max_severity.value
                    if snapshot.global_status.max_severity is not None
                    else None
                ),
            }
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return {
                "status": "error",
                "cycles_count": self.cycles_count,
                "last_run_utc": self.last_run_utc,
                "error": self.last_error,
            }

    async def run_travel_cycle(self) -> None:
        """Keep the travel advice fresh and archive its new versions (a ministry that fails is only noted)."""
        try:
            await self.aggregator.travel.refresh()
            self.last_travel_run_utc = int(time.time())
            self.last_travel_error = None
        except Exception as exc:
            self.last_travel_error = f"{type(exc).__name__}: {exc}"

    async def _loop(self) -> None:
        while self.running:
            await self.run_single_cycle()
            await asyncio.sleep(self.interval_seconds)

    async def _travel_loop(self) -> None:
        while self.running:
            await self.run_travel_cycle()
            await asyncio.sleep(TRAVEL_INTERVAL_S)

    def start(self) -> None:
        """Start the periodic collection loop as an asyncio background task."""
        if self.running:
            return
        self.running = True
        self._task = asyncio.create_task(self._loop())
        self._travel_task = asyncio.create_task(self._travel_loop())

    async def stop(self) -> None:
        """Stop the asyncio background task cleanly."""
        self.running = False
        for task in (self._task, self._travel_task):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self._task = None
        self._travel_task = None

    def get_status(self) -> dict[str, Any]:
        """Return the operational state of the scheduler."""
        return {
            "running": self.running,
            "interval_seconds": self.interval_seconds,
            "cycles_count": self.cycles_count,
            "last_run_utc": self.last_run_utc,
            "last_push_utc": self.last_push_utc,
            "last_new_or_upgraded_count": self.last_new_or_upgraded_count,
            "tracked_hazards_count": len(self._known_hazards),
            "last_error": self.last_error,
            "last_vigilance_changes_count": self.last_vigilance_changes_count,
            "last_vigilance_error": self.last_vigilance_error,
            "last_travel_run_utc": self.last_travel_run_utc,
            "last_travel_error": self.last_travel_error,
        }
