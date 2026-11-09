"""CANDHIS (Cerema): the latest measure of the wave buoys of the French coasts.

``https://candhis.cerema.fr/API/v1/getCampListeTR.php?type=T`` answers, for every buoy of a kind, its **latest hourly real-time
record** (the guide: "Seule la dernière donnée horaire disponible est récupérée"). The kinds are 0 (non-directional, H1/3), 1
(directional, Hm0) and 2 (directional, H1/3): three requests read every buoy. The key goes in the ``Authorization`` header, as it
is (no ``Bearer``), never in the URL.

Read on 2026-11-09 (three answers kept in ``tests/fixtures/candhis/``): 28 buoys of kind 2, 2 of kind 0 and 1 of kind 1 answered. The
date of a record is "YYYY-MM-DD HH:MM", **in UTC**: the Candhis site heads that column "Heure (TU)"; the guide does not say. One
buoy was dated 23 minutes after the server's clock, so a date is kept as the buoy gave it and the age is never below zero. A missing
value is written ``999.9999``. A buoy that has stopped keeps answering with its last record (Royan's was 1.8 days old).

One request per kind per ``TTL_SECONDS``, whatever the number of clients: 72 a day at most, for the 150 a day that the Candhis team
announced with the key (an e-mail of 2026-11-09; the guide says only that a 429 answers once the daily quota is reached). The
User-Agent names the project. Without ``VIGIE_CANDHIS_API_KEY`` there is no collector (see ``candhis_key_from_env``).
"""

from __future__ import annotations

import asyncio
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from app.collectors.base import BaseCollector, CollectorUnavailableError, optional_float

BASE_URL = "https://candhis.cerema.fr/API/v1/getCampListeTR.php"
KEY_ENV = "VIGIE_CANDHIS_API_KEY"
TTL_SECONDS = 3600
KINDS = (0, 1, 2)
MISSING = 999.0  # Candhis writes a value it has not got as 999.9999
_KEY_SHAPE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

# The column names of the answer (lower case) -> what they are. The answer is read by its header, not by position: the three kinds
# do not have the same columns.
_HEIGHT = {"h1/3 (m)": "H1/3", "hm0 (m)": "Hm0"}
_PERIOD = {"th1/3 (s)": "TH1/3", "t02 (s)": "T02"}


@dataclass(frozen=True)
class BuoyReading:
    """The latest record of one buoy, as Candhis gave it (units: metres, seconds, degrees, degrees Celsius)."""

    station_id: str
    measured_utc: int
    height_m: float
    height_kind: str  # "H1/3" or "Hm0": two ways to measure the significant height, close but not the same
    hmax_m: Optional[float]
    period_s: Optional[float]
    period_kind: Optional[str]  # "TH1/3" or "T02"
    peak_period_s: Optional[float]  # only the non-directional buoys give it
    direction_deg: Optional[float]  # at the peak; only the directional buoys give it
    spread_deg: Optional[float]  # "Etal. au pic (°)", the spreading at the peak
    sea_temp_c: Optional[float]


Readings = dict[str, BuoyReading]


@dataclass(frozen=True)
class Latest:
    """What the collector knows of every buoy: ``stale`` when a part came from an expired cache (a read failed), ``failed_kinds``
    the kinds that gave nothing at all."""

    readings: Readings
    stale: bool
    failed_kinds: tuple[int, ...]


def candhis_key_from_env(env: Optional[Mapping[str, str]] = None) -> tuple[Optional[str], Optional[str]]:
    """``(key, problem)`` read from ``VIGIE_CANDHIS_API_KEY``. A key that is not shaped as the guide says
    (``xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx``) is **not used**, and the problem says so without quoting it."""
    raw = ((env if env is not None else os.environ).get(KEY_ENV) or "").strip()
    if not raw:
        return None, None
    if not _KEY_SHAPE.match(raw):
        return None, f"{KEY_ENV} is set but is not shaped as a Candhis key (8-4-4-4-12 hexadecimal digits): it is not used"
    return raw, None


def _number(value: Any) -> Optional[float]:
    number = optional_float(value)
    return None if number is None or number >= MISSING else number


