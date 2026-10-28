"""The recorded answers of the travel advice sources (``tests/fixtures/travel``, read on 2026-10-28) and simulated hosts that serve them."""

from __future__ import annotations

import gzip
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "travel"

# The FCDO pages recorded (the index lists 226): page name -> fixture.
GB_RECORDED = {
    "france": "gb_france",
    "mexico": "gb_mexico",
    "ukraine": "gb_ukraine",
    "turkey": "gb_turkey",
    "russia": "gb_russia",
    "cuba": "gb_cuba",
    "cook-islands-tokelau-and-niue": "gb_cook_islands",
}


def raw(name: str) -> bytes:
    """The bytes of a recorded answer, as the server sent them."""
    return gzip.decompress((FIXTURES / f"{name}.json.gz").read_bytes())


def payload(name: str) -> Any:
    return json.loads(raw(name))


def headers(name: str) -> dict[str, str]:
    """The recorded headers of an answer (``ETag``, ``Last-Modified``...)."""
    found: dict[str, str] = {}
    for line in (FIXTURES / f"{name}.headers.txt").read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        found[key.strip()] = value.strip()
    return found


def nl_items() -> list[Any]:
    return payload("nl_traveladvice_offset0") + payload("nl_traveladvice_offset200")


def gb_index(only: tuple[str, ...] | None = None) -> dict[str, Any]:
    """The recorded FCDO index, or the same index keeping only the pages ``only`` (a test that does not want 226 pages)."""
    index = payload("gb_index")
    if only is not None:
        index["links"]["children"] = [c for c in index["links"]["children"] if c["details"]["country"]["slug"] in only]
    return index


def gb_page(page: str) -> dict[str, Any]:
    return payload(GB_RECORDED[page])


def hosts() -> dict[str, Callable[[httpx.Request], httpx.Response]]:
    """Every travel advice host as recorded, keyed by host (the FCDO serves its recorded pages, and 404 for the others)."""

    def nl(request: httpx.Request) -> httpx.Response:
        offset = request.url.params.get("offset", "0")
        return httpx.Response(200, content=raw(f"nl_traveladvice_offset{offset}")) if offset in ("0", "200") else httpx.Response(200, json=[])

    def ca(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("index-updated.json"):
            return httpx.Response(200, content=raw("ca_index_updated"), headers=headers("ca_index_updated"))
        if request.url.path.endswith("cta-cap-mx.json"):
            return httpx.Response(200, content=raw("ca_cta_cap_mx"), headers=headers("ca_cta_cap_mx"))
        return httpx.Response(404)

    def gb(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/content/foreign-travel-advice":
            return httpx.Response(200, content=raw("gb_index"))
        match = re.fullmatch(r"/api/content/foreign-travel-advice/([a-z0-9-]+)", path)
        if match and match.group(1) in GB_RECORDED:
            return httpx.Response(200, content=raw(GB_RECORDED[match.group(1)]))
        return httpx.Response(404)

    return {
        "cadataapi.state.gov": lambda r: httpx.Response(200, content=raw("us_travel_advisories")),
        "data.international.gc.ca": ca,
        "www.smartraveller.gov.au": lambda r: httpx.Response(200, content=raw("au_destinations_export"), headers=headers("au_destinations_export")),
        "opendata.nederlandwereldwijd.nl": nl,
        "www.gov.uk": gb,
    }
