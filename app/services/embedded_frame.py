"""Générateur de Trame Compacte Embarquée (`GET /v1/embedded/frame`) pour ESP32-P4.

Conçu pour les terminaux Waveshare 7B (1024×600) et M5Stack Tab5 (1280×720 + GPS Grove) :
- Format orienté lignes délimitées par `|` (`HEAD|...`, `GPS|...`, `ZONE|...`, `HAZ|...`, `END|<CRC32>`).
- Décodable en C++ en un seul passage (`strtok_r` / `strtof` / `strtol`) avec **zéro allocation
  dynamique (`malloc`)** dans un tampon statique de 4 à 8 Ko en SRAM/PSRAM.
- Intégrité garantie par une somme de contrôle **CRC32 IEEE 802.3** (8 caractères hexadécimaux
  majuscules) sur tous les octets précédant la ligne `END|`.
"""

from __future__ import annotations

from typing import Optional
import zlib

from app.models.schemas import PointResponse, SnapshotResponse


def _clean_field(text: str, max_len: int = 64) -> str:
    """Nettoie une chaîne pour qu'elle ne contienne ni `|` ni saut de ligne."""
    cleaned = text.replace("|", "/").replace("\r", " ").replace("\n", " ").strip()
    return cleaned[:max_len]


def serialize_embedded_frame(
    snapshot: SnapshotResponse,
    point: Optional[PointResponse] = None,
) -> str:
    """Génère la trame compacte embarquée avec somme de contrôle finale `END|<CRC32>`."""
    lines: list[str] = []
    gs = snapshot.global_status

    # 1. En-tête global (HEAD)
    # HEAD|version|server_utc|device_type|max_sev|red|orange|yellow|kp|g_scale|gps_deg|hf_risk|sim
    lines.append(
        "|".join(
            [
                "HEAD",
                snapshot.version,
                str(snapshot.server_utc),
                snapshot.device_type.value,
                gs.max_severity.value,
                str(gs.active_red_count),
                str(gs.active_orange_count),
                str(gs.active_yellow_count),
                f"{gs.solar_kp:.1f}",
                gs.solar_storm_level,
                "1" if gs.gps_degraded else "0",
                "1" if gs.hf_radio_blackout_risk else "0",
                "1" if snapshot.simulation_active else "0",
            ]
        )
    )

    # 2. Bloc GPS / Marine / Hydrologie / Air optionnel (si lat/lon fournis)
    # GPS|lat|lon|alert_lvl|temp_c|wind_kmh|gust_kmh|press_hpa|rain_1h|wmo|wave_m|sea_state|flood_lvl|aqi_eu|dust_ugm3
    if point is not None:
        wave_m = point.marine.wave_height_m if point.marine else 0.0
        sea_state = point.marine.sea_state_label if point.marine else "NA"
        flood_lvl = point.flood_risk.flood_alert_level.value if point.flood_risk else "GREEN"
        aqi_eu = point.air_quality.european_aqi if point.air_quality else point.current.aqi_eu
        dust_ugm3 = point.air_quality.dust_ugm3 if point.air_quality else 0.0

        lines.append(
            "|".join(
                [
                    "GPS",
                    f"{point.coordinates.lat:.4f}",
                    f"{point.coordinates.lon:.4f}",
                    point.local_alert_level.value,
                    f"{point.current.temp_c:.1f}",
                    f"{point.current.wind_kmh:.1f}",
                    f"{point.current.gust_kmh:.1f}",
                    f"{point.current.pressure_hpa:.1f}",
                    f"{point.current.rain_1h_mm:.2f}",
                    str(point.current.weather_code),
                    f"{wave_m:.2f}",
                    sea_state,
                    flood_lvl,
                    str(aqi_eu),
                    f"{dust_ugm3:.1f}",
                ]
            )
        )

    # 3. Lignes des Zones Mondiales Surveillées (ZONE)
    # ZONE|id|name|lat|lon|local_alert|temp_c|wind_kmh|rain_1h|wmo|aqi_eu
    for z in snapshot.zones:
        lines.append(
            "|".join(
                [
                    "ZONE",
                    _clean_field(z.id, 12),
                    _clean_field(z.name, 32),
                    f"{z.lat:.4f}",
                    f"{z.lon:.4f}",
                    z.local_alert_level.value,
                    f"{z.current.temp_c:.1f}",
                    f"{z.current.wind_kmh:.1f}",
                    f"{z.current.rain_1h_mm:.2f}",
                    str(z.current.weather_code),
                    str(z.current.aqi_eu),
                ]
            )
        )

    # 4. Lignes des Catastrophes Actives (HAZ)
    # HAZ|id|category|severity|lat|lon|val|unit|tsunami|eta_min|dist_km|bearing_deg|cardinal|sources|title
    hazards_source = point.nearby_hazards if point is not None else snapshot.hazards
    for h in hazards_source:
        eta_val = h.tsunami_eta_minutes if h.tsunami_eta_minutes is not None else 0
        dist_val = h.distance_to_zone_km if h.distance_to_zone_km is not None else 0.0
        bear_val = h.bearing_deg if h.bearing_deg is not None else 0.0
        card_val = h.bearing_cardinal or "N"
        sources_str = "+".join(h.sources) if h.sources else h.source

        lines.append(
            "|".join(
                [
                    "HAZ",
                    _clean_field(h.id, 28),
                    h.category.value,
                    h.severity.value,
                    f"{h.lat:.4f}",
                    f"{h.lon:.4f}",
                    f"{h.value_primary:.1f}",
                    _clean_field(h.value_unit, 8),
                    "1" if h.tsunami_warning else "0",
                    str(eta_val),
                    f"{dist_val:.1f}",
                    f"{bear_val:.1f}",
                    _clean_field(card_val, 4),
                    _clean_field(sources_str, 24),
                    _clean_field(h.title, 64),
                ]
            )
        )

    body_with_newline = "\n".join(lines) + "\n"
    crc_int = zlib.crc32(body_with_newline.encode("utf-8")) & 0xFFFFFFFF
    return f"{body_with_newline}END|{crc_int:08X}\n"


def verify_embedded_frame_crc(frame_text: str) -> bool:
    """Vérifie la somme de contrôle CRC32 d'une trame embarquée `HEAD...END|<CRC32>`."""
    marker = "\nEND|"
    idx = frame_text.rfind(marker)
    if idx == -1:
        return False
    body = frame_text[: idx + 1]  # inclut le '\n' juste avant END|
    trailer = frame_text[idx + len(marker) :].strip()
    if len(trailer) != 8:
        return False
    expected_crc = zlib.crc32(body.encode("utf-8")) & 0xFFFFFFFF
    return trailer.upper() == f"{expected_crc:08X}"
