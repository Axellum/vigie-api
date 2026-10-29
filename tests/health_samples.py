"""The recorded ECDC feeds (``tests/fixtures/health``, read on 2026-10-28) and a simulated host that serves them."""

from __future__ import annotations

import gzip
from pathlib import Path

import httpx

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "health"

# The term of each feed in its address -> the fixture.
RECORDED = {
    "1310": "ecdc_epidemiological_updates",
    "1295": "ecdc_risk_assessments",
    "1505": "ecdc_threats_reports",
}


def raw(name: str) -> bytes:
    """The bytes of a recorded feed, as the server sent them."""
    return gzip.decompress((FIXTURES / f"{name}.xml.gz").read_bytes())


def etag(name: str) -> str:
    for line in (FIXTURES / f"{name}.headers.txt").read_text(encoding="utf-8").splitlines():
        if line.lower().startswith("etag:"):
            return line.split(":", 1)[1].strip()
    raise AssertionError(f"{name} has no ETag")


def host(request: httpx.Request) -> httpx.Response:
    """``www.ecdc.europa.eu`` as recorded: each feed with its ``ETag``, and ``304`` to a request that carries it."""
    term = request.url.path.removeprefix("/en/taxonomy/term/").removesuffix("/feed")
    if term not in RECORDED:
        return httpx.Response(404)
    name = RECORDED[term]
    if request.headers.get("If-None-Match") == etag(name):
        return httpx.Response(304, headers={"ETag": etag(name)})
    return httpx.Response(200, content=raw(name), headers={"ETag": etag(name), "Content-Type": "application/rss+xml"})
