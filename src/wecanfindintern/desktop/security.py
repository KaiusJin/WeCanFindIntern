"""Security boundary for the loopback-only Electron sidecar."""

from __future__ import annotations

import hmac
from collections.abc import Awaitable, Callable

from fastapi import Request
from starlette.responses import JSONResponse, Response

PROTECTED_PREFIXES = ("/api/", "/desktop/")


def requires_desktop_token(path: str) -> bool:
    return path == "/health" or path.startswith(PROTECTED_PREFIXES)


async def enforce_desktop_token(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
    *,
    expected_token: str,
) -> Response:
    if requires_desktop_token(request.url.path):
        supplied = request.headers.get("x-wecanfindintern-token", "")
        if not supplied or not hmac.compare_digest(supplied, expected_token):
            return JSONResponse({"detail": "Desktop authentication required."}, status_code=401)
    return await call_next(request)
