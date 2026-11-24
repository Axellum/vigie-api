"""Météo-France AROME-PI: hail, gusts, reduced visibility and type of precipitation for the next hours, read through a gateway (optional).

AROME-PI is Météo-France's "prévision immédiate" model: a run every hour (published about 20 minutes after its hour), steps of 15 minutes
up to 6 hours, 1.3 km. Its WCS (``/public/aromepi/1.0``) answers one step of one parameter per request, so this API does not read it: the
gateway (``Axellum/vigie-meteo``, ``GET /v1/shortterm?lat=&lon=``, read on 2026-11-18) downloads each run once (4 parameters x 24 steps),
keeps a map of France reduced to blocks of 0.04 degree (the worst value of each block) and answers a point in a few milliseconds.

This collector asks the gateway for a point (cached per cell of the forecast grid, five minutes), refuses an answer that is not AROME-PI's or
that has an impossible value, and turns the steps into levels (:func:`summarize`). The thresholds of the levels are written below, with
who set them: the hail ones are Météo-France's (the diagnostic of its own type-of-precipitation code), the others are ours. What the model
forecasts is a forecast: it is not an official warning and never enters ``local_alert_level``.

Codes of the type of precipitation (Météo-France, "Comprendre les diagnostics de type de précipitation dans les modèles AROME", read on
2026-11-18): the WMO table 4.201 (1 rain, 3 freezing precipitation, 5 dry snow, 6 wet snow, 7 rain and snow, 8 ice pellets, 9 snow pellets,
10 hail, 11 drizzle, 12 freezing drizzle) and local codes (193 wet snow, 201 to 213: the same, "intermittent"). A code this table does not
list is ``unknown``, never guessed.

Without ``VIGIE_METEO_GATEWAY_URL`` there is no collector and the point carries no ``short_term``.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Optional

import httpx

from app import clock
from app.attribution import credit_text, merge_credits
from app.collectors.base import SELF_HOSTED_CONNECTION, BaseCollector
from app.collectors.grid import DEFAULT_FORECAST_GRID_DEG, coord_str, snap_to_grid
from app.models.shortterm import (
    GustSummary,
    HailSummary,
    PrecipitationSpan,
    ShortTerm,
    ShortTermStep,
    VisibilitySummary,
)

PROVIDER_ID = "meteofrance_shortterm"
SOURCE_MODEL = "meteofrance-arome-pi"
TTL_SECONDS = 300
STEP_S = 900
# Levels. The hail thresholds are Météo-France's: its diagnostic is "small hail" (code 9) from 8 kg/m2 and "hail" (code 10) from 16.
HAIL_SMALL_KG_M2 = 8.0
HAIL_KG_M2 = 16.0
# Ours: a gust of 60 km/h is felt on foot and moves loose objects; 90 km/h breaks branches and roof tiles. Not Météo-France's wind warning
# levels, which depend on the department.
GUST_STRONG_KMH = 60
GUST_VERY_STRONG_KMH = 90
# Ours: fog is a visibility under 1 km (the usual meteorological definition), under 200 m it is dense.
VISIBILITY_REDUCED_M = 1000
VISIBILITY_VERY_REDUCED_M = 200

PRECIPITATION_KINDS: dict[int, str] = {
    0: "none",
    1: "rain",
    3: "freezing",
    5: "snow",
    6: "wet_snow",
    7: "rain_snow",
    8: "ice_pellets",
    9: "snow_pellets",
    10: "hail",
    11: "drizzle",
    12: "freezing",
    193: "wet_snow",
    201: "rain",
    205: "snow",
    206: "wet_snow",
    207: "rain_snow",
    213: "wet_snow",
}

NOTE = (
    "The forecast of Météo-France's AROME-PI model for the next hours, worked out into levels by this API (the thresholds are in the "
    "descriptions of the fields). It is a model forecast, not an official warning: it is never part of local_alert_level, and the warnings of "
    "the national services are the nearby_hazards. Each value is the worst of a block of about 4 x 3 km around the point."
)


def precipitation_kind(code: int) -> str:
    """The kind of a Météo-France precipitation code; ``unknown`` for a code the table does not list."""
    return PRECIPITATION_KINDS.get(code, "unknown")


def _epoch(value: Any) -> int:
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        raise ValueError("time without a time zone")
    return int(moment.timestamp())


def _int(value: Any, low: int, high: int, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{what} is missing or out of range")
    return int(value)


class ShortTermRaw:
    """The steps of a gateway answer, checked: ``run_utc`` and, per step, ``(end_utc, gust_kmh, hail_kg_m2, visibility_m, code)``."""

    def __init__(self, run_utc: int, steps: tuple[tuple[int, int, float, int, int], ...]) -> None:
        self.run_utc = run_utc
        self.steps = steps


def parse_gateway_shortterm(payload: Any) -> ShortTermRaw:
    """The steps of a gateway answer. ``ValueError`` when it is not AROME-PI's or a value cannot be read or is impossible."""
    if not isinstance(payload, dict):
        raise ValueError("gateway answer is not an object")
    model = payload.get("source_model")
    if model != SOURCE_MODEL:
        raise ValueError(f"gateway answer is not AROME-PI (source_model={str(model)[:40]!r})")
    items = payload.get("steps")
    if not isinstance(items, list) or not items:
        raise ValueError("gateway answer without steps")
    run_utc = _epoch(payload.get("run"))
    by_end: dict[int, tuple[int, int, float, int, int]] = {}
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("gateway step is not an object")
        end = _epoch(item.get("time"))
        hail = item.get("hail_kg_m2")
        if isinstance(hail, bool) or not isinstance(hail, (int, float)) or not math.isfinite(hail) or not 0.0 <= hail <= 1000.0:
            raise ValueError("gateway step without a valid hail diagnostic")
        by_end[end] = (
            end,
            _int(item.get("wind_gust_kmh"), 0, 400, "gust"),
            round(float(hail), 1),
            _int(item.get("visibility_m"), 0, 100_000, "visibility"),
            _int(item.get("precip_type"), 0, 9999, "precipitation type"),
        )
    return ShortTermRaw(run_utc, tuple(by_end[end] for end in sorted(by_end)))


