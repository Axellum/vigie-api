"""A country with no source says why: the table of reasons, and the answers that carry it.

The borders hold every country (``tools/build_geometries.py``), so a position in Japan, Australia or China finds its country; the answer
then names it and gives a stable code and one sentence (``app/services/uncovered.py``). Nothing reaches the network.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any, get_args

import pytest

from app.collectors.swic_registry import bundled_registry
from app.collectors.swic_terms import EXCLUDED, TERMS
from app.models.vigilance import ReasonCode, UncoveredCountry, VigilanceCountry, VigilancesResponse
from app.services.geoindex import default_countries
from app.services.uncovered import COUNTRY_REASONS, EXCLUDED_REASONS, NO_SOURCE, Reason, reason_for
from app.services.vigilance import NO_COUNTRY_NOTE, VigilanceService
from tests.conftest import World
from tests.swic_samples import service_serving_everything

SOURCES_PAGE = (Path(__file__).resolve().parents[1] / "SOURCES.md").read_text(encoding="utf-8")
TOKYO = (35.6762, 139.6503)
SYDNEY = (-33.8688, 151.2093)
BEIJING = (39.9042, 116.4074)
SEA = (0.0, -150.0)


def service_in(mode: str) -> VigilanceService:
    return service_serving_everything(mode)


# ---------------------------------------------------------------- the table of reasons


def all_reasons() -> Iterator[tuple[str, Reason]]:
    yield from COUNTRY_REASONS.items()
    yield from EXCLUDED_REASONS.items()
    yield "", NO_SOURCE


@pytest.mark.parametrize(("key", "reason"), list(all_reasons()), ids=[key or "default" for key, _ in all_reasons()])
def test_a_reason_is_a_known_code_and_one_short_english_sentence(key: str, reason: Reason) -> None:
    assert reason.code in get_args(ReasonCode)
    assert reason.text.endswith(".") and 20 < len(reason.text) < 260, key
    assert reason.text.count(". ") == 0, f"{key}: one sentence"


@pytest.mark.parametrize("country", sorted(COUNTRY_REASONS))
def test_a_country_with_a_stated_reason_says_what_it_was_read_in(country: str) -> None:
    """Never a reason that was not read: each one has its evidence in the code and a row of SOURCES.md that quotes the same sentence."""
    reason = COUNTRY_REASONS[country]
    assert reason.evidence
    [row] = [line for line in SOURCES_PAGE.splitlines() if f"| {country} |" in line and f"`{reason.code}`" in line]
    assert reason.text in row


@pytest.mark.parametrize("source_id", sorted(EXCLUDED_REASONS))
def test_a_service_that_is_never_read_has_its_reason_and_its_row(source_id: str) -> None:
    reason = EXCLUDED_REASONS[source_id]
    [row] = [line for line in SOURCES_PAGE.splitlines() if f"`{source_id}`" in line and f"`{reason.code}`" in line and reason.text in line]
    assert row.startswith("|")


def test_every_service_that_is_never_read_has_a_reason_and_the_two_tables_name_the_same_services() -> None:
    assert set(EXCLUDED_REASONS) == set(EXCLUDED)  # a new exclusion cannot go without its reason
    registry = {source.source_id: source.country for source in bundled_registry()}
    assert {registry[source_id] for source_id in EXCLUDED} == {"HK", "CN"}


def test_each_code_is_explained_in_sources() -> None:
    for code in get_args(ReasonCode):
        assert f"`{code}`" in SOURCES_PAGE, code
    assert {reason.code for _, reason in all_reasons()} <= set(get_args(ReasonCode))


def test_the_terms_records_say_what_the_terms_do() -> None:
    """``restriction`` is what the answer says about a service whose terms were looked at: it must follow the record's own words."""
    contradictory = {t.source_id for t in TERMS if "contradict" in f"{t.licence} {t.note}".lower()}
    assert contradictory == {t.source_id for t in TERMS if t.restriction == "contradictory"} == {
        "kw-dom-en", "kz-kazhydromet-ru", "sg-mss-en", "th-tmd-th", "tt-ttms-en",
    }
    assert {t.source_id for t in TERMS if t.restriction == "restricts"} == {"id-inatews-id", "vu-vms-xx", "za-saws-en"}
    assert all(t.restriction == "" for t in TERMS if t.status == "verified")  # a verified service is read: nothing to say


# ---------------------------------------------------------------- which reason, for which country


