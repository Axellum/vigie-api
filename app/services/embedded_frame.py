"""Générateur de Trame Compacte Embarquée (`GET /v1/embedded/frame`) pour ESP32-P4.

Aligné à 100 % avec le décodeur C++ `vigie_decoder_trame_api` du socle matériel
`alerte_mondiale_7b` (Waveshare 7B 1024×600 & M5Stack Tab5 1280×720 + GPS Grove) :
- Format orienté lignes délimitées par `|` (`HEAD|...`, `ZONE|...`, `RAIN5M|...`, `DAY|...`, `HAZ|...`, `END|<CRC32>`).
- Décodable en C++ en un seul passage (`strtok_r` / `strtof` / `strtol`) avec **zéro allocation
  dynamique (`malloc`)** dans un tampon statique en SRAM/PSRAM.
- Intégrité garantie par une somme de contrôle **CRC32 IEEE 802.3** (8 caractères hexadécimaux
  majuscules) sur tous les octets précédant la ligne `END|`.
"""

from __future__ import annotations

from typing import Iterable, Optional
import zlib

from app.models.schemas import DailyForecast, PointResponse, SnapshotResponse


def _clean_field(text: str, max_len: int = 64) -> str:
    """Nettoie une chaîne pour qu'elle ne contienne ni `|` ni virgule parasite ni saut de ligne."""
    cleaned = text.replace("|", "/").replace("\r", " ").replace("\n", " ").strip()
    return cleaned[:max_len]


def _normalize_rain_15m(values: Iterable[float]) -> list[float]:
    """Garantit exactement 4 valeurs de précipitations aux 15 minutes."""
    arr = [max(0.0, float(v)) for v in list(values)[:4]]
    while len(arr) < 4:
        arr.append(0.0)
    return arr


def _compute_rain_eta_and_max_mmh(c15: list[float]) -> tuple[int, float]:
    """Calcule le délai avant la pluie en minutes (-1 si sec) et l'intensité max en mm/h."""
    eta_min = -1
    for idx, val in enumerate(c15):
        if val > 0.05:
            eta_min = idx * 15
            break
    max_mmh = round(max(c15) * 4.0, 1) if c15 else 0.0
    return eta_min, max_mmh


def _interpolate_rain_5m_from_15m(c15: list[float]) -> list[float]:
    """Interpole 12 pas de 5 minutes (sur 60 min) à partir des 4 tranches de 15 minutes."""
    steps_5m: list[float] = []
    for idx in range(4):
        curr_mmh = c15[idx] * 4.0
        next_mmh = c15[idx + 1] * 4.0 if idx + 1 < 4 else curr_mmh * 0.8
        steps_5m.append(round(curr_mmh, 2))
        steps_5m.append(round((2.0 * curr_mmh + next_mmh) / 3.0, 2))
        steps_5m.append(round((curr_mmh + 2.0 * next_mmh) / 3.0, 2))
    return steps_5m[:12]


def _emit_zone_block(
    lines: list[str],
    zone_id: str,
    zone_name: str,
    lat: float,
    lon: float,
    utc_offset_s: int,
    temp_c: float,
    feels_like_c: float,
    humidity_pct: int,
    pressure_hpa: float,
    wind_kmh: float,
    gust_kmh: float,
    wind_deg: int,
    weather_code: int,
    uv_index: float,
    aqi_eu: int,
    rain_next_60m: list[float],
    daily_10d: list[DailyForecast],
    wave_m: float = 0.0,
    wave_period_s: float = 0.0,
    wave_dir_deg: int = 0,
    swell_m: float = 0.0,
    current_knots: float = 0.0,
) -> None:
    """Émet les lignes `ZONE|...`, `RAIN5M|...` et `DAY|...` pour une zone donnée."""
    zid = _clean_field(zone_id, 12)
    zname = _clean_field(zone_name, 32)
    c15 = _normalize_rain_15m(rain_next_60m)
    pluie_dans_min, intensite_max_mmh = _compute_rain_eta_and_max_mmh(c15)
    c15_csv = ",".join(f"{v:.2f}" for v in c15)

    # Ligne ZONE (24 colonnes exactes après le tag ZONE)
    lines.append(
        "|".join(
            [
                "ZONE",
                zid,
                zname,
                f"{lat:.4f}",
                f"{lon:.4f}",
                str(int(utc_offset_s)),
                f"{temp_c:.1f}",
                f"{feels_like_c:.1f}",
                str(int(humidity_pct)),
                f"{pressure_hpa:.1f}",
                f"{wind_kmh:.1f}",
                f"{gust_kmh:.1f}",
                str(int(wind_deg)),
                str(int(weather_code)),
                f"{uv_index:.1f}",
                str(int(aqi_eu)),
                str(pluie_dans_min),
                f"{intensite_max_mmh:.1f}",
                c15_csv,
                f"{wave_m:.2f}",
                f"{wave_period_s:.1f}",
                str(int(wave_dir_deg)),
                f"{swell_m:.2f}",
                f"{current_knots:.2f}",
            ]
        )
    )

    # Ligne RAIN5M (12 valeurs sur 60 min au pas de 5 min)
    r5m = _interpolate_rain_5m_from_15m(c15)
    r5m_csv = ",".join(f"{v:.2f}" for v in r5m)
    lines.append(f"RAIN5M|{zid}|{r5m_csv}")

    # Lignes DAY pour les prévisions journalières
    for day in daily_10d:
        rain_prob_pct = min(100, max(15, int(round(day.rain_mm * 14.0)))) if day.rain_mm > 0.05 else 5
        lines.append(
            "|".join(
                [
                    "DAY",
                    zid,
                    _clean_field(day.d, 8),
                    str(int(day.code)),
                    f"{day.tmin:.1f}",
                    f"{day.tmax:.1f}",
                    f"{day.rain_mm:.1f}",
                    str(rain_prob_pct),
                    f"{day.wind_max:.1f}",
                    f"{uv_index:.1f}",
                ]
            )
        )


