"""Tests for the official Météo-France vigilance collector (``DPVigilance/v1``) and configurable Open-Meteo URLs."""

from __future__ import annotations

import httpx
import pytest

from app.collectors.marine import DEFAULT_OPEN_METEO_MARINE_URL, OpenMeteoMarineCollector
from app.collectors.meteofrance import (
    DEPARTMENTS,
    MAP_URL,
    TEXTS_URL,
    MeteoFranceVigilanceCollector,
    meteofrance_key_from_env,
    parse_vigilance,
)
from app.collectors.surf import SurfForecastCollector
from app.collectors.weather import DEFAULT_OPEN_METEO_FORECAST_URL, OpenMeteoCollector
from app.models.schemas import SeverityLevel
from app.models.vigilance import VigilancePhenomenon
from app.services.geoindex import default_geometries
from tests import vigilance_samples as samples
from tests.helpers import SimClock
from tests.test_national_sources import VALENCE_RIVER, flow, river
from tests.vigilance_upstream import Upstream, make_service

SAMPLE_KEY = "mf_secret_token_1234567890abcdef"


@pytest.fixture()
def upstream() -> Upstream:
    return Upstream()


def _sample_map_payload(*, drome_flood_color: int = 1) -> dict:
    return {
        "product": {
            "warning_type": "vigilance",
            "update_time": "2026-10-20T06:00:00Z",
            "domain_id": "FRA",
            "global_max_color_id": 3,
            "periods": [
                {
                    "echeance": "J",
                    "begin_validity_time": "2026-10-20T06:00:00Z",
                    "end_validity_time": "2026-10-21T00:00:00Z",
                    "timelaps": {
                        "domain_ids": [
                            {
                                "domain_id": "FRA",
                                "max_color_id": 3,
                                "phenomenon_items": [
                                    {"phenomenon_id": "9", "phenomenon_max_color_id": 3, "timelaps_items": []}
                                ],
                            },
                            {
                                "domain_id": "64",
                                "max_color_id": 2,
                                "phenomenon_items": [
                                    {
                                        "phenomenon_id": "1",
                                        "phenomenon_max_color_id": 2,
                                        "timelaps_items": [
                                            {
                                                "begin_time": "2026-10-20T06:00:00Z",
                                                "end_time": "2026-10-20T12:00:00Z",
                                                "color_id": 1,
                                            },
                                            {
                                                "begin_time": "2026-10-20T12:00:00Z",
                                                "end_time": "2026-10-20T21:00:00Z",
                                                "color_id": 2,
                                            },
                                        ],
                                    }
                                ],
                            },
                            {
                                "domain_id": "6410",
                                "max_color_id": 3,
                                "phenomenon_items": [
                                    {
                                        "phenomenon_id": "9",
                                        "phenomenon_max_color_id": 3,
                                        "timelaps_items": [
                                            {
                                                "begin_time": "2026-10-20T14:00:00Z",
                                                "end_time": "2026-10-20T22:00:00Z",
                                                "color_id": 3,
                                            }
                                        ],
                                    }
                                ],
                            },
                            {
                                "domain_id": "26",
                                "max_color_id": max(2, drome_flood_color),
                                "phenomenon_items": [
                                    {
                                        "phenomenon_id": "3",
                                        "phenomenon_max_color_id": 2,
                                        "timelaps_items": [
                                            {
                                                "begin_time": "2026-10-20T06:00:00Z",
                                                "end_time": "2026-10-20T20:00:00Z",
                                                "color_id": 2,
                                            }
                                        ],
                                    },
                                    {
                                        "phenomenon_id": "4",
                                        "phenomenon_max_color_id": drome_flood_color,
                                        "timelaps_items": [],
                                    },
                                ],
                            },
                        ]
                    },
                }
            ],
        }
    }


def _sample_texts_payload() -> dict:
    return {
        "product": {
            "update_time": "2026-10-20T06:00:00Z",
            "text_bloc_items": [
                {
                    "domain_id": "64",
                    "domain_name": "Pyrénées-Atlantiques",
                    "bloc_title": "Bulletin de suivi départemental : Pyrénées-Atlantiques (64)",
                    "bloc_items": [
                        {
                            "id": "DEP_SITUATION",
                            "text_items": [
                                {
                                    "term_items": [
                                        {
                                            "subdivision_text": [
                                                {
                                                    "underline_text": "",
                                                    "bold_text": "Qualification :",
                                                    "text": ["Forte houle d'ouest-nord-ouest sur la côte basque."],
                                                }
                                            ]
                                        }
                                    ]
                                }
                            ],
                        }
                    ],
                }
            ],
        }
    }


def test_open_meteo_urls_default_and_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VIGIE_OPEN_METEO_FORECAST_URL", raising=False)
    monkeypatch.delenv("VIGIE_OPEN_METEO_MARINE_URL", raising=False)
    assert OpenMeteoCollector().BASE_URL == DEFAULT_OPEN_METEO_FORECAST_URL
    assert OpenMeteoMarineCollector().BASE_URL == DEFAULT_OPEN_METEO_MARINE_URL
    surf = SurfForecastCollector()
    assert surf.FORECAST_URL == DEFAULT_OPEN_METEO_FORECAST_URL
    assert surf.MARINE_URL == DEFAULT_OPEN_METEO_MARINE_URL

    monkeypatch.setenv("VIGIE_OPEN_METEO_FORECAST_URL", "https://meteo.example.org/v1/meteofrance")
    monkeypatch.setenv("VIGIE_OPEN_METEO_MARINE_URL", "https://meteo.example.org/v1/marine")
    assert OpenMeteoCollector().BASE_URL == "https://meteo.example.org/v1/meteofrance"
    assert OpenMeteoMarineCollector().BASE_URL == "https://meteo.example.org/v1/marine"
    surf_custom = SurfForecastCollector()
    assert surf_custom.FORECAST_URL == "https://meteo.example.org/v1/meteofrance"
    assert surf_custom.MARINE_URL == "https://meteo.example.org/v1/marine"


