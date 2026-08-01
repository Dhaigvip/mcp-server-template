"""
MCP server template — core wiring.

create_mcp() builds the FastMCP instance and registers its tools. Every
transport (stdio now, HTTP later) calls this one function to get "the app",
so tools registered here are available regardless of which transport is
running.
"""
from __future__ import annotations

from fastmcp import FastMCP
from fastmcp.server.middleware.error_handling import ErrorHandlingMiddleware
from fastmcp.server.middleware.logging import LoggingMiddleware


def create_mcp() -> FastMCP:
    """Build a FastMCP instance with its middleware and tools registered."""
    mcp = FastMCP("mcp-server-template", version="0.1.0")
    _register_middleware(mcp)
    _register_smoke_test_tools(mcp)
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


# ── Smoke-test tools ─────────────────────────────────────────────────────────
# Hand-written, registered directly via mcp.tool — no generator involved.
# DELETE these once the schema-driven tool generator (get_*/create_*/update_*
# from demo-schema.graphql) is in place; they exist only to prove the
# transport + FastMCP wiring works end to end before the generator exists.


def _register_smoke_test_tools(mcp: FastMCP) -> None:
    @mcp.tool
    def ping() -> str:
        """Takes no arguments, always returns "pong"."""
        return "pong"

    @mcp.tool
    def echo(text: str) -> str:
        """Returns text unchanged. Exercises a required string argument."""
        return text

    @mcp.tool
    def add(a: int, b: int) -> int:
        """Returns a + b. Exercises multiple typed arguments and a non-string return."""
        return a + b
