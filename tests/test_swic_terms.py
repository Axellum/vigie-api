"""The terms records of the registry services: what makes a service verified, and what no setting can switch on.

The records are data written by hand from pages read on 2026-10-20 (``SOURCES.md``); these tests keep them honest: a record that says
"verified" has to name the page or the feed it was read in, and the feed has to be one the registry lists.
"""

from __future__ import annotations

import pytest

from app.collectors.swic_registry import SwicSource, bundled_registry
from app.collectors.swic_terms import (
    COUNTRY_OVERRIDES,
    EXCLUDED,
    TERMS,
    credit_of,
    is_eligible,
    status_of,
    terms_of,
)

REGISTRY = {source.source_id: source for source in bundled_registry()}
VERIFIED = [terms for terms in TERMS if terms.status == "verified"]


def test_the_records_are_unique_and_each_names_a_service_of_the_registry() -> None:
    ids = [terms.source_id for terms in TERMS]
    assert len(ids) == len(set(ids))
    assert set(ids) <= set(REGISTRY), sorted(set(ids) - set(REGISTRY))
    for terms in TERMS:
        assert REGISTRY[terms.source_id].country == terms.country, terms.source_id


def test_at_least_fifteen_services_were_looked_at() -> None:
    assert len(TERMS) >= 15 and len(VERIFIED) >= 5


@pytest.mark.parametrize("terms", VERIFIED, ids=lambda terms: terms.source_id)
def test_a_verified_service_names_the_page_or_the_feed_it_was_read_in(terms) -> None:  # type: ignore[no-untyped-def]
    assert terms.evidence.startswith("https://") and terms.licence
    assert terms.evidence_kind in ("terms page", "own feed")
    if terms.evidence_kind == "own feed":
        # The line was read in the feed the registry lists for this service, and the weekly check looks for it there.
        assert terms.evidence in REGISTRY[terms.source_id].feeds
        assert terms.feed_marker and terms.feed_marker == terms.feed_marker.lower()
        assert "cc by 4.0" in terms.licence.lower() or "creative commons" in terms.licence.lower()
    else:
        assert terms.feed_marker  # a word the feed holds that points to the page (the weekly check looks for it)


@pytest.mark.parametrize("terms", TERMS, ids=lambda terms: terms.source_id)
def test_each_credit_names_the_issuer_and_never_holds_the_separator(terms) -> None:  # type: ignore[no-untyped-def]
    assert terms.credit.startswith("Source: ") and " | " not in terms.credit
    if terms.status == "unverified":
        assert "not verified" in terms.credit.lower()  # an unverified service never reads as if it were allowed
    else:
        assert "not verified" not in terms.credit.lower()


def test_a_bare_public_domain_line_is_never_enough() -> None:
    """The line most feeds carry (the WMO's own template) says nothing of what the issuer allows."""
    for terms in TERMS:
        if "public domain" in terms.licence.lower() or "dominio público" in terms.licence.lower():
            assert terms.status == "unverified", terms.source_id


def test_the_excluded_services_are_never_eligible_whatever_the_setting() -> None:
    assert set(EXCLUDED) == {"hk-hko-xx", "cn-cma-xx"}
    for source_id in EXCLUDED:
        for mode in ("verified", "all", "off"):
            assert is_eligible(REGISTRY[source_id], mode) is False
    assert all(reason for reason in EXCLUDED.values())


def test_the_modes_pick_the_services() -> None:
    services = list(REGISTRY.values())
    verified = {s.source_id for s in services if is_eligible(s, "verified")}
    everything = {s.source_id for s in services if is_eligible(s, "all")}
    assert verified == {terms.source_id for terms in VERIFIED}
    assert verified < everything and everything == {s.source_id for s in services} - set(EXCLUDED)
    assert not any(is_eligible(s, "off") for s in services)
    assert len(everything) == 138  # 140 services in the registry of 2026-10-20, two of them excluded


def test_a_service_nobody_looked_at_is_unverified_and_gets_a_credit_that_says_so() -> None:
    stranger = SwicSource("zz-new-en", "ZZ", "Zedland", "Zed Weather", ("https://example.org/f.xml",))
    assert terms_of("zz-new-en") is None and status_of("zz-new-en") == "unverified"
    assert is_eligible(stranger, "verified") is False and is_eligible(stranger, "all") is True
    credit = credit_of(stranger)
    assert credit.startswith("Source: Zed Weather (Zedland). Reuse terms not verified") and "WMO Severe Weather Information Centre" in credit


def test_a_service_without_a_feed_or_a_country_is_never_read() -> None:
    assert is_eligible(SwicSource("a-b-c", "AA", "", "x", ()), "all") is False
    assert is_eligible(SwicSource("a-b-c", None, "", "x", ("https://example.org/f.xml",)), "all") is False


def test_the_credit_of_a_verified_service_names_the_issuer_and_the_registry() -> None:
    for terms in VERIFIED:
        credit = credit_of(REGISTRY[terms.source_id])
        assert terms.credit in credit and "WMO Severe Weather Information Centre" in credit, terms.source_id


def test_the_country_overrides_name_a_verified_service_of_that_country() -> None:
    for country, source_id in COUNTRY_OVERRIDES.items():
        assert REGISTRY[source_id].country == country and status_of(source_id) == "verified"
