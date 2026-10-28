"""Aurora collectors: four products of the NOAA Space Weather Prediction Center (SWPC), read for ``GET /v1/sky/aurora``.

The addresses come from SWPC's own index of its data (https://services.swpc.noaa.gov/json/ and /products/, read 2026-10-28):

- ``json/ovation_aurora_latest.json``: the OVATION model, "a 30 to 90 minute forecast of the location and intensity of the
  aurora" on a grid of one degree (360 longitudes by 181 latitudes, both hemispheres), with its observation and forecast times.
  SWPC: "An estimate of aurora viewing probability can be derived by assuming a linear relationship to the intensity of the
  aurora" (https://www.swpc.noaa.gov/products/aurora-30-minute-forecast). 0.9 MB, 0.16 MB compressed.
- ``products/noaa-planetary-k-index-forecast.json``: the planetary Kp index by 3-hour interval, observed for the last days,
  then estimated, then predicted for three days, with the NOAA G scale when it is reached.
- ``products/geospace/propagated-solar-wind-1-hour.json``: the solar wind measured at L1 over the last hour (speed, density,
  magnetic field), each row with the time SWPC's propagation gives it.
- ``products/alerts.json``: the alerts, watches and warnings SWPC issued over the last days, as their text messages.

Each file is asked with ``If-Modified-Since`` once a copy is cached: SWPC answers ``304`` with no body when nothing changed. SWPC
rewrites these files every one to three minutes (their ``Last-Modified``, watched for 12 minutes on 2026-10-28), so within the cache
lifetimes below the answer is usually a new copy. The parsers never invent a value: a missing number is ``None``,
a file that has nothing readable raises and the collector serves its last copy.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

from app import clock
from app.collectors.base import BaseCollector, CacheEntry, Collected, optional_float, parse_iso_utc

SWPC_SERVICES = "https://services.swpc.noaa.gov"

# The OVATION grid: longitudes 0 to 359 and latitudes -90 to 90, one degree apart.
OVATION_LONGITUDES = 360
OVATION_LATITUDES = 181


@dataclass(frozen=True)
class OvationGrid:
    """One OVATION forecast as SWPC published it: the value of each cell of the one-degree grid (``values[lon][lat + 90]``)."""

    observation_utc: int
    forecast_utc: int
    values: bytes  # OVATION_LONGITUDES * OVATION_LATITUDES values, the cell of (lon, lat) at lon * 181 + (lat + 90)
    max_value: int

    def value_at(self, lat: float, lon: float) -> int:
        """The value of the cell nearest to ``(lat, lon)``."""
        lon_index = int(round(lon)) % OVATION_LONGITUDES
        lat_index = max(0, min(OVATION_LATITUDES - 1, int(round(lat)) + 90))
        return self.values[lon_index * OVATION_LATITUDES + lat_index]

    def cells_at_least(self, threshold: int) -> list[tuple[int, int, int]]:
        """Every cell whose value is at least ``threshold``, as ``(lat, lon, value)`` with ``lon`` in [-180, 180)."""
        cells: list[tuple[int, int, int]] = []
        for lon_index in range(OVATION_LONGITUDES):
            base = lon_index * OVATION_LATITUDES
            lon = lon_index - 360 if lon_index >= 180 else lon_index
            for lat_index in range(OVATION_LATITUDES):
                value = self.values[base + lat_index]
                if value >= threshold:
                    cells.append((lat_index - 90, lon, value))
        return cells


@dataclass(frozen=True)
class KpInterval:
    """One 3-hour interval of the Kp file: its start, the Kp, and whether SWPC marks it observed, estimated or predicted."""

    start_utc: int
    kp: float
    kind: str  # "observed", "estimated" or "predicted", as the file says
    noaa_scale: Optional[str]  # "G1" to "G5" when the file gives one


@dataclass(frozen=True)
class SolarWind:
    """The latest solar wind row of the last hour (measured at L1)."""

    measured_utc: int
    propagated_utc: Optional[int]
    speed_km_s: Optional[float]
    density_cm3: Optional[float]
    bt_nt: Optional[float]
    bz_nt: Optional[float]


@dataclass(frozen=True)
class SpaceWeatherMessage:
    """One message of ``alerts.json``, its text as SWPC wrote it."""

    product_id: str
    code: Optional[str]  # the "Space Weather Message Code" line of the message
    serial: Optional[str]
    issued_utc: int
    headline: Optional[str]  # the first line that starts with WATCH:, WARNING:, ALERT:, SUMMARY: (and their variants)
    message: str


@dataclass(frozen=True)
class SpaceWeatherMessages:
    """Every message of ``alerts.json`` that could be read."""

    messages: list[SpaceWeatherMessage] = field(default_factory=list)


def _swpc_utc(value: Any) -> Optional[int]:
    """A time of the SWPC files (``2026-10-28T20:17:00Z``, ``2026-10-28T21:00:00`` or ``2026-10-28 16:38:01.100``) as UTC epoch seconds."""
    if not isinstance(value, str) or not value.strip():
        return None
    return parse_iso_utc(value.strip().replace(" ", "T"))


def _conditional_headers(previous: Optional[CacheEntry[Any]]) -> dict[str, str]:
    """``If-Modified-Since`` with the ``Last-Modified`` of the cached copy, when there is one."""
    if previous is not None and previous.validators and previous.validators.get("Last-Modified"):
        return {"If-Modified-Since": previous.validators["Last-Modified"]}
    return {}


class _SWPCFileCollector(BaseCollector[Any]):
    """A collector of one SWPC file, asked conditionally; the parser runs in a thread (the OVATION grid is 0.9 MB of JSON)."""

    URL = ""

    async def _collect(self, client: httpx.AsyncClient, **kwargs: Any) -> Collected[Any]:
        previous: Optional[CacheEntry[Any]] = kwargs.get("previous")
        resp = await self._get(client, self.URL, headers=_conditional_headers(previous))
        if resp.status_code == 304:
            if previous is None:
                raise ValueError(f"{self.provider_id}: 304 Not Modified to a request that was not conditional")
            return Collected(previous.value, validators=previous.validators)
        resp.raise_for_status()
        value = await clock.run_blocking(self.parse_bytes, resp.content)
        last_modified = resp.headers.get("Last-Modified")
        return Collected(value, validators={"Last-Modified": last_modified} if last_modified else None)

    def parse_bytes(self, content: bytes) -> Any:
        """Decode and read the file (in a thread: ``clock.run_blocking``)."""
        return self.parse(json.loads(content))

    @staticmethod
    def parse(payload: Any) -> Any:
        """Read the decoded file; each collector has its own."""
        raise NotImplementedError


class SWPCOvationCollector(_SWPCFileCollector):
    """The OVATION aurora forecast of SWPC (both hemispheres, one-degree grid)."""

    URL = f"{SWPC_SERVICES}/json/ovation_aurora_latest.json"

    def __init__(self, http_client: Optional[httpx.AsyncClient] = None) -> None:
        super().__init__(
            provider_id="noaa_swpc_ovation",
            name="NOAA SWPC OVATION aurora forecast (30 to 90 minutes)",
            domain="space",
            # The file is rewritten every 1 to 3 minutes (its Last-Modified, watched for 12 minutes on 2026-10-28) and sent with
            # max-age=60; the forecast itself looks 30 to 90 minutes ahead, so 5 minutes loses little and saves 0.16 MB a minute.
            ttl_seconds=300,
            timeout_seconds=15.0,
            http_client=http_client,
        )

    @staticmethod
    def parse(payload: Any) -> OvationGrid:
        """Read the grid. Raises ``ValueError`` when the times or the coordinates are missing: nothing is filled in."""
        if not isinstance(payload, dict):
            raise ValueError("OVATION payload is not an object")
        observation = _swpc_utc(payload.get("Observation Time"))
        forecast = _swpc_utc(payload.get("Forecast Time"))
        if observation is None or forecast is None:
            raise ValueError("OVATION payload has no observation or forecast time")
        coordinates = payload.get("coordinates")
        if not isinstance(coordinates, list) or not coordinates:
            raise ValueError("OVATION payload has no coordinates")
        values = bytearray(OVATION_LONGITUDES * OVATION_LATITUDES)
        seen = 0
        for row in coordinates:
            if not isinstance(row, list) or len(row) < 3:
                continue
            lon, lat, value = optional_float(row[0]), optional_float(row[1]), optional_float(row[2])
            if lon is None or lat is None or value is None or not (-90 <= lat <= 90):
                continue
            lon_index = int(round(lon)) % OVATION_LONGITUDES
            lat_index = int(round(lat)) + 90
            values[lon_index * OVATION_LATITUDES + lat_index] = max(0, min(255, int(round(value))))
            seen += 1
        if seen < OVATION_LONGITUDES * OVATION_LATITUDES // 2:
            raise ValueError(f"OVATION payload has only {seen} readable cells")
        return OvationGrid(observation, forecast, bytes(values), max(values))


class SWPCKpForecastCollector(_SWPCFileCollector):
    """The planetary Kp index by 3-hour interval: observed, estimated and predicted for three days."""

    URL = f"{SWPC_SERVICES}/products/noaa-planetary-k-index-forecast.json"

    def __init__(self, http_client: Optional[httpx.AsyncClient] = None) -> None:
        super().__init__(
            provider_id="noaa_swpc_kp_forecast",
            name="NOAA SWPC planetary Kp index forecast (3 days)",
            domain="space",
            # The intervals are 3 hours long and the forecast is a 3-day one; the file is rewritten about every minute (the estimated
            # value of the current interval moves). 15 minutes.
            ttl_seconds=900,
            http_client=http_client,
        )

    @staticmethod
    def parse(payload: Any) -> list[KpInterval]:
        """Read the intervals, oldest first. Two layouts: a list of objects, or a list of lists under a header row.

        Raises ``ValueError`` when no interval can be read.
        """
        if not isinstance(payload, list) or not payload:
            raise ValueError("Kp forecast payload is empty")
        rows: list[dict[str, Any]] = []
        if isinstance(payload[0], list):
            header = [str(name).strip().lower() for name in payload[0]]
            rows = [dict(zip(header, row, strict=False)) for row in payload[1:] if isinstance(row, list)]
        else:
            rows = [{str(k).lower(): v for k, v in row.items()} for row in payload if isinstance(row, dict)]
        intervals: list[KpInterval] = []
        for row in rows:
            start = _swpc_utc(row.get("time_tag"))
            kp = optional_float(row.get("kp"))
            kind = str(row.get("observed") or "").strip().lower()
            if start is None or kp is None or kind not in ("observed", "estimated", "predicted"):
                continue
            scale = row.get("noaa_scale")
            intervals.append(
                KpInterval(start, round(min(9.0, max(0.0, kp)), 2), kind, scale if isinstance(scale, str) and scale else None)
            )
        if not intervals:
            raise ValueError("Kp forecast payload has no readable interval")
        intervals.sort(key=lambda interval: interval.start_utc)
        return intervals


class SWPCSolarWindCollector(_SWPCFileCollector):
    """The solar wind of the last hour, measured at L1, as SWPC's geospace product propagates it."""

    URL = f"{SWPC_SERVICES}/products/geospace/propagated-solar-wind-1-hour.json"

    def __init__(self, http_client: Optional[httpx.AsyncClient] = None) -> None:
        super().__init__(
            provider_id="noaa_swpc_solar_wind",
            name="NOAA SWPC solar wind at L1 (last hour)",
            domain="space",
            # One row a minute (sent with max-age=60); the aurora estimate does not use it, it is shown as it is: 5 minutes.
            ttl_seconds=300,
            http_client=http_client,
        )

    @staticmethod
    def parse(payload: Any) -> SolarWind:
        """The latest row that has a time and a speed. Raises ``ValueError`` when there is none."""
        if not isinstance(payload, list) or len(payload) < 2 or not isinstance(payload[0], list):
            raise ValueError("Solar wind payload has no header and rows")
        header = [str(name).strip().lower() for name in payload[0]]
        for raw in reversed(payload[1:]):
            if not isinstance(raw, list):
                continue
            row = dict(zip(header, raw, strict=False))
            measured = _swpc_utc(row.get("time_tag"))
            speed = optional_float(row.get("speed"))
            if measured is None or speed is None:
                continue
            return SolarWind(
                measured_utc=measured,
                propagated_utc=_swpc_utc(row.get("propagated_time_tag")),
                speed_km_s=round(speed, 1),
                density_cm3=optional_float(row.get("density")),
                bt_nt=optional_float(row.get("bt")),
                bz_nt=optional_float(row.get("bz")),
            )
        raise ValueError("Solar wind payload has no row with a time and a speed")