@pytest.mark.parametrize(
    ("country", "name", "code"),
    [
        ("JP", "Japan", "law_restricts"),
        ("AU", "Australia", "redistribution_forbidden"),
        ("KR", "South Korea", "licence_unverified"),
        ("HK", "Hong Kong", "terms_contradictory"),
        ("CN", "People's Republic of China", "licence_unverified"),
        ("SG", "Singapore", "terms_contradictory"),  # the terms of the service contradict the line of its feed
        ("TH", "Thailand", "terms_contradictory"),
        ("ID", "Indonesia", "terms_restrict"),
        ("ZA", "South Africa", "terms_restrict"),
        ("BR", "Brazil", "licence_unverified"),  # a service of the registry, no terms found
        ("RU", "Russia", "licence_unverified"),  # a bare "public domain" line
        ("AF", "Afghanistan", "licence_unverified"),  # a service of the registry that no record looked at
        ("KP", "North Korea", "no_source"),  # nothing known
        ("PR", "Puerto Rico", "no_source"),  # a territory is answered on its own: the source of the United States is not asked for it
        ("GL", "Greenland", "no_source"),
    ],
)
def test_the_reason_of_a_country_by_default(country: str, name: str, code: str) -> None:
    reason = service_in("verified").uncovered_reason(country)
    assert reason is not None
    assert (reason.country, reason.name, reason.covered, reason.reason_code) == (country, name, False, code)


def test_the_sentence_of_a_registry_service_names_it_and_says_how_to_read_it() -> None:
    service = service_in("verified")
    for country, issuer in (("SG", "Meteorological Service Singapore"), ("BR", "Instituto Nacional de Meteorologia"), ("ID", "BMKG")):
        reason = service.uncovered_reason(country)
        assert reason is not None and issuer in reason.reason and "VIGIE_WMO_SOURCES=all" in reason.reason
    unknown = service.uncovered_reason("AF")
    assert unknown is not None and "not looked at" in unknown.reason  # said as it is: nobody read its terms


def test_a_country_with_a_source_has_no_reason() -> None:
    service = service_in("verified")
    for country in ("FR", "DE", "US", "CA", "NZ", "ES", "IS", "MX", "AR", "PH"):  # the collectors' and the verified services'
        assert service.uncovered_reason(country) is None, country


def test_with_all_the_services_read_only_the_never_read_ones_and_the_unknown_stay_out() -> None:
    service = service_in("all")
    assert service.uncovered_reason("SG") is None and service.uncovered_reason("BR") is None and service.uncovered_reason("AF") is None
    assert {item.country: item.reason_code for item in service.uncovered_of(["HK", "CN", "JP", "AU", "KP"])} == {
        "HK": "terms_contradictory",
        "CN": "licence_unverified",
        "JP": "law_restricts",
        "AU": "redistribution_forbidden",
        "KP": "no_source",
    }


def test_with_the_registry_turned_off_every_registry_country_says_so_and_the_others_do_not_change() -> None:
    service = service_in("off")
    for country in ("MX", "AR", "SG", "BR"):
        reason = service.uncovered_reason(country)
        assert reason is not None and reason.reason_code == "source_off" and "VIGIE_WMO_SOURCES=off" in reason.reason, country
    assert service.uncovered_reason("FR") is None
    assert [service.uncovered_reason(country).reason_code for country in ("JP", "HK", "KP")] == ["law_restricts", "terms_contradictory", "no_source"]  # type: ignore[union-attr]


def test_new_zealand_with_metservice_turned_off_says_so() -> None:
    service = service_in("verified")
    assert service.uncovered_reason("NZ") is None
    service.metservice = None
    reason = service.uncovered_reason("NZ")
    assert reason is not None and reason.reason_code == "source_off" and "VIGIE_ENABLE_METSERVICE=0" in reason.reason


def test_a_country_that_has_a_collector_missing_is_not_read_and_says_so_plainly() -> None:
    service = service_in("verified")
    service.eccc = None  # Canada without its collector: nothing is asked for it, and nothing more is known
    reason = service.uncovered_reason("CA")
    assert reason is not None and reason.reason_code == "no_source"


def test_the_precedence_of_the_reasons() -> None:
    """A stated reason first (Japan), then the registry's, then the plain default; MetService's switch before them all."""
    services = [source for source in bundled_registry() if source.country == "SG"]
    assert reason_for("SG", services, "verified").code == "terms_contradictory"
    assert reason_for("SG", [], "verified") is NO_SOURCE  # the registry lists no service for it
    assert reason_for("JP", services, "off") is COUNTRY_REASONS["JP"]  # a stated reason wins over the registry's
    assert reason_for("NZ", [], "all", metservice_off=True).code == "source_off"


# ---------------------------------------------------------------- the answers


def test_a_point_in_tokyo_names_japan_and_why(world: World) -> None:
    body = world.client.get("/v1/vigilances", params={"lat": TOKYO[0], "lon": TOKYO[1]}).json()
    assert body["countries"] == [] and body["vigilances"] == [] and body["coverage"] == [] and body["disclaimer"] is None
    assert body["uncovered"] == [
        {
            "country": "JP",
            "name": "Japan",
            "covered": False,
            "reason_code": "law_restricts",
            "reason": COUNTRY_REASONS["JP"].text,
        }
    ]
    assert body["note"].startswith("Japan (JP): " + COUNTRY_REASONS["JP"].text) and "/v1/vigilances/countries" in body["note"]


