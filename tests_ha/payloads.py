"""Builders of answers of ``GET /v1/vigilances`` (the shape of the real one: see ``fixtures/``)."""

from __future__ import annotations

from typing import Any

SERVER_UTC = 1_792_505_748
URL = "http://vigie.test:8080"

HEALTH_NO_PUSH = {"status": "ok", "version": "1.1.0", "server_utc": SERVER_UTC, "scheduler": {"running": False}}
HEALTH_PUSH = {"status": "ok", "version": "1.1.0", "server_utc": SERVER_UTC, "scheduler": {"running": True}}


def vigilance(
    identifier: str = "meteoalarm:doc-1:0-0-1",
    *,
    source: str = "meteoalarm",
    country: str | None = "FR",
    level: str | None = "YELLOW",
    phenomenon: str | None = "wind",
    original: str | None = "Wind",
    event: str | None = None,
    status: str | None = "new",
    onset: int | None = SERVER_UTC - 600,
    expires: int | None = SERVER_UTC + 3600,
    ends: int | None = None,
    area: str | None = "Haute-Corse",
    codes: tuple[tuple[str, str], ...] = (("NUTS3", "FR832"),),
    texts: list[dict[str, Any]] | None = None,
    distance_km: float | None = None,
) -> dict[str, Any]:
    """One warning as the API gives it (the fields the integration reads, and a few it ignores)."""
    shown = event if event is not None else f"{(level or 'unknown').title()} {phenomenon} warning"
    return {
        "id": identifier,
        "source": source,
        "country": country,
        "attribution": f"Source: {source}",
        "identifier": f"cap-{identifier}",
        "sent_utc": SERVER_UTC - 900,
        "effective_utc": None,
        "onset_utc": onset,
        "expires_utc": expires,
        "ends_utc": ends,
        "message_type": "Alert",
        "status": status,
        "level": level,
        "level_original": level.title() if level else None,
        "phenomenon": phenomenon,
        "phenomenon_original": original,
        "event": shown,
        "issuer": "TEST-SERVICE",
        "texts": texts
        if texts is not None
        else [
            {"language": "fr-FR", "headline": f"Vigilance {phenomenon}", "description": "d" * 400, "instruction": "i" * 400},
            {"language": "en-GB", "headline": shown, "description": "d" * 400, "instruction": "i" * 400},
        ],
        "area": {
            "description": area,
            "geocodes": [{"scheme": scheme, "value": value} for scheme, value in codes],
            "has_polygon": False,
            "localization": "geocode",
        },
        "distance_km": distance_km,
        "link": f"https://example.invalid/{identifier}",
        "web": "https://example.invalid/warnings",
        "derived": ["status", "level", "phenomenon"],
    }


def coverage(
    source: str = "meteoalarm", country: str | None = "FR", status: str = "ok", not_matchable: int | None = 0
) -> dict[str, Any]:
    """One line of ``coverage``."""
    return {
        "source": source,
        "country": country,
        "status": status,
        "warnings_in_feed": 10,
        "warnings_not_matchable": not_matchable,
        "feed_updated_utc": SERVER_UTC - 60,
        "fetched_age_s": 30,
        "note": None,
    }


def response(
    vigilances: list[dict[str, Any]] | None = None,
    sources: list[dict[str, Any]] | None = None,
    *,
    attribution: str | None = "Source: Test service",
    disclaimer: str | None = None,
    note: str | None = None,
    server_utc: int = SERVER_UTC,
) -> dict[str, Any]:
    """An answer of ``GET /v1/vigilances`` for a point. By default one source answered (`ok`)."""
    return {
        "version": "1.0",
        "server_utc": server_utc,
        "query": {"lat": 45.0, "lon": 5.0, "country": None, "river_radius_km": 5.0},
        "countries": ["FR"],
        "vigilances": vigilances or [],
        "coverage": [coverage()] if sources is None else sources,
        "attribution": attribution,
        "disclaimer": disclaimer,
        "note": note,
    }
