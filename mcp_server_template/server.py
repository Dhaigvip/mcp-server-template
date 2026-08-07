"""
MCP server template — core wiring.

create_mcp() builds the FastMCP instance and registers its tools. Every
transport (stdio now, HTTP later) calls this one function to get "the app",
so tools registered here are available regardless of which transport is
running.

Schema loading — file path only:
  SCHEMA_FILE set (see .env.example) — tools generated from the file at
    startup via introspect_from_file(). No live backend needed to start.
  SCHEMA_FILE not set — no tools registered; a warning is logged.

  The dynamic path (fetch schema from a live backend on first request,
  see the reference's schema_manager.py) needs an HTTP "first request"
  moment that file-path loading doesn't — not implemented; could be added
  later if a use case needs it.

Overrides:
  mcp_server_template/definitions/overrides.json — optional, applied after
  introspection. Supports: skip (bool), description (str) per entity.

Authentication (HTTP transport only — stdio is a trusted local subprocess,
no request headers to check):
  build_asgi_app() picks an AuthProvider from config, in order:
    OIDC configured (OIDC_DISCOVERY_URL or OIDC_JWKS_URI) → OidcAuthProvider
    DEV_TOKEN set, no OIDC → StaticTokenAuthProvider (dev/test only)
    neither set → no auth at all (every request accepted) — logged loudly
  See auth/provider.py for the AuthProvider protocol itself.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path

from fastmcp import FastMCP
from fastmcp.server.middleware.error_handling import ErrorHandlingMiddleware
from fastmcp.server.middleware.logging import LoggingMiddleware

from mcp_server_template.auth import AuthProvider, JwtValidator, OidcAuthProvider, StaticTokenAuthProvider
from mcp_server_template.config import AppConfig
from mcp_server_template.graphql.client import GraphQLClient
from mcp_server_template.graphql.introspection import introspect_from_file
from mcp_server_template.middleware.http_auth import AuthMiddleware
from mcp_server_template.registry.entity_map import render_entity_map, set_entity_defs
from mcp_server_template.registry.loader import load_overrides, raw_defs_to_entity_defs
from mcp_server_template.registry.tool_factory import register_entity_tools

logger = logging.getLogger(__name__)

_DEFINITIONS_DIR = Path(__file__).parent / "definitions"


def create_mcp(config: AppConfig) -> FastMCP:
    """Build a FastMCP instance with its middleware and tools registered."""
    client = GraphQLClient(config.graphql)
    overrides = load_overrides(_DEFINITIONS_DIR)

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        yield
        await client.aclose()

    mcp = FastMCP("mcp-server-template", version="0.1.0", lifespan=lifespan)
    _register_middleware(mcp)

    if config.server.schema_file:
        logger.info("Schema file: loading from %s", config.server.schema_file)
        raw_defs = introspect_from_file(config.server.schema_file)
        entity_defs = raw_defs_to_entity_defs(raw_defs, overrides)
        set_entity_defs(entity_defs)
        for defn in entity_defs.values():
            register_entity_tools(mcp, defn, client)
        _register_entity_map_resource(mcp)
        logger.info("Schema file: registered tools for %d entities", len(entity_defs))
    else:
        logger.warning(
            "No SCHEMA_FILE configured — no tools registered. Set SCHEMA_FILE (see "
            ".env.example) to generate tools from a schema at startup."
        )

    return mcp


def build_asgi_app(config: AppConfig):
    """Build the ASGI app for HTTP transport (uvicorn)."""
    from starlette.middleware import Middleware

    mcp = create_mcp(config)
    auth_provider = asyncio.run(_build_auth_provider(config))

    return mcp.http_app(
        middleware=[
            Middleware(AuthMiddleware, provider=auth_provider),
        ]
    )


async def _build_auth_provider(config: AppConfig) -> AuthProvider | None:
    """Build the AuthProvider HTTP requests get checked against, from OIDC/dev-token
    config. None disables auth entirely — every request accepted."""
    oidc = config.oidc
    if oidc.discovery_url:
        logger.info("OIDC: resolving JWKS via discovery → %s", oidc.discovery_url)
        validator = await JwtValidator.from_discovery(oidc.discovery_url, audience=oidc.audience)
        return OidcAuthProvider(validator)
    if oidc.jwks_uri:
        logger.info("OIDC: using JWKS URI directly → %s", oidc.jwks_uri)
        validator = await JwtValidator.from_jwks_uri(oidc.jwks_uri, audience=oidc.audience)
        return OidcAuthProvider(validator)
    if config.server.dev_token:
        logger.warning("OIDC not configured — using DEV_TOKEN static auth (dev/test only)")
        return StaticTokenAuthProvider(config.server.dev_token)
    logger.warning(
        "Neither OIDC nor DEV_TOKEN configured — HTTP transport has NO authentication, "
        "every request is accepted"
    )
    return None


# ── Middleware ────────────────────────────────────────────────────────────────
# FastMCP's Middleware wraps every tool call (on_call_tool) regardless of
# transport — stdio and HTTP both go through the same chain, so it's built
# here rather than per-transport. Order matters: add_middleware() appends,
# and the chain wraps outer-to-inner in add order, so the first middleware
# added is outermost.
#
# Auth is HTTP-only (see build_asgi_app/AuthMiddleware above) — it wraps the
# ASGI app itself, not this FastMCP tool-call chain, since it needs the raw
# HTTP request headers. stdio has no equivalent (trusted local subprocess).


def _register_entity_map_resource(mcp: FastMCP) -> None:
    """Expose the entity map as a fetchable MCP resource, not just an internal
    render function. `render_entity_map()` (registry/entity_map.py) has
    existed since Task 8 but was never wired to anything a client could
    actually reach — this is that wiring. A resource, not a tool: it's
    static reference context for a client to read before calling tools, not
    an action the agent decides to invoke mid-task.

    Content is rendered fresh on every fetch, from whatever EntityDefs are
    current (see entity_map.py's docstring on why it's lazy) — cheap enough
    that caching it isn't worth the staleness risk.
    """

    @mcp.resource(
        "resource://entity-map",
        name="entity-map",
        description=(
            "Compact summary of every queryable entity and its fields "
            "(scalar + one level of nested relations), generated from the "
            "same schema as the tools themselves. Read this before calling "
            "get_* tools so you know what fields are available up front, "
            "instead of fetching defaults and re-fetching once you learn "
            "what you actually needed."
        ),
        mime_type="text/plain",
    )
    def entity_map_resource() -> str:
        return render_entity_map()


def _register_middleware(mcp: FastMCP) -> None:
    # Outermost: logs the final outcome, including errors already normalized
    # by ErrorHandlingMiddleware below it.
    mcp.add_middleware(LoggingMiddleware())
    # Innermost: closest to the actual tool call, catches raw exceptions and
    # converts them to proper MCP error responses before they bubble up.
    mcp.add_middleware(ErrorHandlingMiddleware())
