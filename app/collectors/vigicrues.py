"""Vigicrues collector: the official river flood vigilance of France (SCHAPI) as ``Vigilance`` objects.

Vigicrues (https://www.vigicrues.gouv.fr/), the flood information service of the French State run by the SCHAPI, gives a
colour to each **section** (tronçon) of the rivers it watches: green, yellow, orange or red, for the hours or days to
come. One public GeoJSON flow carries all of them, with the line of every section, without a key:

``https://www.vigicrues.gouv.fr/services/InfoVigiCru.geojson`` (documented at https://www.vigicrues.gouv.fr/services/v1.1,
section "GeoJson", as ``/services/1/InfoVigiCru.geojson``, which redirects to the address above). Its envelope holds
``RefInfoVigiCru`` (the reference of the bulletin, ``20102026_10``) and ``DtHrInfoVigiCru`` (when it was published); each
feature is a section with ``CdEntCru`` (its code), ``lbentcru`` (its name), ``cdensup_1`` (the code of its territory, a
Service de prévision des crues area), ``CdTCC`` (the basin) and ``NivInfViCr`` (the level, 1 to 4).

The levels: 1 green, 2 yellow, 3 orange, 4 red. The flow itself carries only the number; the colours are the ones of the
official local bulletins, which were compared on 2026-10-20 (the sections Vistre and Vidourle at level 3 are "orange" in
the bulletin of the territory Grand Delta, the Gardon d'Alès at level 2 is "Jaune"). **A green section is no warning**: it
is not returned (it is counted in the coverage).

The dataset (https://www.data.gouv.fr/datasets/troncons-de-cours-deau-vigicrues-simplifies-avec-niveau-de-vigilance-crues-2)
says the flow is provided at each publication of the flood vigilance, twice a day (10 a.m. and 4 p.m.) outside floods
and more often during them. The server sends ``Cache-Control: max-age=120``, no ``ETag``, no ``Last-Modified``, and does
not compress: **no conditional request is possible and one answer is 2.2 MB** (337 sections). So the flow is read once
per refresh for all of France, and cached 30 minutes, or 10 minutes while a section is orange or red (the exact rhythm
of the extra bulletins is not published: not verified).

A flood vigilance applies to a river section, a line: a point is matched when it lies within a radius of it
(``river_radius_km`` of the query, 5 km by default, see ``docs/data-sources.md``).
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Optional

import httpx
from shapely.geometry import shape

from app.attribution import credit_text
from app.collectors.base import BaseCollector, Collected, Fetched, parse_iso_utc
from app.models.schemas import SeverityLevel
from app.models.vigilance import (
    Vigilance,
    VigilanceArea,
    VigilanceGeocode,
    VigilancePhenomenon,
)

FLOW_URL = "https://www.vigicrues.gouv.fr/services/InfoVigiCru.geojson"
TERRITORY_URL = "https://www.vigicrues.gouv.fr/bulletin_local/{territory}"
MAX_FEATURES = 5_000  # the flow has 337 sections; thousands is a mistake

TTL_CALM_S = 1800.0
TTL_FLOOD_S = 600.0

# ``NivInfViCr`` -> our level. 1 is green (no particular vigilance): kept in the table so that it is known, never returned.
LEVELS: dict[int, SeverityLevel] = {
    1: SeverityLevel.GREEN,
    2: SeverityLevel.YELLOW,
    3: SeverityLevel.ORANGE,
    4: SeverityLevel.RED,
}


@dataclass(frozen=True)
class VigicruesFlow:
    """The river sections above green, and how many sections the flow holds."""

    updated_utc: Optional[int]  # DtHrInfoVigiCru: when the vigilance was published
    reference: Optional[str]  # RefInfoVigiCru
    vigilances: tuple[Vigilance, ...]
    sections: int = 0  # all the sections of the flow, green ones included
    skipped: int = 0  # sections above green that could not be read (no valid line or level)


def derive_level(value: Any) -> Optional[SeverityLevel]:
    """Our level for ``NivInfViCr`` (``None`` if it is not 1 to 4)."""
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    try:
        return LEVELS.get(int(value))
    except ValueError:
        return None


def _line(geometry: Any) -> Any:
    """The shapely line of a GeoJSON LineString / MultiLineString; ``None`` for anything else or a broken one."""
    if not isinstance(geometry, dict) or geometry.get("type") not in ("LineString", "MultiLineString"):
        return None
    try:
        line = shape(geometry)
    except (ValueError, TypeError, KeyError, IndexError, AttributeError):
        return None
    return None if line.is_empty else line


def parse_flow(payload: Any) -> VigicruesFlow:
    """Parse the flow: the sections above green as warnings. All green is a valid answer (nothing in force)."""
    features = payload.get("features") if isinstance(payload, dict) else None
    if not isinstance(features, list) or len(features) > MAX_FEATURES:
        raise ValueError("Vigicrues answer has no usable 'features' list")
    updated_text = payload.get("DtHrInfoVigiCru") if isinstance(payload.get("DtHrInfoVigiCru"), str) else None
    reference = payload.get("RefInfoVigiCru") if isinstance(payload.get("RefInfoVigiCru"), str) else None
    updated = parse_iso_utc(updated_text)
    credit = credit_text("vigicrues")
    attribution = f"{credit} | Last update of the flow: {updated_text}" if updated_text else credit

    vigilances: list[Vigilance] = []
    skipped = 0
    for feature in features:
        props = feature.get("properties") if isinstance(feature, dict) else None
        if not isinstance(props, dict):
            skipped += 1
            continue
        level = derive_level(props.get("NivInfViCr"))
        if level is SeverityLevel.GREEN:
            continue
        code = props.get("CdEntCru")
        line = _line(feature.get("geometry"))
        if level is None or not isinstance(code, str) or not code or line is None:
            skipped += 1
            continue
        territory = props.get("cdensup_1")
        basin = props.get("CdTCC")
        geocodes = [VigilanceGeocode(scheme="VIGICRUES_SECTION", value=code)]
        if territory not in (None, ""):
            geocodes.append(VigilanceGeocode(scheme="VIGICRUES_TERRITORY", value=str(territory)))
        if basin not in (None, ""):
            geocodes.append(VigilanceGeocode(scheme="VIGICRUES_BASIN", value=str(basin)))
        name = props.get("lbentcru")
        vigilance = Vigilance(
            id=f"vigicrues:{code}",
            source="vigicrues",
            country="FR",
            attribution=attribution,
            identifier=f"{reference}:{code}" if reference else code,
            sent_utc=updated,
            level=level,
            level_original=str(props.get("NivInfViCr")),
            phenomenon=VigilancePhenomenon.FLOOD,
            area=VigilanceArea(
                description=name if isinstance(name, str) else None,
                geocodes=geocodes,
                has_polygon=False,
            ),
            derived=["level", "phenomenon"],  # the flow has no message type: the status is not derived, it is unknown
            link=FLOW_URL,
            web=(TERRITORY_URL.format(territory=territory) + f"#{code}") if territory not in (None, "") else None,
        )
        vigilances.append(vigilance.with_shape(line))
    return VigicruesFlow(
        updated_utc=updated,
        reference=reference,
        vigilances=tuple(vigilances),
        sections=len(features),
        skipped=skipped,
    )


class VigicruesCollector(BaseCollector[VigicruesFlow]):
    """The flood vigilance of France: one request for all the sections, cached 30 minutes (10 while a flood is on)."""

    def __init__(self, http_client: Optional[httpx.AsyncClient] = None) -> None:
        super().__init__(
            provider_id="vigicrues_flow",
            name="Vigicrues flood vigilance (SCHAPI)",
            domain="warnings",
            ttl_seconds=int(TTL_CALM_S),
            timeout_seconds=20.0,  # 2.2 MB, uncompressed
            http_client=http_client,
        )

    async def fetch_flow(self, allow_stale_on_error: bool = True) -> Fetched[VigicruesFlow]:
        """The sections above green, from the cache while it is fresh, else one request."""
        return await self.fetch_ex(cache_key="flow", allow_stale_on_error=allow_stale_on_error)

    async def _collect(self, client: httpx.AsyncClient, **kwargs: Any) -> Collected[VigicruesFlow]:
        resp = await self._get(client, FLOW_URL, headers={"Accept": "application/geo+json, application/json;q=0.9"})
        resp.raise_for_status()
        content = resp.content
        flow = await asyncio.to_thread(lambda: parse_flow(json.loads(content)))
        flood = any(v.level in (SeverityLevel.ORANGE, SeverityLevel.RED) for v in flow.vigilances)
        return Collected(flow, fresh_for_s=TTL_FLOOD_S if flood else TTL_CALM_S)
