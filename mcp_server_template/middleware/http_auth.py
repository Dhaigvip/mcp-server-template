"""
HTTP-transport auth middleware.

Validates the incoming Authorization header against whichever AuthProvider
build_asgi_app() constructed from config (see server.py). stdio has no
equivalent — there's no request header to check on a trusted local
subprocess, so this only wraps the HTTP transport.
"""
from __future__ import annotations

import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from mcp_server_template.auth import AuthenticationError, AuthProvider

logger = logging.getLogger(__name__)


class AuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, provider: AuthProvider | None) -> None:
        super().__init__(app)
        self._provider = provider

    async def dispatch(self, request: Request, call_next) -> Response:
        if self._provider is not None:
            token = request.headers.get("Authorization")
            try:
                self._provider.authenticate(token)
            except AuthenticationError as exc:
                logger.warning("HTTP auth failed: %s", exc)
                return Response(f"Unauthorized: {exc}", status_code=401, media_type="text/plain")
        return await call_next(request)
