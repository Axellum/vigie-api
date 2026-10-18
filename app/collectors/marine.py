"""Collecteur météo marine & océanographie : Open-Meteo Marine API.

Fournit aux coordonnées GPS exactes d'un navire (`lat, lon`) :
- Hauteur significative des vagues (`wave_height`), direction (`wave_direction`) et période (`wave_period`).
- Houle primaire (`swell_wave_height`, `swell_wave_direction`, `swell_wave_period`).
- Courants marins de surface (`ocean_current_velocity`, `ocean_current_direction`).
- Classification de l'état de la mer (Échelle Douglas) et alerte de navigation.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

import httpx

from app.collectors.base import BaseCollector
from app.models.schemas import MarineConditions

SeaStateLabel = Literal["CALM", "SMOOTH", "MODERATE", "ROUGH", "VERY_ROUGH", "HIGH", "PHENOMENAL"]


def classify_sea_state_douglas(wave_height_m: float) -> SeaStateLabel:
    """Classifie l'état de la mer selon l'échelle océanographique internationale de Douglas."""
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


class OpenMeteoMarineCollector(BaseCollector[MarineConditions]):
    """Collecteur asynchrone Open-Meteo Marine API pour le mode Bateau / GPS."""

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
        """Récupère les conditions marines aux coordonnées (`lat, lon`)."""
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
        """Normalise la réponse JSON d'Open-Meteo Marine."""
        curr = payload.get("current") or {}

        wave_h = round(float(curr.get("wave_height") or 0.0), 2)
        wave_dir = int(round(float(curr.get("wave_direction") or 0))) % 361
        wave_per = round(float(curr.get("wave_period") or 0.0), 1)

        swell_h = round(float(curr.get("swell_wave_height") or 0.0), 2)
        swell_dir = int(round(float(curr.get("swell_wave_direction") or 0))) % 361
        swell_per = round(float(curr.get("swell_wave_period") or 0.0), 1)

        curr_vel = round(float(curr.get("ocean_current_velocity") or 0.0), 2)
        curr_dir = int(round(float(curr.get("ocean_current_direction") or 0))) % 361

        sea_state = classify_sea_state_douglas(wave_h)
        nav_warning = bool(wave_h >= 3.5 or swell_h >= 3.5 or curr_vel >= 6.0)

        return MarineConditions(
            wave_height_m=wave_h,
            wave_direction_deg=wave_dir,
            wave_period_s=wave_per,
            swell_wave_height_m=swell_h,
            swell_wave_direction_deg=swell_dir,
            swell_wave_period_s=swell_per,
            ocean_current_velocity_kmh=curr_vel,
            ocean_current_direction_deg=curr_dir,
            sea_state_label=sea_state,
            navigation_warning=nav_warning,
        )