def summarize(raw: ShortTermRaw, now_s: float) -> Optional[ShortTerm]:
    """The levels of the steps that are not entirely past at ``now_s``; ``None`` when none is left."""
    rows = [row for row in raw.steps if row[0] >= now_s]
    if not rows:
        return None
    steps = [
        ShortTermStep(
            time_utc=end,
            wind_gust_kmh=gust,
            hail_kg_m2=hail,
            visibility_m=visibility,
            precipitation=precipitation_kind(code),  # type: ignore[arg-type]
        )
        for end, gust, hail, visibility, code in rows
    ]

    peak = max(steps, key=lambda s: s.wind_gust_kmh)
    gust_level = (
        "very_strong" if peak.wind_gust_kmh >= GUST_VERY_STRONG_KMH else "strong" if peak.wind_gust_kmh >= GUST_STRONG_KMH else "none"
    )
    hail_peak = max(steps, key=lambda s: s.hail_kg_m2)
    first_hail = next((s.time_utc for s in steps if s.hail_kg_m2 >= HAIL_SMALL_KG_M2), None)
    hail_level = "hail" if hail_peak.hail_kg_m2 >= HAIL_KG_M2 else "small_hail" if hail_peak.hail_kg_m2 >= HAIL_SMALL_KG_M2 else "none"
    low = min(steps, key=lambda s: s.visibility_m)
    first_low = next((s.time_utc for s in steps if s.visibility_m < VISIBILITY_REDUCED_M), None)
    visibility_level = (
        "very_reduced" if low.visibility_m < VISIBILITY_VERY_REDUCED_M else "reduced" if low.visibility_m < VISIBILITY_REDUCED_M else "none"
    )

    spans: dict[str, list[int]] = {}
    for step in steps:
        if step.precipitation != "none":
            spans.setdefault(step.precipitation, []).append(step.time_utc)

    return ShortTerm(
        run_utc=raw.run_utc,
        horizon_minutes=max(0, int(round((steps[-1].time_utc - now_s) / 60.0))),
        steps=steps,
        gusts=GustSummary(level=gust_level, max_kmh=peak.wind_gust_kmh, at_utc=peak.time_utc if gust_level != "none" else None),  # type: ignore[arg-type]
        hail=HailSummary(level=hail_level, max_kg_m2=hail_peak.hail_kg_m2, first_at_utc=first_hail),  # type: ignore[arg-type]
        visibility=VisibilitySummary(level=visibility_level, min_m=low.visibility_m, first_at_utc=first_low),  # type: ignore[arg-type]
        precipitation=[PrecipitationSpan(kind=kind, first_at_utc=ends[0], last_at_utc=ends[-1]) for kind, ends in spans.items()],  # type: ignore[arg-type]
        attribution=merge_credits(credit_text(PROVIDER_ID)),
        note=NOTE,
    )


class MeteoFranceShortTermCollector(BaseCollector[ShortTermRaw]):
    """The AROME-PI steps of a point through the gateway, cached per cell of the forecast grid."""

    def __init__(
        self,
        base_url: str,
        grid_deg: float = DEFAULT_FORECAST_GRID_DEG,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        super().__init__(
            provider_id=PROVIDER_ID,
            name="Météo-France AROME-PI short-term forecast (vigie-meteo gateway)",
            domain="weather",
            ttl_seconds=TTL_SECONDS,
            failure_threshold=3,
            recovery_timeout_seconds=120,
            timeout_seconds=8.0,
            http_client=http_client,
            connection=SELF_HOSTED_CONNECTION,
        )
        self.base_url = base_url.rstrip("/")
        self.grid_deg = grid_deg

    async def _collect(self, client: httpx.AsyncClient, **kwargs: Any) -> ShortTermRaw:
        kwargs.pop("previous", None)
        lat, lon = float(kwargs["lat"]), float(kwargs["lon"])
        response = await self._get(
            client, f"{self.base_url}/v1/shortterm", params={"lat": coord_str(lat), "lon": coord_str(lon)}
        )
        if response.status_code == 404:
            return ShortTermRaw(0, ())  # a point outside the model's map: nothing to show, not a failure of the source
        response.raise_for_status()
        return parse_gateway_shortterm(response.json())

    async def short_term(self, lat: float, lon: float) -> Optional[ShortTerm]:
        """The levels for the next hours at a point, or ``None`` (outside the map, gateway down, run too old, no step left)."""
        s_lat, s_lon = snap_to_grid(lat, lon, self.grid_deg)
        try:
            raw = await self.fetch(f"shortterm:{coord_str(s_lat)},{coord_str(s_lon)}", lat=s_lat, lon=s_lon)
        except Exception:
            return None
        # An expired entry is fine: the steps are checked against the clock, a run too old leaves none.
        return summarize(raw, clock.wall())
