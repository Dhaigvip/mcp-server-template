"""
MCP server template — core wiring.

create_mcp() builds the FastMCP instance and registers its tools. Every
transport (stdio now, HTTP later) calls this one function to get "the app",
so tools registered here are available regardless of which transport is
running.
"""
from __future__ import annotations

from fastmcp import FastMCP


def create_mcp() -> FastMCP:
    """Build a FastMCP instance with its tools registered."""
    mcp = FastMCP("mcp-server-template", version="0.1.0")
    _register_smoke_test_tools(mcp)
    return mcp


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
