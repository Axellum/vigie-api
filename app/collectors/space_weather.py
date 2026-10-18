"""Collecteur de Météo Spatiale : NOAA Space Weather Prediction Center (SWPC).

Surveille l'indice géomagnétique planétaire Kp (0 à 9) et les tempêtes solaires (G1 à G5).
Critique pour :
- La précision du positionnement GPS / GNSS en mer et sur les terminaux mobiles (M5Stack Tab5).
- La propagation et les risques de blackout radio HF en haute mer.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

import httpx

from app.collectors.base import BaseCollector
from app.models.schemas import SpaceWeatherStatus

StormScale = Literal["G0", "G1", "G2", "G3", "G4", "G5"]


def kp_to_noaa_g_scale(kp: float) -> StormScale:
    """Convertit un indice Kp planétaire en niveau de tempête géomagnétique NOAA (G0-G5)."""
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
    """Collecteur asynchrone de l'indice Kp géomagnétique NOAA SWPC."""

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
    def parse_kp_json(payload: Any) -> SpaceWeatherStatus:
        """Extrait le dernier indice Kp valide du tableau JSON NOAA SWPC."""
        kp_val = 2.0
        if isinstance(payload, list) and len(payload) >= 2:
            last_row = payload[-1]
            if isinstance(last_row, list) and len(last_row) >= 2:
                try:
                    kp_val = float(last_row[1])
                except (ValueError, TypeError):
                    kp_val = 2.0
            elif isinstance(last_row, dict) and "kp_index" in last_row:
                try:
                    kp_val = float(last_row["kp_index"])
                except (ValueError, TypeError):
                    kp_val = 2.0

        kp_val = round(min(9.0, max(0.0, kp_val)), 1)
        g_level = kp_to_noaa_g_scale(kp_val)
        return SpaceWeatherStatus(
            solar_kp=kp_val,
            solar_storm_level=g_level,
            gps_degraded=bool(kp_val >= 6.0),
            hf_radio_blackout_risk=bool(kp_val >= 5.0),
        )
