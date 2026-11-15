"""Météo-France official weather warnings: ``DPVigilance/v1`` on ``portail-api.meteofrance.fr``.

Reads the current vigilance map (``/cartevigilance/encours``) and the accompanying bulletins
(``/textesvigilance/encours``), published under **Licence Ouverte 2.0 (Etalab)**
(https://www.data.gouv.fr/datasets/675bf4442d16a89b0a767b24).

Why two endpoints and what they hold:

- ``/cartevigilance/encours`` gives the vigilance colour (``1`` green, ``2`` yellow, ``3`` orange, ``4`` red) per department
  (``01`` to ``95``, ``2A``, ``2B``) and per coastal strip (``<dep>10``, e.g. ``6410`` for ``Pyrénées-Atlantiques``, where
  phenomenon ``9`` ``Vagues-submersion`` is published), for today (``J``) and tomorrow (``J1``), with the sub-daily time slots
  (``timelaps_items``) of each phenomenon.
- ``/textesvigilance/encours`` gives the follow-up bulletins written by Météo-France's forecasters (``text_bloc_items``), untouched.
  If the text endpoint fails while the map answers, the warnings are still served with their time slots.

Every metropolitan department is given its ``NUTS3`` geocode (from ``app/data/geocodes-nuts.geojson.gz``) alongside its
``INSEE_DEP`` code, so that ``PointLocator`` matches any GPS position in France offline and ``VigilanceService`` deduplicates
department-wide river-flood warnings (phenomenon ``4``, ``Crues``) when a finer Vigicrues river section covers the department
at the same or a higher level.

Requires ``VIGIE_METEOFRANCE_API_KEY`` (sent in the ``apikey`` header, never in a URL, and redacted in every error and log line).
Without it there is no collector and ``VigilanceService`` uses ``MeteoAlarm`` for France as before.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from app.attribution import credit_text
from app.collectors.base import BaseCollector
from app.models.schemas import SeverityLevel
from app.models.vigilance import (
    Vigilance,
    VigilanceArea,
    VigilanceGeocode,
    VigilanceParameter,
    VigilancePhenomenon,
    VigilanceStatus,
    VigilanceText,
)

MAP_URL = "https://public-api.meteofrance.fr/public/DPVigilance/v1/cartevigilance/encours"
TEXTS_URL = "https://public-api.meteofrance.fr/public/DPVigilance/v1/textesvigilance/encours"
WEB_URL = "https://vigilance.meteofrance.fr/fr"
KEY_ENV = "VIGIE_METEOFRANCE_API_KEY"
TTL_SECONDS = 180
ISSUER = "Météo-France"
_KEY_SHAPE = re.compile(r"^[A-Za-z0-9._\-]{16,}$")

# Météo-France colour id -> (French label as published, derived SeverityLevel). Colour 1 is green (no warning).
COLORS: dict[int, tuple[str, SeverityLevel]] = {
    2: ("Jaune", SeverityLevel.YELLOW),
    3: ("Orange", SeverityLevel.ORANGE),
    4: ("Rouge", SeverityLevel.RED),
}

# Météo-France phenomenon id -> (French label as published, derived VigilancePhenomenon).
PHENOMENA: dict[str, tuple[str, VigilancePhenomenon]] = {
    "1": ("Vent", VigilancePhenomenon.WIND),
    "2": ("Pluie-inondation", VigilancePhenomenon.RAIN),
    "3": ("Orages", VigilancePhenomenon.THUNDERSTORM),
    "4": ("Crues", VigilancePhenomenon.FLOOD),
    "5": ("Neige-verglas", VigilancePhenomenon.SNOW_ICE),
    "6": ("Canicule", VigilancePhenomenon.HEAT),
    "7": ("Grand-froid", VigilancePhenomenon.COLD),
    "8": ("Avalanches", VigilancePhenomenon.AVALANCHE),
    "9": ("Vagues-submersion", VigilancePhenomenon.COASTAL),
}

# INSEE department code -> (NUTS3 code in app/data/geocodes-nuts.geojson.gz, department name).
DEPARTMENTS: dict[str, tuple[str, str]] = {
    "01": ("FR711", "Ain"),
    "02": ("FR221", "Aisne"),
    "03": ("FR721", "Allier"),
    "04": ("FR821", "Alpes-de-Haute-Provence"),
    "05": ("FR822", "Hautes-Alpes"),
    "06": ("FR823", "Alpes-Maritimes"),
    "07": ("FR712", "Ardèche"),
    "08": ("FR211", "Ardennes"),
    "09": ("FR621", "Ariège"),
    "10": ("FR212", "Aube"),
    "11": ("FR811", "Aude"),
    "12": ("FR622", "Aveyron"),
    "13": ("FR824", "Bouches-du-Rhône"),
    "14": ("FR251", "Calvados"),
    "15": ("FR722", "Cantal"),
    "16": ("FR531", "Charente"),
    "17": ("FR532", "Charente-Maritime"),
    "18": ("FR241", "Cher"),
    "19": ("FR631", "Corrèze"),
    "21": ("FR261", "Côte-d'Or"),
    "22": ("FR521", "Côtes-d'Armor"),
    "23": ("FR632", "Creuse"),
    "24": ("FR611", "Dordogne"),
    "25": ("FR431", "Doubs"),
    "26": ("FR713", "Drôme"),
    "27": ("FR231", "Eure"),
    "28": ("FR242", "Eure-et-Loir"),
    "29": ("FR522", "Finistère"),
    "2A": ("FR831", "Corse-du-Sud"),
    "2B": ("FR832", "Haute-Corse"),
    "30": ("FR812", "Gard"),
    "31": ("FR623", "Haute-Garonne"),
    "32": ("FR624", "Gers"),
    "33": ("FR612", "Gironde"),
    "34": ("FR813", "Hérault"),
    "35": ("FR523", "Ille-et-Vilaine"),
    "36": ("FR243", "Indre"),
    "37": ("FR244", "Indre-et-Loire"),
    "38": ("FR714", "Isère"),
    "39": ("FR432", "Jura"),
    "40": ("FR613", "Landes"),
    "41": ("FR245", "Loir-et-Cher"),
    "42": ("FR715", "Loire"),
    "43": ("FR723", "Haute-Loire"),
    "44": ("FR511", "Loire-Atlantique"),
    "45": ("FR246", "Loiret"),
    "46": ("FR625", "Lot"),
    "47": ("FR614", "Lot-et-Garonne"),
    "48": ("FR814", "Lozère"),
    "49": ("FR512", "Maine-et-Loire"),
    "50": ("FR252", "Manche"),
    "51": ("FR213", "Marne"),
    "52": ("FR214", "Haute-Marne"),
    "53": ("FR513", "Mayenne"),
    "54": ("FR411", "Meurthe-et-Moselle"),
    "55": ("FR412", "Meuse"),
    "56": ("FR524", "Morbihan"),
    "57": ("FR413", "Moselle"),
    "58": ("FR262", "Nièvre"),
    "59": ("FR301", "Nord"),
    "60": ("FR222", "Oise"),
    "61": ("FR253", "Orne"),
    "62": ("FR302", "Pas-de-Calais"),
    "63": ("FR724", "Puy-de-Dôme"),
    "64": ("FR615", "Pyrénées-Atlantiques"),
    "65": ("FR626", "Hautes-Pyrénées"),
    "66": ("FR815", "Pyrénées-Orientales"),
    "67": ("FR421", "Bas-Rhin"),
    "68": ("FR422", "Haut-Rhin"),
    "69": ("FR716", "Rhône"),
    "70": ("FR433", "Haute-Saône"),
    "71": ("FR263", "Saône-et-Loire"),
    "72": ("FR514", "Sarthe"),
    "73": ("FR717", "Savoie"),
    "74": ("FR718", "Haute-Savoie"),
    "75": ("FR101", "Paris"),
    "76": ("FR232", "Seine-Maritime"),
    "77": ("FR102", "Seine-et-Marne"),
    "78": ("FR103", "Yvelines"),
    "79": ("FR533", "Deux-Sèvres"),
    "80": ("FR223", "Somme"),
    "81": ("FR627", "Tarn"),
    "82": ("FR628", "Tarn-et-Garonne"),
    "83": ("FR825", "Var"),
    "84": ("FR826", "Vaucluse"),
    "85": ("FR515", "Vendée"),
    "86": ("FR534", "Vienne"),
    "87": ("FR633", "Haute-Vienne"),
    "88": ("FR414", "Vosges"),
    "89": ("FR264", "Yonne"),
    "90": ("FR434", "Territoire de Belfort"),
    "91": ("FR104", "Essonne"),
    "92": ("FR105", "Hauts-de-Seine"),
    "93": ("FR106", "Seine-Saint-Denis"),
    "94": ("FR107", "Val-de-Marne"),
    "95": ("FR108", "Val-d'Oise"),
}


@dataclass(frozen=True)
class MeteoFranceVigilanceStatus:
    """The current warnings of Météo-France, and the publication timestamp of the bulletin."""

    updated_utc: Optional[int]
    vigilances: tuple[Vigilance, ...]


def meteofrance_key_from_env(env: Optional[Mapping[str, str]] = None) -> tuple[Optional[str], Optional[str]]:
    """``(key, problem)`` read from ``VIGIE_METEOFRANCE_API_KEY``. A key with whitespace or fewer than 16 characters is
    refused without being quoted."""
    raw = ((env if env is not None else os.environ).get(KEY_ENV) or "").strip()
    if not raw:
        return None, None
    if not _KEY_SHAPE.match(raw):
        return None, f"{KEY_ENV} is set but is not shaped as a Météo-France API key: it is not used"
    return raw, None


def _iso_to_utc(value: Any) -> Optional[int]:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def _resolve_department(domain_id: str) -> Optional[str]:
    """Map a ``domain_id`` of ``cartevigilance`` to an INSEE department code (``64`` or coastal ``6410`` -> ``64``)."""
    code = domain_id.strip().upper()
    if code in DEPARTMENTS:
        return code
    if len(code) == 4 and code.endswith("10") and code[:2] in DEPARTMENTS:
        return code[:2]
    return None


def _extract_department_texts(payload: Any) -> dict[str, tuple[Optional[str], str]]:
    """Return ``{domain_id: (bloc_title, description)}`` from ``/textesvigilance/encours``, keeping the issuer's wording intact."""
    if not isinstance(payload, Mapping):
        return {}
    product = payload.get("product")
    if not isinstance(product, Mapping):
        return {}
    items = product.get("text_bloc_items")
    if not isinstance(items, list):
        return {}
    out: dict[str, tuple[Optional[str], str]] = {}
    for entry in items:
        if not isinstance(entry, Mapping):
            continue
        domain_id = str(entry.get("domain_id") or "").strip().upper()
        bloc_items = entry.get("bloc_items")
        if not domain_id or not isinstance(bloc_items, list) or not bloc_items:
            continue
        title = str(entry.get("bloc_title") or "").strip() or None
        paragraphs: list[str] = []
        for bloc in bloc_items:
            if not isinstance(bloc, Mapping):
                continue
            for text_item in bloc.get("text_items") or []:
                if not isinstance(text_item, Mapping):
                    continue
                for term in text_item.get("term_items") or []:
                    if not isinstance(term, Mapping):
                        continue
                    for sub in term.get("subdivision_text") or []:
                        if not isinstance(sub, Mapping):
                            continue
                        header = " ".join(
                            part.strip()
                            for part in (str(sub.get("underline_text") or ""), str(sub.get("bold_text") or ""))
                            if part.strip()
                        )
                        lines = [str(line).strip() for line in (sub.get("text") or []) if str(line).strip()]
                        body = "\n".join(lines)
                        if header and body:
                            paragraphs.append(f"{header}\n{body}")
                        elif header:
                            paragraphs.append(header)
                        elif body:
                            paragraphs.append(body)
        if paragraphs:
            out[domain_id] = (title, "\n\n".join(paragraphs))
    return out


