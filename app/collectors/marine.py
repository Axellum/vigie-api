"""Marine weather and oceanography collector: Open-Meteo Marine API.

Provides, at the exact GPS position of a vessel (``lat, lon``):

- Significant wave height, direction and period.
- Primary swell height, direction and period.
- Surface ocean current speed and direction.
- Sea state classification (Douglas scale) and a navigation warning.

A variable the API does not supply stays ``None``. The Marine API returns nothing for
inland points: the collector then raises, and the caller reports "no marine data".
"""

from __future__ import annotations

from typing import Any, Literal, Optional

import httpx

from app.collectors.base import BaseCollector, optional_float
from app.models.schemas import MarineConditions

SeaStateLabel = Literal["CALM", "SMOOTH", "MODERATE", "ROUGH", "VERY_ROUGH", "HIGH", "PHENOMENAL"]


def classify_sea_state_douglas(wave_height_m: float) -> SeaStateLabel:
    """Classify the sea state with the international Douglas scale."""
    if wave_height_m < 0.1:
        return "CALM"
    if wave_height_m < 0.5:
        return "SMOOTH"
    if wave_height_m < 1.25:
        return "MODERATE"
    if wave_height_m < 2.5:
        return "ROUGH"
    if wave_height_m < 4.0:
        return "VERY_ROUGH"
    if wave_height_m < 6.0:
        return "HIGH"
    return "PHENOMENAL"


def _direction(value: Any) -> Optional[int]:
    """Round a direction in degrees to ``[0, 359]``, or ``None`` when missing."""
    number = optional_float(value)
    return None if number is None else int(round(number)) % 360


def _rounded(value: Any, digits: int) -> Optional[float]:
    """Round a non-negative provider number, or ``None`` when missing."""
    number = optional_float(value)
    return None if number is None else round(max(0.0, number), digits)


class OpenMeteoMarineCollector(BaseCollector[MarineConditions]):
    """Asynchronous Open-Meteo Marine API collector for the boat / GPS mode."""

    BASE_URL = "https://marine-api.open-meteo.com/v1/marine"

    def __init__(self, http_client: Optional[httpx.AsyncClient] = None) -> None:
        super().__init__(
            provider_id="open_meteo_marine",
            name="Open-Meteo Marine & Oceanography API",
            domain="marine",
            ttl_seconds=300,
            http_client=http_client,
        )

    async def fetch_marine(self, lat: float, lon: float) -> MarineConditions:
        """Fetch the marine conditions at ``lat, lon``."""
        lat_r = round(float(lat), 2)
        lon_r = round(float(lon), 2)
        cache_key = f"marine:{lat_r:.2f},{lon_r:.2f}"
        return await self.fetch(cache_key=cache_key, lat=lat_r, lon=lon_r)

    async def _collect(self, client: httpx.AsyncClient, **kwargs: Any) -> MarineConditions:
        lat = float(kwargs["lat"])
        lon = float(kwargs["lon"])
        params = {
            "latitude": f"{lat:.2f}",
            "longitude": f"{lon:.2f}",
            "current": (
                "wave_height,wave_direction,wave_period,"
                "swell_wave_height,swell_wave_direction,swell_wave_period,"
                "ocean_current_velocity,ocean_current_direction"
            ),
        }
        resp = await client.get(self.BASE_URL, params=params)
        resp.raise_for_status()
        return self.parse_response(resp.json())

    @staticmethod
    def parse_response(payload: dict[str, Any]) -> MarineConditions:
        """Normalize an Open-Meteo Marine response. Missing variables become ``None``.

        Raises ``ValueError`` when the response carries no marine value at all.
        """
        curr = payload.get("current") or {}

        wave_h = _rounded(curr.get("wave_height"), 2)
        swell_h = _rounded(curr.get("swell_wave_height"), 2)
        curr_vel = _rounded(curr.get("ocean_current_velocity"), 2)
        if wave_h is None and swell_h is None and curr_vel is None:
            raise ValueError("Open-Meteo Marine response has no marine value")

        nav_warning = bool(
            (wave_h is not None and wave_h >= 3.5)
            or (swell_h is not None and swell_h >= 3.5)
            or (curr_vel is not None and curr_vel >= 6.0)
        )
        return MarineConditions(
            wave_height_m=wave_h,
            wave_direction_deg=_direction(curr.get("wave_direction")),
            wave_period_s=_rounded(curr.get("wave_period"), 1),
            swell_wave_height_m=swell_h,
            swell_wave_direction_deg=_direction(curr.get("swell_wave_direction")),
            swell_wave_period_s=_rounded(curr.get("swell_wave_period"), 1),
            ocean_current_velocity_kmh=curr_vel,
            ocean_current_direction_deg=_direction(curr.get("ocean_current_direction")),
            sea_state_label=None if wave_h is None else classify_sea_state_douglas(wave_h),
            navigation_warning=nav_warning,
        )
