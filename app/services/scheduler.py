"""Background scheduler and differential push detector (``app/services/scheduler.py``).

This service:

1. Refreshes the world snapshot periodically without blocking (``run_single_cycle``).
2. Detects every **new alert** or **severity increase** (``detect_new_or_upgraded_hazards``:
   ``GREEN -> YELLOW -> ORANGE -> RED``, or a higher magnitude / intensity).
3. Pushes urgent alerts over WebSocket (``/v1/stream``) together with the updated embedded
   frame (``embedded_frame``) for the displays.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any, Optional

from app.models.schemas import SEVERITY_RANK, HazardAlert
from app.services.aggregator import AggregatorService
from app.services.embedded_frame import serialize_embedded_frame


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
        self._known_hazards: dict[str, HazardAlert] = {}
        self._task: Optional[asyncio.Task[None]] = None

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

            return {
                "status": "ok",
                "cycles_count": self.cycles_count,
                "last_run_utc": self.last_run_utc,
                "total_hazards": len(snapshot.hazards),
                "new_or_upgraded_count": len(diff),
                "new_or_upgraded_ids": [h.id for h in diff],
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

    async def _loop(self) -> None:
        while self.running:
            await self.run_single_cycle()
            await asyncio.sleep(self.interval_seconds)

    def start(self) -> None:
        """Start the periodic collection loop as an asyncio background task."""
        if self.running:
            return
        self.running = True
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        """Stop the asyncio background task cleanly."""
        self.running = False
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

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
        }
