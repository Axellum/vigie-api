"""Space weather collector: NOAA Space Weather Prediction Center (SWPC).

Tracks the planetary geomagnetic Kp index (0 to 9) and the storm scale (G1 to G5).
It matters for:

- GPS / GNSS accuracy at sea and on mobile terminals (M5Stack Tab5).
- HF radio propagation and blackout risk on the high seas.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

import httpx

from app.collectors.base import BaseCollector, optional_float
from app.models.schemas import SpaceWeatherStatus

StormScale = Literal["G0", "G1", "G2", "G3", "G4", "G5"]


def kp_to_noaa_g_scale(kp: float) -> StormScale:
    """Convert a planetary Kp index to the NOAA geomagnetic storm level (G0-G5)."""
    if kp >= 9.0:
        return "G5"
    if kp >= 8.0:
        return "G4"
    if kp >= 7.0:
        return "G3"
    if kp >= 6.0:
        return "G2"
    if kp >= 5.0:
        return "G1"
    return "G0"


class NOAASWPCCollector(BaseCollector[SpaceWeatherStatus]):
    """Asynchronous collector of the NOAA SWPC planetary Kp index."""

    KP_URL = "https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json"

    def __init__(self, http_client: Optional[httpx.AsyncClient] = None) -> None:
        super().__init__(
            provider_id="noaa_swpc",
            name="NOAA Space Weather Prediction Center (Planetary Kp)",
            domain="space",
            ttl_seconds=300,
            http_client=http_client,
        )

    async def _collect(self, client: httpx.AsyncClient, **kwargs: Any) -> SpaceWeatherStatus:
        resp = await client.get(self.KP_URL)
        resp.raise_for_status()
        return self.parse_kp_json(resp.json())

    @staticmethod
    def _kp_of_row(row: Any, kp_column: Optional[int]) -> Optional[float]:
        """Read the Kp value of one row (an object ``{"Kp": ...}`` or a list indexed by the header)."""
        if isinstance(row, dict):
            for key in ("Kp", "kp", "kp_index"):
                if key in row:
                    return optional_float(row[key])
            return None
        if isinstance(row, list) and kp_column is not None and len(row) > kp_column:
            return optional_float(row[kp_column])
        return None

    @staticmethod
    def parse_kp_json(payload: Any) -> SpaceWeatherStatus:
        """Extract the latest valid Kp from the NOAA SWPC JSON.

        Two layouts are supported: a list of objects (``{"time_tag", "Kp", ...}``, the
        current one) and a list of lists whose first row is a header. Raises ``ValueError``
        when no Kp can be read: no default value is invented.
        """
        if not isinstance(payload, list) or not payload:
            raise ValueError("NOAA SWPC Kp payload is empty")

        kp_column: Optional[int] = None
        header = payload[0]
        if isinstance(header, list):
            names = [str(name).strip().lower() for name in header]
            kp_column = names.index("kp") if "kp" in names else None

        kp_val: Optional[float] = None
        for row in reversed(payload):
            kp_val = NOAASWPCCollector._kp_of_row(row, kp_column)
            if kp_val is not None:
                break
        if kp_val is None:
            raise ValueError("NOAA SWPC Kp payload has no readable Kp value")

        kp_val = round(min(9.0, max(0.0, kp_val)), 1)
        return SpaceWeatherStatus(
            solar_kp=kp_val,
            solar_storm_level=kp_to_noaa_g_scale(kp_val),
            gps_degraded=bool(kp_val >= 6.0),
            hf_radio_blackout_risk=bool(kp_val >= 5.0),
        )