_CODE = re.compile(r"Space Weather Message Code:\s*(\S+)")
_SERIAL = re.compile(r"Serial Number:\s*(\S+)")
_HEADLINE = re.compile(r"^\s*((?:EXTENDED |CANCEL )?(?:WATCH|WARNING|ALERT|SUMMARY|CONTINUED ALERT)\b.*?:.*)$", re.MULTILINE)


class SWPCAlertsCollector(_SWPCFileCollector):
    """The alerts, watches and warnings of SWPC, as their text messages."""

    URL = f"{SWPC_SERVICES}/products/alerts.json"

    def __init__(self, http_client: Optional[httpx.AsyncClient] = None) -> None:
        super().__init__(
            provider_id="noaa_swpc_alerts",
            name="NOAA SWPC alerts, watches and warnings",
            domain="space",
            # The messages are issued when an event happens (none for hours on a quiet day); 5 minutes.
            ttl_seconds=300,
            http_client=http_client,
        )

    @staticmethod
    def parse(payload: Any) -> SpaceWeatherMessages:
        """Read every message that has an identifier, a time and a text; the text is kept as SWPC wrote it (line ends normalized).

        An empty list is a valid answer (no message). Raises ``ValueError`` when the payload is not a list.
        """
        if not isinstance(payload, list):
            raise ValueError("SWPC alerts payload is not a list")
        messages: list[SpaceWeatherMessage] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            product = item.get("product_id")
            issued = _swpc_utc(item.get("issue_datetime"))
            text = item.get("message")
            if not isinstance(product, str) or issued is None or not isinstance(text, str) or not text.strip():
                continue
            text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
            code = _CODE.search(text)
            serial = _SERIAL.search(text)
            headline = _HEADLINE.search(text)
            messages.append(
                SpaceWeatherMessage(
                    product_id=product,
                    code=code.group(1) if code else None,
                    serial=serial.group(1) if serial else None,
                    issued_utc=issued,
                    headline=headline.group(1).strip() if headline else None,
                    message=text,
                )
            )
        messages.sort(key=lambda message: message.issued_utc, reverse=True)
        return SpaceWeatherMessages(messages)
