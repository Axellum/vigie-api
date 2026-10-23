"""Why a country has no source of official warnings on an instance: a stable code and one short sentence per country.

A position in a country that the instance reads nothing for (Japan, Australia, a country of the WMO registry whose terms were not
verified...) is answered with the country and the reason, never with a bare "nothing" (``GET /v1/vigilances`` carries it in
``uncovered`` and in ``note``; ``GET /v1/vigilances/countries?uncovered=true`` lists every country). The code is meant for a client to
translate; the sentence is English.

**A reason is written only when it was read.** Where it comes from, in the order the functions below look:

1. ``COUNTRY_REASONS``: a country the instance was never meant to read, with what was read about it (``SOURCES.md`` has one row for
   each, with the address). Australia and Japan have no service in the WMO registry.
2. The services of the WMO registry the instance does not read: the reason is the record of ``swic_terms.TERMS`` (``restriction``) or,
   when no record looks at the service, that its terms were not looked at; ``swic_terms.EXCLUDED`` (Hong Kong, China) are never read,
   whatever the setting (``EXCLUDED_REASONS``); with ``VIGIE_WMO_SOURCES=off`` none is read (``source_off``).
3. Anything else is ``no_source``: no official warning feed is read for the country, and nothing more is known. It does not say that
   the country has no service, nor that its terms forbid anything.

A territory has its own ISO code (Puerto Rico ``PR``, French Guiana ``GF``...) and is answered on its own: the source of the country it
belongs to is not asked for it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.collectors.swic_registry import SwicSource
from app.collectors.swic_terms import EXCLUDED, Restriction, terms_of
from app.models.vigilance import ReasonCode, UncoveredCountry


@dataclass(frozen=True)
class Reason:
    """One reason: the stable code, the sentence, and what it was read in (an address or a document; empty for the default)."""

    code: ReasonCode
    text: str
    evidence: str


NO_SOURCE = Reason("no_source", "No official warning feed is read by this instance for this country.", "")

# Countries that are not read, and why. Checked on 2026-10-20; ``SOURCES.md`` ("Countries with no source") has the row of each.
COUNTRY_REASONS: dict[str, Reason] = {
    "AU": Reason(
        "redistribution_forbidden",
        "The Bureau of Meteorology does not allow its warnings to be redistributed without the paid status of registered user: "
        "this instance reads none.",
        "https://www.bom.gov.au/ (research of 2026-10-20; the pages answered 403 to an automated reader that day, so the wording was "
        "not read again)",
    ),
    "JP": Reason(
        "law_restricts",
        "Japanese law reserves the giving of warnings to the Japan Meteorological Agency (Meteorological Service Act, article 23): "
        "this instance reads none of its warnings.",
        "https://www.japaneselawtranslation.go.jp/en/laws/view/1968/en",
    ),
    "KR": Reason(
        "licence_unverified",
        "The reuse terms of the national weather service of Korea were not verified: this instance reads none.",
        "none: no terms were read (the plan of 2026-10-20 lists Korea among the licences not verified)",
    ),
}

# The services of the registry that ``swic_terms.EXCLUDED`` never reads, by service id. A test keeps the two tables on the same ids.
EXCLUDED_REASONS: dict[str, Reason] = {
    "hk-hko-xx": Reason(
        "terms_contradictory",
        "The terms of the Hong Kong Observatory contradict each other: this instance never reads it.",
        "SOURCES.md, \"Never read, whatever the setting\"",
    ),
    "cn-cma-xx": Reason(
        "licence_unverified",
        "No reuse terms were found for the China Meteorological Administration, and its feed (1,000 items, about 1 MB) is too heavy "
        "to read: this instance never reads it.",
        "SOURCES.md, \"Never read, whatever the setting\"",
    ),
}

WMO_OFF = Reason("source_off", "This instance does not read the services of the WMO registry (VIGIE_WMO_SOURCES=off).", "")
METSERVICE_OFF = Reason(
    "source_off",
    "MetService is turned off on this instance (VIGIE_ENABLE_METSERVICE=0): its terms of use are not settled for an instance that "
    "relays the warnings to others.",
    "SOURCES.md, MetService",
)

_ONLY_WITH_ALL = "it is read only with VIGIE_WMO_SOURCES=all"
_CODE_OF_RESTRICTION: dict[Restriction, ReasonCode] = {"": "licence_unverified", "restricts": "terms_restrict", "contradictory": "terms_contradictory"}


def _service_reason(service: SwicSource) -> Reason:
    """Why one service of the registry is not read, from the record that looked at its terms (else: that nobody did)."""
    terms = terms_of(service.source_id)
    if terms is None:
        return Reason(
            "licence_unverified",
            f"{service.name} is listed by the WMO as a national warning service, but its reuse terms were not looked at: {_ONLY_WITH_ALL}.",
            "",
        )
    code = _CODE_OF_RESTRICTION[terms.restriction]
    if code == "terms_restrict":
        text = f"The terms found for {terms.issuer} ask for its permission before its warnings are passed on: {_ONLY_WITH_ALL}."
    elif code == "terms_contradictory":
        text = f"The terms of {terms.issuer} contradict each other: {_ONLY_WITH_ALL}."
    else:
        text = f"{terms.issuer} is listed by the WMO as a national warning service, but its reuse terms were not verified: {_ONLY_WITH_ALL}."
    return Reason(code, text, terms.evidence)


def reason_for(country: str, services: Sequence[SwicSource], wmo_mode: str, *, metservice_off: bool = False) -> Reason:
    """Why an instance reads no source for ``country``.

    ``services`` are the services the WMO registry lists for the country (read or not: the caller has already found that the instance
    reads none of them), ``wmo_mode`` is ``VIGIE_WMO_SOURCES`` (``verified``, ``all`` or ``off``).
    """
    if country == "NZ" and metservice_off:
        return METSERVICE_OFF
    if country in COUNTRY_REASONS:
        return COUNTRY_REASONS[country]
    with_feed = sorted((service for service in services if service.feeds), key=lambda service: service.source_id)
    never_read = [service for service in with_feed if service.source_id in EXCLUDED]
    not_read = [service for service in with_feed if service.source_id not in EXCLUDED]
    if not_read:
        return WMO_OFF if wmo_mode == "off" else _service_reason(not_read[0])
    for service in never_read:
        if service.source_id in EXCLUDED_REASONS:
            return EXCLUDED_REASONS[service.source_id]
    return NO_SOURCE


def uncovered_country(country: str, name: str, reason: Reason) -> UncoveredCountry:
    """The answer's form of a reason."""
    return UncoveredCountry(country=country, name=name, reason_code=reason.code, reason=reason.text)
