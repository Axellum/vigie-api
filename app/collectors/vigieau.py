"""Collector for French drought restriction alerts from VigiEau (api.vigieau.gouv.fr).

VigiEau (https://vigieau.gouv.fr/) publishes prefectural drought restriction zones, decrees,
usages, and departmental summaries for metropolitan France and French overseas departments
(Guadeloupe ``971``, Martinique ``972``, French Guiana ``973``, Réunion ``974``, Mayotte ``976``)
under Licence Ouverte 2.0 (Etalab), provided for information only (prefectural decrees published in
the Recueil des Actes Administratifs remain legally authoritative).

Call sobriety:
- Vigie checks ``CountryIndex`` offline before querying VigiEau on ``/v1/point``, so coordinates
  outside ``FR, GP, MQ, GF, RE, YT`` trigger zero upstream requests.
- Point queries snap coordinates to a ``0.02°`` (~2.2 km) grid and omit ``profil``/``zoneType`` so
  every user profile and water-type filter at the same cell shares one cached upstream answer
  (filtering is applied locally in Python).
- All endpoints share the ``vigieau`` quota (300 requests per 15 minutes per IP per
  ``MTES-MCT/vigieau-api`` ``ThrottlerModule``; held to 90% in ``budget.py``) and a 1-hour cache TTL.
- The 315 MB bulk GeoJSON (``/api/zones/publication``) is never downloaded.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Literal, Optional, cast

import httpx

from app.attribution import DROUGHT, credit_text
from app.collectors.base import BaseCollector, Collected, Fetched
from app.collectors.grid import coord_str, snap_to_grid
from app.models.drought import (
    DroughtAvailability,
    DroughtAvailabilityStatus,
    DroughtDecree,
    DroughtDepartmentSummary,
    DroughtLevel,
    DroughtProfile,
    DroughtStatus,
    DroughtUsageRestriction,
    DroughtWaterType,
    DroughtZone,
    DroughtZoneSummary,
    restrictions_in_force_for,
    worst_drought_level,
)

log = logging.getLogger("vigie.collectors.vigieau")

BASE_URL = "https://api.vigieau.gouv.fr"
TTL_SECONDS = 3600
DEFAULT_GRID_STEP_DEG = 0.02

# ISO 3166-1 alpha-2 codes covered by VigiEau (metropolitan France + 5 overseas departments).
VIGIEAU_COUNTRIES = frozenset({"FR", "GP", "MQ", "GF", "RE", "YT"})

_VALID_LEVELS: frozenset[str] = frozenset({"vigilance", "alerte", "alerte_renforcee", "crise"})
_VALID_WATER_TYPES: frozenset[str] = frozenset({"AEP", "SUP", "SOU"})
_VALID_AVAILABILITY: frozenset[str] = frozenset({"available", "confirmed_none", "unavailable"})

_PROFILE_FIELD: dict[DroughtProfile, str] = {
    "particulier": "concerns_individual",
    "entreprise": "concerns_business",
    "collectivite": "concerns_municipality",
    "exploitation": "concerns_farm",
}

LEGAL_NOTE = (
    "Provided by VigiEau for information only; only prefectural decrees published in the "
    "Recueil des Actes Administratifs (RAA) are legally authoritative."
)


@dataclass(frozen=True)
class VigiEauZonesResult:
    """Parsed upstream ``/api/zones/v2`` response (or structured 409/400 outcome)."""

    status: Literal["available", "confirmed_none", "unavailable", "ambiguous_commune", "invalid_query"]
    zones: tuple[DroughtZone, ...] = ()
    availability: dict[str, DroughtAvailability] = field(default_factory=dict)
    ambiguous_water_types: tuple[str, ...] = ()
    note: Optional[str] = None
    snapped_lat: Optional[float] = None
    snapped_lon: Optional[float] = None


def _as_level(raw: Any) -> Optional[DroughtLevel]:
    if isinstance(raw, str) and raw in _VALID_LEVELS:
        return cast(DroughtLevel, raw)
    return None


def _as_optional_int(raw: Any) -> Optional[int]:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and raw.strip().isdigit():
        return int(raw.strip())
    return None


def _as_optional_str(raw: Any) -> Optional[str]:
    if isinstance(raw, str):
        stripped = raw.strip()
        return stripped if stripped else None
    return None


def _as_optional_bool(raw: Any) -> Optional[bool]:
    return raw if isinstance(raw, bool) else None


def parse_availability(raw: Any) -> dict[str, DroughtAvailability]:
    """Parse VigiEau ``availability`` map (``AEP``, ``SUP``, ``SOU``)."""
    if not isinstance(raw, dict):
        return {}
    parsed: dict[str, DroughtAvailability] = {}
    for water_type, item in raw.items():
        if not isinstance(item, dict):
            continue
        status = item.get("status")
        if not isinstance(status, str) or status not in _VALID_AVAILABILITY:
            continue
        parsed[str(water_type)] = DroughtAvailability(
            status=cast(DroughtAvailabilityStatus, status),
            as_of=_as_optional_str(item.get("asOf")),
            source_revision=_as_optional_str(item.get("sourceRevision")),
            official_url=_as_optional_str(item.get("officialUrl")),
            freshness=_as_optional_str(item.get("freshness")),
            pending_since=_as_optional_str(item.get("pendingSince")),
        )
    return parsed


def _parse_decree(raw: Any) -> Optional[DroughtDecree]:
    if not isinstance(raw, dict):
        return None
    return DroughtDecree(
        id=_as_optional_int(raw.get("id")),
        pdf_url=_as_optional_str(raw.get("cheminFichier")),
        framework_pdf_url=_as_optional_str(raw.get("cheminFichierArreteCadre")),
        valid_from=_as_optional_str(raw.get("dateDebutValidite")),
        valid_until=_as_optional_str(raw.get("dateFinValidite")),
    )


def _parse_usage(raw: Any) -> Optional[DroughtUsageRestriction]:
    if not isinstance(raw, dict):
        return None
    name = _as_optional_str(raw.get("nom"))
    theme = _as_optional_str(raw.get("thematique")) or ""
    if not name:
        return None
    return DroughtUsageRestriction(
        id=_as_optional_int(raw.get("id")),
        name=name,
        theme=theme,
        description=_as_optional_str(raw.get("description")),
        concerns_individual=_as_optional_bool(raw.get("concerneParticulier")),
        concerns_business=_as_optional_bool(raw.get("concerneEntreprise")),
        concerns_municipality=_as_optional_bool(raw.get("concerneCollectivite")),
        concerns_farm=_as_optional_bool(raw.get("concerneExploitation")),
    )


def _parse_zone(raw: Any) -> Optional[DroughtZone]:
    if not isinstance(raw, dict):
        return None
    zone_id = _as_optional_int(raw.get("id"))
    name = _as_optional_str(raw.get("nom"))
    water_type = raw.get("type")
    if zone_id is None or not name or not isinstance(water_type, str) or water_type not in _VALID_WATER_TYPES:
        return None
    usages: list[DroughtUsageRestriction] = []
    for item in raw.get("usages") or []:
        parsed = _parse_usage(item)
        if parsed is not None:
            usages.append(parsed)
    return DroughtZone(
        id=zone_id,
        name=name,
        code=_as_optional_str(raw.get("code")),
        water_type=cast(DroughtWaterType, water_type),
        department=_as_optional_str(raw.get("departement")),
        level=_as_level(raw.get("niveauGravite")),
        influenced_resource=_as_optional_bool(raw.get("ressourceInfluencee")),
        sandre_id=_as_optional_int(raw.get("idSandre")),
        decree=_parse_decree(raw.get("arrete")),
        municipal_decree_url=_as_optional_str(raw.get("arreteMunicipalCheminFichier")),
        usages=usages,
    )


def aggregate_availability_status(
    zones: list[DroughtZone] | tuple[DroughtZone, ...],
    availability: dict[str, DroughtAvailability],
) -> Literal["available", "confirmed_none", "unavailable"]:
    """Derive overall availability status without turning ``unavailable`` into ``confirmed_none``."""
    statuses = {entry.status for entry in availability.values()}
    if zones:
        return "available"
    if "available" in statuses:
        return "available"
    if statuses and statuses == {"confirmed_none"}:
        return "confirmed_none"
    if "confirmed_none" in statuses and "unavailable" not in statuses:
        return "confirmed_none"
    return "unavailable"


def latest_as_of(availability: dict[str, DroughtAvailability]) -> Optional[str]:
    """Return the latest non-null ``as_of`` ISO timestamp from ``availability``."""
    timestamps = [entry.as_of for entry in availability.values() if entry.as_of]
    return max(timestamps) if timestamps else None


def parse_zones_v2_payload(
    payload: Any,
    *,
    snapped_lat: Optional[float] = None,
    snapped_lon: Optional[float] = None,
) -> VigiEauZonesResult:
    """Parse a JSON object from ``GET /api/zones/v2``."""
    if not isinstance(payload, dict):
        raise ValueError("VigiEau /api/zones/v2 returned a non-object JSON payload")
    availability = parse_availability(payload.get("availability"))
    raw_zones = payload.get("zones")
    if raw_zones is None:
        raw_zones = []
    if not isinstance(raw_zones, list):
        raise ValueError("VigiEau /api/zones/v2 'zones' field is not a list")
    zones = tuple(z for item in raw_zones if (z := _parse_zone(item)) is not None)
    status = aggregate_availability_status(zones, availability)
    return VigiEauZonesResult(
        status=status,
        zones=zones,
        availability=availability,
        snapped_lat=snapped_lat,
        snapped_lon=snapped_lon,
    )


def parse_departments_payload(payload: Any) -> tuple[DroughtDepartmentSummary, ...]:
    """Parse the JSON list from ``GET /api/departements``."""
    if not isinstance(payload, list):
        raise ValueError("VigiEau /api/departements returned a non-list JSON payload")
    items: list[DroughtDepartmentSummary] = []
    for raw in payload:
        if not isinstance(raw, dict):
            continue
        code = _as_optional_str(raw.get("code"))
        name = _as_optional_str(raw.get("nom"))
        if not code or not name:
            continue
        max_level = _as_level(raw.get("niveauGraviteMax"))
        items.append(
            DroughtDepartmentSummary(
                code=code,
                name=name,
                region=_as_optional_str(raw.get("region")),
                max_level=max_level,
                surface_level=_as_level(raw.get("niveauGraviteSupMax")),
                groundwater_level=_as_level(raw.get("niveauGraviteSouMax")),
                drinking_water_level=_as_level(raw.get("niveauGraviteAepMax")),
                restrictions_in_force=restrictions_in_force_for(max_level) if raw.get("availability") or max_level is not None else None,
                availability=parse_availability(raw.get("availability")),
            )
        )
    return tuple(items)


def filter_zones(
    zones: tuple[DroughtZone, ...] | list[DroughtZone],
    *,
    water_type: Optional[DroughtWaterType] = None,
    profile: Optional[DroughtProfile] = None,
) -> list[DroughtZone]:
    """Filter parsed zones by ``water_type`` and usage restrictions by ``profile`` locally."""
    out: list[DroughtZone] = []
    attr = _PROFILE_FIELD.get(profile) if profile is not None else None
    for zone in zones:
        if water_type is not None and zone.water_type != water_type:
            continue
        if attr is not None:
            filtered_usages = [u for u in zone.usages if getattr(u, attr) is True]
            out.append(zone.model_copy(update={"usages": filtered_usages}))
        else:
            out.append(zone)
    return out


def to_point_status(result: VigiEauZonesResult) -> DroughtStatus:
    """Compress a full ``VigiEauZonesResult`` into the lightweight block embedded in ``/v1/point``."""
    zones = list(result.zones)
    max_level = worst_drought_level([z.level for z in zones])
    departments = [z.department for z in zones if z.department]
    if result.status == "unavailable":
        status: Literal["available", "confirmed_none", "unavailable"] = "unavailable"
        in_force: Optional[bool] = None
    elif result.status == "confirmed_none":
        status = "confirmed_none"
        in_force = False
    else:
        status = "available"
        in_force = restrictions_in_force_for(max_level)
    summaries = [
        DroughtZoneSummary(
            id=z.id,
            name=z.name,
            water_type=z.water_type,
            level=z.level,
            influenced_resource=z.influenced_resource,
            decree_valid_until=z.decree.valid_until if z.decree else None,
        )
        for z in zones
    ]
    return DroughtStatus(
        status=status,
        max_level=max_level,
        restrictions_in_force=in_force,
        department=departments[0] if departments else None,
        zones=summaries,
        availability=dict(result.availability),
        as_of=latest_as_of(result.availability),
        attribution=credit_text(*DROUGHT),
    )


class VigiEauCollector(BaseCollector[Any]):
    """Sober, grid-cached collector for VigiEau (``api.vigieau.gouv.fr``)."""

    def __init__(
        self,
        *,
        base_url: str = BASE_URL,
        grid_step_deg: float = DEFAULT_GRID_STEP_DEG,
        ttl_seconds: int = TTL_SECONDS,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        super().__init__(
            provider_id="vigieau",
            name="VigiEau (French drought restrictions)",
            domain="drought",
            ttl_seconds=ttl_seconds,
            timeout_seconds=10.0,
            http_client=http_client,
        )
        self.base_url = base_url.rstrip("/")
        self.grid_step_deg = grid_step_deg

    async def fetch_point(self, lat: float, lon: float) -> Fetched[VigiEauZonesResult]:
        """Fetch drought restriction zones for ``(lat, lon)`` snapped to the grid cache cell."""
        slat, slon = snap_to_grid(lat, lon, self.grid_step_deg)
        key = f"pt:{coord_str(slat)},{coord_str(slon)}"
        return await self.fetch_ex(cache_key=key, mode="point", lat=slat, lon=slon)

    async def fetch_commune(self, commune: str) -> Fetched[VigiEauZonesResult]:
        """Fetch drought restriction zones for one French INSEE commune code (e.g. ``75056``)."""
        code = commune.strip().upper()
        key = f"commune:{code}"
        return await self.fetch_ex(cache_key=key, mode="commune", commune=code)

    async def fetch_departments(
        self,
        *,
        date: Optional[str] = None,
        bassin_versant: Optional[str] = None,
    ) -> Fetched[tuple[DroughtDepartmentSummary, ...]]:
        """Fetch the departmental drought summary across France (all 101 departments in one request)."""
        if date is None and bassin_versant is None:
            key = "departments:current"
        else:
            key = f"departments:{date or ''}:{bassin_versant or ''}"
        return await self.fetch_ex(
            cache_key=key,
            mode="departments",
            date=date,
            bassin_versant=bassin_versant,
        )

    async def fetch_point_status(self, lat: float, lon: float) -> DroughtStatus:
        """Return the compact ``DroughtStatus`` block for ``GET /v1/point``."""
        fetched = await self.fetch_point(lat, lon)
        return to_point_status(fetched.value)

    async def _collect(self, client: httpx.AsyncClient, **kwargs: Any) -> Collected[Any]:
        mode = kwargs["mode"]
        headers = {"Accept": "application/json"}
        if mode == "point":
            slat = float(kwargs["lat"])
            slon = float(kwargs["lon"])
            resp = await self._get(
                client,
                f"{self.base_url}/api/zones/v2",
                params={"lat": coord_str(slat), "lon": coord_str(slon)},
                headers=headers,
            )
            resp.raise_for_status()
            parsed = parse_zones_v2_payload(resp.json(), snapped_lat=slat, snapped_lon=slon)
            return Collected(value=parsed, fresh_for_s=float(self.ttl_seconds))

        if mode == "commune":
            commune = str(kwargs["commune"])
            resp = await self._get(
                client,
                f"{self.base_url}/api/zones/v2",
                params={"commune": commune},
                headers=headers,
            )
            if resp.status_code == 409:
                body = resp.json() if resp.content else {}
                zone_types = tuple(str(t) for t in (body.get("zoneTypes") or []))
                msg = _as_optional_str(body.get("message")) or (
                    "This commune spans multiple alert zones of the same water type; query by lat/lon instead."
                )
                return Collected(
                    value=VigiEauZonesResult(
                        status="ambiguous_commune",
                        ambiguous_water_types=zone_types,
                        note=msg,
                    ),
                    fresh_for_s=float(self.ttl_seconds),
                )
            if resp.status_code == 400:
                body = resp.json() if resp.content else {}
                msg = _as_optional_str(body.get("message")) or f"Commune '{commune}' was rejected by VigiEau."
                # Do not trip the circuit breaker on client-supplied unknown/invalid INSEE codes.
                return Collected(
                    value=VigiEauZonesResult(status="invalid_query", note=msg),
                    fresh_for_s=300.0,
                )
            resp.raise_for_status()
            parsed = parse_zones_v2_payload(resp.json())
            return Collected(value=parsed, fresh_for_s=float(self.ttl_seconds))

        if mode == "departments":
            params: dict[str, str] = {}
            if kwargs.get("date"):
                params["date"] = str(kwargs["date"])
            if kwargs.get("bassin_versant"):
                params["bassinVersant"] = str(kwargs["bassin_versant"])
            resp = await self._get(
                client,
                f"{self.base_url}/api/departements",
                params=params or None,
                headers=headers,
            )
            resp.raise_for_status()
            return Collected(value=parse_departments_payload(resp.json()), fresh_for_s=float(self.ttl_seconds))

        raise ValueError(f"Unknown VigiEau collector mode: {mode!r}")