def test_every_french_department_maps_to_a_bundled_nuts3_shape() -> None:
    geometries = default_geometries()
    assert len(DEPARTMENTS) == 96
    for dep_code, (nuts3, name) in DEPARTMENTS.items():
        assert geometries.has("NUTS3", nuts3), f"Missing NUTS3 shape for {dep_code} ({name}): {nuts3}"


def test_meteofrance_key_validation_and_redaction() -> None:
    assert meteofrance_key_from_env({}) == (None, None)
    assert meteofrance_key_from_env({"VIGIE_METEOFRANCE_API_KEY": "short"})[0] is None
    assert "short" not in (meteofrance_key_from_env({"VIGIE_METEOFRANCE_API_KEY": "short"})[1] or "")
    key, problem = meteofrance_key_from_env({"VIGIE_METEOFRANCE_API_KEY": SAMPLE_KEY})
    assert key == SAMPLE_KEY and problem is None
    collector = MeteoFranceVigilanceCollector(SAMPLE_KEY)
    assert SAMPLE_KEY not in collector.redact(f"401 for {SAMPLE_KEY}")


def test_parse_vigilance_maps_coastal_and_departmental_domains() -> None:
    status = parse_vigilance(_sample_map_payload(), _sample_texts_payload())
    assert status.updated_utc == 1_792_476_000
    assert len(status.vigilances) == 3
    coastal = status.vigilances[0]
    assert coastal.source == "meteofrance"
    assert coastal.level == SeverityLevel.ORANGE
    assert coastal.phenomenon == VigilancePhenomenon.COASTAL
    assert coastal.phenomenon_original == "Vagues-submersion"
    assert coastal.area.description == "Pyrénées-Atlantiques"
    assert [(g.scheme, g.value) for g in coastal.area.geocodes] == [("NUTS3", "FR615"), ("INSEE_DEP", "64")]
    assert "Forte houle" in (coastal.texts[0].description or "")
    assert any(p.name == "timelaps" and "Orange" in p.value for p in coastal.texts[0].parameters)


@pytest.mark.asyncio
async def test_vigilance_service_uses_meteofrance_for_france_and_deduplicates_crues(
    upstream: Upstream, sim_clock: SimClock
) -> None:
    sim_clock.advance(samples.NATIONAL_RECORDED_AT - SimClock.EPOCH)
    upstream.vigicrues = flow(river("FR_VAL", "Rhône à Valence", 3, *VALENCE_RIVER))

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("apikey") == SAMPLE_KEY
        if str(request.url) == MAP_URL:
            return httpx.Response(200, json=_sample_map_payload(drome_flood_color=2))
        if str(request.url) == TEXTS_URL:
            return httpx.Response(200, json=_sample_texts_payload())
        return httpx.Response(404)

    mf_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    mf_collector = MeteoFranceVigilanceCollector(SAMPLE_KEY, http_client=mf_client)
    service = make_service(upstream, national=True)
    service.meteofrance = mf_collector

    assert service._plan("FR") == ["meteofrance", "vigicrues"]

    # Biarritz (Pyrénées-Atlantiques, 64) matches both the coastal Vagues-submersion (6410) and the wind (64) warning.
    biarritz = await service.for_point(43.4832, -1.5586)
    assert [(v.source, v.level, v.phenomenon) for v in biarritz.vigilances] == [
        ("meteofrance", SeverityLevel.ORANGE, VigilancePhenomenon.COASTAL),
        ("meteofrance", SeverityLevel.YELLOW, VigilancePhenomenon.WIND),
    ]
    assert upstream.feed_requests == []  # MeteoAlarm was not asked for France

    # Valence (Drôme, 26): the yellow Crues warning is deduplicated because the Vigicrues section is ORANGE;
    # the yellow Orages warning stays.
    valence = await service.for_point(*samples.VALENCE)
    assert [(v.source, v.level, v.phenomenon) for v in valence.vigilances] == [
        ("vigicrues", SeverityLevel.ORANGE, VigilancePhenomenon.FLOOD),
        ("meteofrance", SeverityLevel.YELLOW, VigilancePhenomenon.THUNDERSTORM),
    ]
    await mf_client.aclose()


@pytest.mark.asyncio
async def test_when_meteofrance_is_unavailable_meteoalarm_stands_in(
    upstream: Upstream, sim_clock: SimClock
) -> None:
    sim_clock.advance(samples.NATIONAL_RECORDED_AT - SimClock.EPOCH)
    mf_client = httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(503)))
    mf_collector = MeteoFranceVigilanceCollector(SAMPLE_KEY, http_client=mf_client)
    service = make_service(upstream, national=True)
    service.meteofrance = mf_collector

    response = await service.for_point(*samples.VALENCE)
    assert [(c.source, c.status) for c in response.coverage] == [
        ("meteofrance", "unavailable"),
        ("meteoalarm", "ok"),
        ("vigicrues", "ok"),
    ]
    assert any(v.source == "meteoalarm" for v in response.vigilances)
    assert upstream.feed_requests == ["france"]
    await mf_client.aclose()
