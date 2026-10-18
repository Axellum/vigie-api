"""Scheduler asynchrone en arrière-plan & Détecteur différentiel d'alertes Push (`app/services/scheduler.py`).

Ce service assure :
1. Le rafraîchissement périodique non-bloquant du snapshot mondial (`run_single_cycle`).
2. La détection différentielle (`detect_new_or_upgraded_hazards`) de toute **nouvelle alerte**
   ou **aggravation de sévérité** (`GREEN -> YELLOW -> ORANGE -> RED` ou hausse de magnitude/intensité).
3. La diffusion instantanée via WebSocket (`/v1/stream`) des alertes urgentes accompagnées
   de la trame C++ mise à jour (`embedded_frame`) pour les terminaux Waveshare 7B et M5Stack Tab5.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any, Optional

from app.models.schemas import HazardAlert, SEVERITY_RANK
from app.services.aggregator import AggregatorService
from app.services.embedded_frame import serialize_embedded_frame


def detect_new_or_upgraded_hazards(
    previous_by_id: dict[str, HazardAlert],
    current_hazards: list[HazardAlert],
) -> list[HazardAlert]:
    """Retourne les alertes nouvellement apparues ou dont la sévérité / intensité a augmenté."""
    changed: list[HazardAlert] = []
    for h in current_hazards:
        prev = previous_by_id.get(h.id)
        if prev is None:
            changed.append(h)
        elif SEVERITY_RANK[h.severity] > SEVERITY_RANK[prev.severity]:
            changed.append(h)
        elif h.value_primary > prev.value_primary or (h.tsunami_warning and not prev.tsunami_warning):
            changed.append(h)
    return changed


class BackgroundAlertScheduler:
    """Ordonnanceur asynchrone de collecte périodique et de notification Push WebSocket."""

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
        """Exécute un cycle complet de collecte, calcul différentiel et push WebSocket si nécessaire."""
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
                "max_severity": snapshot.global_status.max_severity.value,
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
        """Démarre la boucle de collecte périodique en tâche de fond asyncio."""
        if self.running:
            return
        self.running = True
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        """Arrête proprement la tâche de fond asyncio."""
        self.running = False
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    def get_status(self) -> dict[str, Any]:
        """Retourne l'état opérationnel du scheduler."""
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