def serialize_embedded_frame(
    snapshot: SnapshotResponse,
    point: Optional[PointResponse] = None,
) -> str:
    """Génère la trame C++ compacte alignée avec `vigie_decoder_trame_api` et signée par `END|<CRC32>`."""
    lines: list[str] = []
    gs = snapshot.global_status

    # 1. En-tête global (HEAD)
    # HEAD|<server_utc>|<solar_kp:.1f>|0|<version>|<device_type>|<max_severity>|<red_count>|<orange_count>|<yellow_count>|<gps_degraded_0_1>|<hf_risk_0_1>|<sim_0_1>
    lines.append(
        "|".join(
            [
                "HEAD",
                str(snapshot.server_utc),
                f"{gs.solar_kp:.1f}",
                "0",
                snapshot.version,
                snapshot.device_type.value,
                gs.max_severity.value,
                str(gs.active_red_count),
                str(gs.active_orange_count),
                str(gs.active_yellow_count),
                "1" if gs.gps_degraded else "0",
                "1" if gs.hf_radio_blackout_risk else "0",
                "1" if snapshot.simulation_active else "0",
            ]
        )
    )

    # 2. Si `point` (coordonnées GPS / Bateau) est fourni, l'insérer en première zone (`id="GPS"`)
    if point is not None:
        wave_m = point.marine.wave_height_m if point.marine else 0.0
        wave_per = point.marine.wave_period_s if point.marine else 0.0
        wave_dir = point.marine.wave_direction_deg if point.marine else 0
        swell_m = point.marine.swell_wave_height_m if point.marine else 0.0
        curr_knots = (
            round(point.marine.ocean_current_velocity_kmh / 1.852, 2)
            if point.marine
            else 0.0
        )
        utc_off = int(round(point.coordinates.lon / 15.0) * 3600)
        gps_daily = snapshot.zones[0].daily_10d if snapshot.zones else []

        _emit_zone_block(
            lines=lines,
            zone_id="GPS",
            zone_name="Position GPS / Bateau",
            lat=point.coordinates.lat,
            lon=point.coordinates.lon,
            utc_offset_s=utc_off,
            temp_c=point.current.temp_c,
            feels_like_c=point.current.feels_like_c,
            humidity_pct=point.current.humidity_pct,
            pressure_hpa=point.current.pressure_hpa,
            wind_kmh=point.current.wind_kmh,
            gust_kmh=point.current.gust_kmh,
            wind_deg=point.current.wind_deg,
            weather_code=point.current.weather_code,
            uv_index=point.current.uv_index,
            aqi_eu=point.current.aqi_eu,
            rain_next_60m=point.rain_nowcast.mm_per_step,
            daily_10d=gps_daily,
            wave_m=wave_m,
            wave_period_s=wave_per,
            wave_dir_deg=wave_dir,
            swell_m=swell_m,
            current_knots=curr_knots,
        )

    # 3. Lignes des Zones Mondiales Surveillées (ZONE + RAIN5M + DAY)
    for z in snapshot.zones:
        _emit_zone_block(
            lines=lines,
            zone_id=z.id,
            zone_name=z.name,
            lat=z.lat,
            lon=z.lon,
            utc_offset_s=z.utc_offset_s,
            temp_c=z.current.temp_c,
            feels_like_c=z.current.feels_like_c,
            humidity_pct=z.current.humidity_pct,
            pressure_hpa=z.current.pressure_hpa,
            wind_kmh=z.current.wind_kmh,
            gust_kmh=z.current.gust_kmh,
            wind_deg=z.current.wind_deg,
            weather_code=z.current.weather_code,
            uv_index=z.current.uv_index,
            aqi_eu=z.current.aqi_eu,
            rain_next_60m=z.rain_next_60m,
            daily_10d=z.daily_10d,
        )

    # 4. Lignes des Catastrophes Actives (HAZ)
    # HAZ|<id>|<category>|<severity>|<lat:.4f>|<lon:.4f>|<value_primary:.1f>|<depth_km:.1f>|<tsunami_0_1>|<timestamp_utc>|<sources_plus>|<title>
    hazards_source = point.nearby_hazards if point is not None else snapshot.hazards
    for h in hazards_source:
        depth_val = h.depth_km if h.depth_km is not None else 0.0
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
                    f"{depth_val:.1f}",
                    "1" if h.tsunami_warning else "0",
                    str(int(h.timestamp_utc)),
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
