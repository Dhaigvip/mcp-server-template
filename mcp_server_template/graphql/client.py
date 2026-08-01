"""
GraphQL client for the backend this server introspects and queries.

Auth is a single static credential from GraphQLConfig.api_token, sent as a
Bearer header on every call — not per-caller forwarding. (The reference this
is ported from forwards each inbound caller's own cookie/token through
ContextVars set by request middleware; this template has no per-caller
backend identity to forward, so that machinery isn't here. If your backend
needs per-caller credentials, that's the seam to add it back at.)

Retries once on 503 (transient backend issue) — a generic resilience
pattern, not tied to any specific backend.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from mcp_server_template.config import GraphQLConfig

logger = logging.getLogger(__name__)


class GraphQLClient:
    def __init__(self, config: GraphQLConfig) -> None:
        self._base_url = config.base_url
        self._endpoint = config.graphql_endpoint
        self._schema_ep = config.schema_endpoint
        self._api_token = config.api_token
        # Single shared client — avoids connection setup overhead per call.
        self._http = httpx.AsyncClient(timeout=30.0, verify=config.verify_ssl)

    async def aclose(self) -> None:
        await self._http.aclose()

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self._api_token:
            headers["Authorization"] = f"Bearer {self._api_token}"
        return headers

    async def _post_with_retry(self, url: str, headers: dict, body: dict) -> dict:
        """POST with one 503 retry. Returns parsed JSON body."""
        for attempt in range(2):
            try:
                resp = await self._http.post(url, headers=headers, json=body)
            except httpx.RequestError as exc:
                raise RuntimeError(f"Network error calling GraphQL backend: {exc}") from exc

            if resp.status_code == 503 and attempt == 0:
                logger.warning("503 from backend — retrying after 500ms")
                await asyncio.sleep(0.5)
                continue

            if not resp.is_success:
                hint = {
                    401: " Token invalid or expired.",
                    403: " Permission denied.",
                    400: " Bad request — check query syntax.",
                }.get(resp.status_code, "")
                raise RuntimeError(
                    f"GraphQL backend HTTP {resp.status_code}{hint}\n"
                    f"  URL:  {resp.url}\n"
                    f"  body: {resp.text[:400]}"
                )

            try:
                return resp.json()
            except Exception as exc:
                raise RuntimeError(f"Failed to parse backend response: {exc}") from exc

        raise RuntimeError("GraphQL backend: 503 persisted after retry")

    async def _get_with_retry(self, url: str, headers: dict) -> Any:
        """GET with one 503 retry. Returns parsed JSON body."""
        for attempt in range(2):
            try:
                resp = await self._http.get(url, headers=headers)
            except httpx.RequestError as exc:
                raise RuntimeError(f"Network error calling GraphQL backend: {exc}") from exc

            if resp.status_code == 503 and attempt == 0:
                logger.warning("503 from backend — retrying after 500ms")
                await asyncio.sleep(0.5)
                continue

            if not resp.is_success:
                raise RuntimeError(f"GraphQL backend HTTP {resp.status_code}: {resp.text[:400]}")

            try:
                return resp.json()
            except Exception as exc:
                raise RuntimeError(f"Failed to parse backend response: {exc}") from exc

        raise RuntimeError("GraphQL backend: 503 persisted after retry")

    # ── Public API ────────────────────────────────────────────────────────────

    async def execute(self, query: str) -> dict[str, Any]:
        """Execute a GraphQL query or mutation."""
        url = f"{self._base_url}{self._endpoint}"
        headers = self._headers()

        logger.info("GraphQL →  url=%s\n  query=%s", url, query)

        envelope = await self._post_with_retry(url, headers, {"query": query})

        logger.debug("GraphQL raw envelope: %s", envelope)

        if errors := envelope.get("errors"):
            messages = "; ".join(e.get("message", str(e)) for e in errors)
            logger.warning("GraphQL errors — data=%s  errors=%s", envelope.get("data"), errors)
            raise RuntimeError(f"GraphQL error: {messages}")

        if "data" not in envelope:
            raise RuntimeError("GraphQL: response contained neither data nor errors")

        return envelope["data"]

    async def fetch_schema(self) -> Any:
        """
        Fetch schema via a dedicated schema endpoint, when the backend has one.

        Not every GraphQL backend exposes this — it's an optional fast path
        ahead of standard __schema introspection (see introspection.py's
        introspect_schema(), which tries this first and falls back to the
        standard introspection query on failure).
        """
        if not self._schema_ep:
            raise RuntimeError(
                "Schema endpoint not configured — set GRAPHQL_SCHEMA_ENDPOINT"
            )
        url = f"{self._base_url}{self._schema_ep}"
        headers = {k: v for k, v in self._headers().items() if k != "Content-Type"}

        body = await self._get_with_retry(url, headers)

        # Unwrap standard GraphQL envelope { data: ... } if present.
        if isinstance(body, dict) and "data" in body:
            return body["data"]
        return body
