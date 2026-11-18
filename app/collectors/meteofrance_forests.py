"""The forest fire danger of Météo-France ("Météo des forêts"), read through a vigie-meteo gateway (optional).

``DPMeteoForets/v1`` gives, for every metropolitan department, a danger of forest fires in four levels (1 low to 4 very high) for the two days after
the publication. The flux is **seasonal**: published each day at about 17:00 local time from June to September (the API's documentation, read on
2026-11-18, and its licence document, "Licence Ouverte 2.0"); out of season the portal answers 404. The gateway (``Axellum/vigie-meteo``,
``GET /v1/forests?department=``) asks the portal every 30 minutes in season and every 6 hours otherwise, and says ``out_of_season`` when there is
nothing: that is a normal state, not a failure, and this collector passes it on as such.

The format of the map the gateway parses is the one of the sample Météo-France publishes on data.gouv.fr (22/05/2024); the file the portal serves today could
not be read out of season (ADR 0035): if it differs, the gateway reports an error and this API says ``unavailable``, it never shows an empty map as
"no danger".

Without ``VIGIE_METEO_GATEWAY_URL`` there is no collector and ``GET /v1/forest-fire`` says ``not_configured``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Optional

import httpx

from app.collectors.base import BaseCollector

PROVIDER_ID = "meteofrance_forests"
SOURCE_MODEL = "meteofrance-forests"
TTL_SECONDS = 600
DEPARTMENT_RE = re.compile(r"^(\d{2}|2A|2B)$")


@dataclass(frozen=True, slots=True)
class ForestsDepartment:
    """The answer for a department: ``available`` with its levels, or ``out_of_season`` (no levels)."""

    status: Literal["available", "out_of_season"]
    reference_time: Optional[str]
    checked_at_utc: Optional[int]
    level_d1: Optional[int]
    level_d2: Optional[int]


def _level(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 4:
        raise ValueError("a danger level is 1 to 4")
    return value


def parse_gateway_forests(payload: Any, department: str) -> ForestsDepartment:
    """Check and read the gateway's answer for a department; ``ValueError`` when it is not what the source says it is."""
    if not isinstance(payload, dict) or payload.get("source_model") != SOURCE_MODEL:
        raise ValueError("not an answer of the Météo des forêts of Météo-France")
    status = payload.get("status")
    if status not in ("available", "out_of_season"):
        raise ValueError("unknown status")
    checked = payload.get("checked_at")
    checked_s = int(datetime.fromisoformat(str(checked).replace("Z", "+00:00")).timestamp()) if checked else None
    if status == "out_of_season":
        return ForestsDepartment("out_of_season", None, checked_s, None, None)
    rows = [d for d in payload.get("departments", []) if isinstance(d, dict) and d.get("department") == department]
    if not rows:
        raise ValueError("the department is not in the published map")
    reference = payload.get("reference_time")
    return ForestsDepartment(
        "available",
        str(reference) if reference else None,
        checked_s,
        _level(rows[0].get("level_d1")),
        _level(rows[0].get("level_d2")),
    )


class MeteoFranceForestsCollector(BaseCollector[ForestsDepartment]):
    """The forest danger of a department through the gateway, cached per department."""

    def __init__(self, base_url: str, http_client: Optional[httpx.AsyncClient] = None) -> None:
        super().__init__(
            provider_id=PROVIDER_ID,
            name="Météo-France forest fire danger, Météo des forêts (vigie-meteo gateway)",
            domain="weather",
            ttl_seconds=TTL_SECONDS,
            failure_threshold=3,
            recovery_timeout_seconds=120,
            timeout_seconds=8.0,
            http_client=http_client,
        )
        self.base_url = base_url.rstrip("/")

    async def _collect(self, client: httpx.AsyncClient, **kwargs: Any) -> ForestsDepartment:
        kwargs.pop("previous", None)
        department = str(kwargs["department"])
        response = await self._get(client, f"{self.base_url}/v1/forests", params={"department": department})
        response.raise_for_status()
        return parse_gateway_forests(response.json(), department)

    async def fetch_department(self, department: str) -> ForestsDepartment:
        """The danger of a department; raises when the gateway cannot give it (the caller says ``unavailable``)."""
        if not DEPARTMENT_RE.match(department):
            raise ValueError("a department code is two digits, 2A or 2B")
        return await self.fetch(f"forests:{department}", department=department)