@pytest.mark.parametrize(
    ("point", "country", "name", "code"),
    [
        (SYDNEY, "AU", "Australia", "redistribution_forbidden"),
        (BEIJING, "CN", "People's Republic of China", "licence_unverified"),
    ],
)
def test_a_point_in_sydney_or_beijing_names_the_country_and_why(world: World, point: tuple[float, float], country: str, name: str, code: str) -> None:
    body = world.client.get("/v1/vigilances", params={"lat": point[0], "lon": point[1]}).json()
    [item] = body["uncovered"]
    assert (item["country"], item["name"], item["reason_code"]) == (country, name, code)
    assert f"{name} ({country}): {item['reason']}" in body["note"]
    assert body["countries"] == [] and body["coverage"] == []


def test_a_point_at_sea_is_in_no_country_and_says_so(world: World) -> None:
    body = world.client.get("/v1/vigilances", params={"lat": SEA[0], "lon": SEA[1]}).json()
    assert body["countries"] == [] and body["uncovered"] == [] and body["coverage"] == []
    assert body["note"] == NO_COUNTRY_NOTE


def test_the_geojson_carries_the_reasons_in_its_metadata(world: World) -> None:
    metadata = world.client.get("/v1/vigilances.geojson", params={"lat": TOKYO[0], "lon": TOKYO[1]}).json()["metadata"]
    assert [item["country"] for item in metadata["uncovered"]] == ["JP"] and metadata["note"].startswith("Japan (JP)")


def test_a_country_nobody_serves_is_a_404_that_says_why(world: World) -> None:
    detail = world.client.get("/v1/vigilances", params={"country": "JP"})
    assert detail.status_code == 404
    text = detail.json()["detail"]
    assert "Japan" in text and COUNTRY_REASONS["JP"].text in text and "/v1/vigilances/countries" in text
    unknown = world.client.get("/v1/vigilances", params={"country": "ZZ"}).json()["detail"]  # not a country at all: no reason invented
    assert "ZZ" in unknown and "reads" not in unknown and "/v1/vigilances/countries" in unknown


def test_the_answer_model_has_the_new_field_with_an_empty_default() -> None:
    assert VigilancesResponse(server_utc=0, query={}).uncovered == []  # type: ignore[arg-type]


# ---------------------------------------------------------------- GET /v1/vigilances/countries


def listing(world: World, **params: Any) -> list[dict[str, Any]]:
    response = world.client.get("/v1/vigilances/countries", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_the_default_listing_is_the_one_it_always_was(world: World) -> None:
    default = listing(world)
    assert all(set(item) == {"country", "sources", "licence_verified"} for item in default)
    assert listing(world, uncovered="false") == default


def test_with_uncovered_the_list_adds_every_other_country_and_leaves_the_others_as_they_are(world: World) -> None:
    default = listing(world)
    full = listing(world, uncovered="true")
    codes = [item["country"] for item in full]
    assert codes == sorted(codes) and set(codes) == set(default_countries().countries())  # every border, once, sorted
    served = [item for item in full if "covered" not in item]
    assert served == default  # the entries of the countries with a source: not one field changed
    uncovered = [item for item in full if item.get("covered") is False]
    assert len(served) + len(uncovered) == len(full) and len(uncovered) > 150
    assert all(set(item) == {"country", "name", "covered", "reason_code", "reason"} for item in uncovered)
    assert all(item["reason_code"] in get_args(ReasonCode) and item["reason"] for item in uncovered)
    by_code = {item["country"]: item for item in uncovered}
    assert by_code["JP"]["reason_code"] == "law_restricts" and by_code["AU"]["reason_code"] == "redistribution_forbidden"
    assert by_code["HK"]["reason_code"] == "terms_contradictory" and by_code["CN"]["name"] == "People's Republic of China"
    assert "FR" not in by_code and "DE" not in by_code and "MX" not in by_code  # these have a source


def test_the_entries_of_the_listing_validate_as_the_models_of_the_api(world: World) -> None:
    for item in listing(world, uncovered="true"):
        model = UncoveredCountry if item.get("covered") is False else VigilanceCountry
        model.model_validate(item)


def test_the_openapi_document_describes_the_flag_and_the_reason_codes(world: World) -> None:
    document = world.client.get("/openapi.json").json()
    parameters = {p["name"]: p for p in document["paths"]["/v1/vigilances/countries"]["get"]["parameters"]}
    assert parameters["uncovered"]["schema"]["default"] is False
    assert set(document["components"]["schemas"]["UncoveredCountry"]["properties"]["reason_code"]["enum"]) == set(get_args(ReasonCode))