def parse_vigilance(map_payload: Any, texts_payload: Any = None) -> MeteoFranceVigilanceStatus:
    """Parse ``/cartevigilance/encours`` (and optional ``/textesvigilance/encours``) into ``MeteoFranceVigilanceStatus``."""
    if not isinstance(map_payload, Mapping) or not isinstance(map_payload.get("product"), Mapping):
        raise ValueError("Météo-France DPVigilance answer has no `product` object")
    product: Mapping[str, Any] = map_payload["product"]
    periods = product.get("periods")
    if not isinstance(periods, list) or not periods:
        raise ValueError("Météo-France DPVigilance answer has no `periods` list")

    update_raw = str(product.get("update_time") or "").strip() or None
    updated_utc = _iso_to_utc(update_raw)
    base_credit = credit_text("meteofrance_vigilance")
    attribution = f"{base_credit}. Last update: {update_raw}" if update_raw else base_credit
    texts_by_domain = _extract_department_texts(texts_payload)

    # Aggregate across periods (J and J1) by (dep_code, phenomenon_id).
    aggregated: dict[tuple[str, str], dict[str, Any]] = {}
    for period in periods:
        if not isinstance(period, Mapping):
            continue
        p_begin = _iso_to_utc(period.get("begin_validity_time"))
        p_end = _iso_to_utc(period.get("end_validity_time"))
        timelaps = period.get("timelaps")
        domain_ids = timelaps.get("domain_ids") if isinstance(timelaps, Mapping) else None
        if not isinstance(domain_ids, list):
            continue
        for domain in domain_ids:
            if not isinstance(domain, Mapping):
                continue
            dep_code = _resolve_department(str(domain.get("domain_id") or ""))
            if dep_code is None:
                continue
            for item in domain.get("phenomenon_items") or []:
                if not isinstance(item, Mapping):
                    continue
                phen_id = str(item.get("phenomenon_id") or "").strip()
                try:
                    color_id = int(item.get("phenomenon_max_color_id") or 1)
                except (TypeError, ValueError):
                    continue
                if color_id not in COLORS:
                    continue
                slot_starts: list[int] = []
                slot_ends: list[int] = []
                slot_strings: list[str] = []
                for slot in item.get("timelaps_items") or []:
                    if not isinstance(slot, Mapping):
                        continue
                    try:
                        slot_color = int(slot.get("color_id") or 1)
                    except (TypeError, ValueError):
                        continue
                    if slot_color not in COLORS:
                        continue
                    b_raw = str(slot.get("begin_time") or "").strip()
                    e_raw = str(slot.get("end_time") or "").strip()
                    b_utc = _iso_to_utc(b_raw)
                    e_utc = _iso_to_utc(e_raw)
                    if b_utc is not None:
                        slot_starts.append(b_utc)
                    if e_utc is not None:
                        slot_ends.append(e_utc)
                    if b_raw and e_raw:
                        slot_strings.append(f"{b_raw}/{e_raw}:{COLORS[slot_color][0]}")
                onset = min(slot_starts) if slot_starts else p_begin
                expires = max(slot_ends) if slot_ends else p_end
                key = (dep_code, phen_id)
                current = aggregated.get(key)
                if current is None:
                    aggregated[key] = {
                        "color_id": color_id,
                        "onset_utc": onset,
                        "expires_utc": expires,
                        "slots": slot_strings,
                    }
                else:
                    current["color_id"] = max(current["color_id"], color_id)
                    if onset is not None:
                        current["onset_utc"] = onset if current["onset_utc"] is None else min(current["onset_utc"], onset)
                    if expires is not None:
                        current["expires_utc"] = (
                            expires if current["expires_utc"] is None else max(current["expires_utc"], expires)
                        )
                    for s in slot_strings:
                        if s not in current["slots"]:
                            current["slots"].append(s)

    vigilances: list[Vigilance] = []
    for (dep_code, phen_id), info in sorted(
        aggregated.items(), key=lambda entry: (-entry[1]["color_id"], entry[0][0], entry[0][1])
    ):
        nuts3, dep_name = DEPARTMENTS[dep_code]
        color_label, level = COLORS[info["color_id"]]
        phen_label, phenomenon = PHENOMENA.get(phen_id, (f"Phénomène {phen_id}", VigilancePhenomenon.OTHER))
        event_text = f"Vigilance {color_label.lower()} {phen_label.lower()}"
        parameters: list[VigilanceParameter] = [
            VigilanceParameter(name="phenomenon_id", value=phen_id),
            VigilanceParameter(name="color_id", value=str(info["color_id"])),
        ]
        if info["slots"]:
            parameters.append(VigilanceParameter(name="timelaps", value=", ".join(info["slots"])))
        dept_text = texts_by_domain.get(dep_code)
        headline = dept_text[0] if dept_text and dept_text[0] else f"{dep_name} ({dep_code}) : {event_text}"
        description = dept_text[1] if dept_text else None
        text_entry = VigilanceText(
            language="fr-FR",
            sender_name=ISSUER,
            event=event_text,
            headline=headline,
            description=description,
            web=WEB_URL,
            parameters=parameters,
        )
        stamp = update_raw or str(updated_utc or "current")
        vigilances.append(
            Vigilance(
                id=f"meteofrance:{dep_code}:{phen_id}:{stamp}",
                source="meteofrance",
                country="FR",
                attribution=attribution,
                identifier=f"MF-VIGILANCE-{dep_code}-{phen_id}-{stamp}",
                sent_utc=updated_utc,
                effective_utc=updated_utc,
                onset_utc=info["onset_utc"],
                expires_utc=info["expires_utc"],
                message_type="Alert",
                status=VigilanceStatus.NEW,
                level=level,
                level_original=color_label,
                phenomenon=phenomenon,
                phenomenon_original=phen_label,
                event=event_text,
                issuer=ISSUER,
                texts=[text_entry],
                area=VigilanceArea(
                    description=dep_name,
                    geocodes=[
                        VigilanceGeocode(scheme="NUTS3", value=nuts3),
                        VigilanceGeocode(scheme="INSEE_DEP", value=dep_code),
                    ],
                ),
                web=WEB_URL,
            )
        )
    return MeteoFranceVigilanceStatus(updated_utc=updated_utc, vigilances=tuple(vigilances))


