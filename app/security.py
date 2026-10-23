"""The optional admin token: when ``VIGIE_ADMIN_TOKEN`` is set, every route that changes something asks for it.

Without the setting nothing changes: the API trusts the network it runs on, as it always did (``docs/architecture.md``, "Security"). With
it, **every request whose method is not a read** (anything but ``GET``, ``HEAD`` and ``OPTIONS``: ``POST``, ``PUT``, ``PATCH``, ``DELETE``,
and any other) must carry ``Authorization: Bearer <token>``, else the answer is 401 and nothing is read from the body or changed.

It is a middleware on the method, not a list of routes or a dependency on each of them, so a route added tomorrow is covered without
anyone remembering to protect it; ``tests/test_admin_token.py`` lists the routes of the application and fails if a write route answers
without the token. The reads never need it. It stacks with the switches that turn a route off (``VIGIE_ENABLE_SIMULATION``,
``VIGIE_ENABLE_WATCHED_POINTS_WRITE``): the token is checked first, then the switch answers 403 as before.

The WebSocket ``/v1/stream`` is not an HTTP write: a client can ask it to follow positions, which lives in memory for the length of the
connection and changes nothing on the instance. It is not covered.

The token is compared in constant time (a digest of each side, so that the length of the real one is not revealed either), and it is
never written anywhere: not in a log line, not in an answer, not in ``GET /v1/health`` (which only says that one is set).
"""

from __future__ import annotations

import hashlib
import hmac
import os
from typing import Any, Optional

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

ADMIN_TOKEN_SETTING = "VIGIE_ADMIN_TOKEN"
READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})  # the ones the documents name; every method that is not a read needs the token

MISSING_TOKEN_MESSAGE = (
    "This instance asks for its admin token to change anything: send the header `Authorization: Bearer <token>` "
    f"(the token is the instance's {ADMIN_TOKEN_SETTING} setting)."
)
WRONG_TOKEN_MESSAGE = f"The admin token is not the one of this instance ({ADMIN_TOKEN_SETTING}). Send `Authorization: Bearer <token>`."


def admin_token() -> Optional[str]:
    """The token write requests must carry, or ``None`` when the instance sets none (blank counts as none). Read at each request."""
    value = os.environ.get(ADMIN_TOKEN_SETTING, "").strip()
    return value or None


def bearer_token(authorization: Optional[str]) -> Optional[str]:
    """The credentials of an ``Authorization: Bearer ...`` header (the scheme is case-insensitive), else ``None``."""
    if not authorization:
        return None
    scheme, _, credentials = authorization.strip().partition(" ")
    return (credentials.strip() or None) if scheme.lower() == "bearer" else None


def token_matches(presented: str, expected: str) -> bool:
    """Constant-time comparison: the digests are compared, so neither the content nor the length of ``expected`` leaks through the time."""
    return hmac.compare_digest(hashlib.sha256(presented.encode("utf-8")).digest(), hashlib.sha256(expected.encode("utf-8")).digest())


def refusal(presented: Optional[str], expected: str) -> Optional[JSONResponse]:
    """The 401 answer when the request may not go on, else ``None``. It never repeats what the client sent."""
    if presented is not None and token_matches(presented, expected):
        return None
    message = MISSING_TOKEN_MESSAGE if presented is None else WRONG_TOKEN_MESSAGE
    return JSONResponse({"detail": message}, status_code=401, headers={"WWW-Authenticate": "Bearer"})


class AdminTokenMiddleware:
    """Refuses (401) a request that is not a read when the instance has an admin token and the request does not carry it."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and str(scope.get("method", "")).upper() not in READ_METHODS:
            expected = admin_token()
            if expected is not None:
                refused = refusal(bearer_token(Headers(scope=scope).get("authorization")), expected)
                if refused is not None:
                    await refused(scope, receive, send)
                    return
        await self.app(scope, receive, send)


def document_admin_token(schema: dict[str, Any]) -> dict[str, Any]:
    """Add to an OpenAPI document the ``AdminToken`` scheme and, on each write operation, the 401 it can answer (the token is optional)."""
    schemes = schema.setdefault("components", {}).setdefault("securitySchemes", {})
    if "AdminToken" in schemes:
        return schema
    schemes["AdminToken"] = {
        "type": "http",
        "scheme": "bearer",
        "description": f"Asked for by every route that changes something, only on an instance that sets {ADMIN_TOKEN_SETTING}.",
    }
    for path_item in schema.get("paths", {}).values():
        for method, operation in path_item.items():
            if method.upper() in WRITE_METHODS and isinstance(operation, dict):
                operation["security"] = [{}, {"AdminToken": []}]  # {} : not asked on an instance that sets no token
                operation.setdefault("responses", {}).setdefault(
                    "401", {"description": f"The instance sets {ADMIN_TOKEN_SETTING} and the request has no valid `Authorization: Bearer` header."}
                )
    return schema
