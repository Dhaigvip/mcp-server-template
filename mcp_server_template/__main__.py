"""
Entry point: python -m mcp_server_template

MCP_TRANSPORT selects the transport:
  MCP_TRANSPORT=stdio (default)  — stdin/stdout, what most MCP clients speak
  MCP_TRANSPORT=http             — not implemented yet
"""

from __future__ import annotations

import logging
import os
import sys

# stdio transport uses stdout exclusively for MCP protocol messages — any
# other output on stdout corrupts the stream, so logging must go to stderr.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("mcp_server_template")


def main() -> None:
    _run_server()


def _run_server() -> None:
    transport = os.environ.get("MCP_TRANSPORT", "stdio").lower()
    logger.info("mcp-server-template starting — transport=%s", transport)

    if transport == "http":
        logger.error("MCP_TRANSPORT=http is not implemented yet — use 'stdio'")
        sys.exit(1)
    elif transport == "stdio":
        from mcp_server_template.server import create_mcp

        mcp = create_mcp()
        mcp.run(transport="stdio")
    else:
        logger.error("Unknown MCP_TRANSPORT=%r — valid values: 'http', 'stdio'", transport)
        sys.exit(1)


if __name__ == "__main__":
    main()