class MeteoFranceVigilanceCollector(BaseCollector[MeteoFranceVigilanceStatus]):
    """Official Météo-France departmental and coastal warnings from ``DPVigilance/v1``."""

    def __init__(self, api_key: str, http_client: Optional[httpx.AsyncClient] = None) -> None:
        super().__init__(
            provider_id="meteofrance_vigilance",
            name="Météo-France Vigilance (DPVigilance v1)",
            domain="vigilance",
            ttl_seconds=TTL_SECONDS,
            timeout_seconds=15.0,
            http_client=http_client,
        )
        self._key = api_key
        self.secrets = (api_key,)

    async def fetch_status(self):  # type: ignore[no-untyped-def]
        """Fetch the current vigilance map and bulletins (cached ``TTL_SECONDS``)."""
        return await self.fetch_ex(cache_key="status")

    async def _collect(self, client: httpx.AsyncClient, **kwargs: Any) -> MeteoFranceVigilanceStatus:
        headers = {"apikey": self._key, "Accept": "application/json"}
        map_resp = await self._get(client, MAP_URL, headers=headers)
        map_resp.raise_for_status()
        map_payload = map_resp.json()
        texts_payload: Any = None
        try:
            texts_resp = await self._get(client, TEXTS_URL, headers=headers)
            if texts_resp.status_code == 200:
                texts_payload = texts_resp.json()
        except Exception:  # noqa: BLE001 - the map alone is enough to serve the warnings if the text endpoint fails
            texts_payload = None
        return parse_vigilance(map_payload, texts_payload)
