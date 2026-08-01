"""
MCP server template — core wiring.

create_mcp() builds the FastMCP instance and registers its tools. Every
transport (stdio now, HTTP later) calls this one function to get "the app",
so tools registered here are available regardless of which transport is
running.

Schema loading — file path only (for now):
  SCHEMA_FILE set (see .env.example) — tools generated from the file at
    startup via introspect_from_file(). No live backend needed to start.
  SCHEMA_FILE not set — no tools registered; a warning is logged.

  The dynamic path (fetch schema from a live backend on first request,
  see the reference's schema_manager.py) only makes sense once there's an
  HTTP transport to hang "first request" off of — stdio has no such
  moment. Deferred to whenever Task 3b (HTTP transport) lands.

Overrides:
  mcp_server_template/definitions/overrides.json — optional, applied after
  introspection. Supports: skip (bool), description (str) per entity.
"""
from __future__ import annotations

import contextlib
import logging
from pathlib import Path

from fastmcp import FastMCP
from fastmcp.server.middleware.error_handling import ErrorHandlingMiddleware
from fastmcp.server.middleware.logging import LoggingMiddleware

from mcp_server_template.config import AppConfig
from mcp_server_template.graphql.client import GraphQLClient
from mcp_server_template.graphql.introspection import introspect_from_file
from mcp_server_template.registry.entity_map import set_entity_defs
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
        logger.info("Schema file: registered tools for %d entities", len(entity_defs))
    else:
        logger.warning(
            "No SCHEMA_FILE configured — no tools registered. Set SCHEMA_FILE (see "
            ".env.example) to generate tools from a schema at startup."
        )

    return mcp


# ── Middleware ────────────────────────────────────────────────────────────────
# FastMCP's Middleware wraps every tool call (on_call_tool) regardless of
# transport — stdio and HTTP both go through the same chain, so it's built
# here rather than per-transport. Order matters: add_middleware() appends,
# and the chain wraps outer-to-inner in add order, so the first middleware
# added is outermost.
#
# Auth is a hook point in this same chain, not implemented yet — when it
# lands it's another Middleware subclass added here (its own on_call_tool,
# rejecting unauthenticated calls before they reach the tool).


def _register_middleware(mcp: FastMCP) -> None:
    # Outermost: logs the final outcome, including errors already normalized
    # by ErrorHandlingMiddleware below it.
    mcp.add_middleware(LoggingMiddleware())
    # Innermost: closest to the actual tool call, catches raw exceptions and
    # converts them to proper MCP error responses before they bubble up.
    mcp.add_middleware(ErrorHandlingMiddleware())