def parse_latest(payload: Any) -> Readings:
    """The records of an answer of ``getCampListeTR.php`` by station. Raises ``ValueError`` when the answer is not Candhis's shape
    (the collector then falls back on its cache). A record without a station, a date that cannot be read or a height is skipped."""
    if not isinstance(payload, dict):
        raise ValueError("Candhis answer is not an object")
    if str(payload.get("success")).lower() != "true":
        raise ValueError(f"Candhis did not succeed: {str(payload.get('message'))[:120]!r}")
    header = payload.get("entete")
    rows = payload.get("results")
    if not isinstance(header, list) or not isinstance(rows, list):
        raise ValueError("Candhis answer without a header and a list of results")
    columns = {str(name).strip().lower(): i for i, name in enumerate(header)}
    for needed in ("campagne", "date"):
        if needed not in columns:
            raise ValueError(f"Candhis answer without a `{needed}` column")
    height_name = next((n for n in columns if n in _HEIGHT), None)
    if height_name is None:
        raise ValueError("Candhis answer without a height column (H1/3 or Hm0)")
    period_name = next((n for n in columns if n in _PERIOD), None)

    def cell(row: list[Any], name: Optional[str]) -> Any:
        i = columns.get(name) if name is not None else None
        return row[i] if i is not None and i < len(row) else None

    readings: Readings = {}
    for row in rows:
        if not isinstance(row, list):
            continue
        station = str(cell(row, "campagne") or "").strip()
        try:
            moment = datetime.strptime(str(cell(row, "date")).strip(), "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        height = _number(cell(row, height_name))
        if not station or height is None:
            continue
        readings[station] = BuoyReading(
            station_id=station,
            measured_utc=int(moment.timestamp()),
            height_m=height,
            height_kind=_HEIGHT[height_name],
            hmax_m=_number(cell(row, "hmax (m)")),
            period_s=_number(cell(row, period_name)),
            period_kind=_PERIOD[period_name] if period_name is not None else None,
            peak_period_s=_number(cell(row, "t. au pic (s)")),
            direction_deg=_number(cell(row, "dir. au pic (°)")),
            spread_deg=_number(cell(row, "etal. au pic (°)")),
            sea_temp_c=_number(cell(row, "temp. mer (°c)")),
        )
    return readings


class CandhisCollector(BaseCollector[Readings]):
    """The latest record of every CANDHIS buoy (three kinds, three requests an hour at most)."""

    def __init__(self, api_key: str, http_client: Optional[httpx.AsyncClient] = None) -> None:
        super().__init__(
            provider_id="candhis_tr",
            name="CANDHIS wave buoys (Cerema)",
            domain="marine",
            ttl_seconds=TTL_SECONDS,
            timeout_seconds=20.0,
            http_client=http_client,
        )
        self._key = api_key
        self.secrets = (api_key,)

    async def fetch_latest(self) -> Latest:
        """The latest record of every buoy. A kind that cannot be read keeps its last answer (the result is then ``stale``); raises
        ``CollectorUnavailableError`` only when no kind gave anything, now or before."""
        answers = await asyncio.gather(*(self.fetch_ex(cache_key=f"kind:{kind}", buoy_kind=kind) for kind in KINDS), return_exceptions=True)
        readings: Readings = {}
        stale = False
        failed: list[int] = []
        for kind, answer in zip(KINDS, answers, strict=True):
            if isinstance(answer, BaseException):
                if not isinstance(answer, CollectorUnavailableError):
                    raise answer
                failed.append(kind)
                continue
            readings.update(answer.value)
            stale = stale or answer.stale
        if len(failed) == len(KINDS):
            raise CollectorUnavailableError(self.redact(self.last_error or "no CANDHIS answer"))
        return Latest(readings, stale, tuple(failed))

    async def _collect(self, client: httpx.AsyncClient, **kwargs: Any) -> Readings:
        response = await self._get(client, BASE_URL, params={"type": str(kwargs["buoy_kind"])}, headers={"Authorization": self._key})
        response.raise_for_status()
        return parse_latest(response.json())
