"""
Entry point: python -m mcp_server_template

Subcommands:
  python -m mcp_server_template                                    run the server
  MCP_TRANSPORT=http python -m mcp_server_template                 run over HTTP
  python -m mcp_server_template generate --schema-file demo-schema.graphql
                                                                     print tools generated
                                                                     from a schema file
  python -m mcp_server_template generate --schema-file demo-schema.graphql --json -o tools.json
                                                                     same, as JSON to a file
"""

from __future__ import annotations

import argparse
import atexit
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

# stdio transport uses stdout exclusively for MCP protocol messages — any
# other output on stdout corrupts the stream, so logging must go to stderr.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("mcp_server_template")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="mcp-server-template",
        description=(
            "mcp-server-template server.\n\n"
            "  mcp-server-template                                             run the server\n"
            "  mcp-server-template generate --schema-file demo-schema.graphql  "
            "show tools from a schema file\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command")

    gen = sub.add_parser("generate", help="Show tools generated from a schema file")
    gen.add_argument("--schema-file", required=True, help="Path to schema file (.graphql or .json)")
    gen.add_argument("--json", dest="emit_json", action="store_true", help="JSON output")
    gen.add_argument("--output", "-o", help="Write to file instead of stdout")

    args = parser.parse_args()

    if args.command == "generate":
        _generate(args)
    else:
        _run_server()


def _maybe_start_mock_backend() -> None:
    """
    When MOCK_BACKEND=1, start the bundled mock GraphQL backend as a subprocess
    and auto-configure GRAPHQL_BASE_URL and SCHEMA_FILE to point at it.

    MOCK_BACKEND_PORT controls the port (default 4000).
    """
    if not os.environ.get("MOCK_BACKEND"):
        return

    port = int(os.environ.get("MOCK_BACKEND_PORT", "4000"))
    os.environ.setdefault("GRAPHQL_BASE_URL", f"http://127.0.0.1:{port}")

    # Auto-set SCHEMA_FILE to the demo schema when not already configured.
    if not os.environ.get("SCHEMA_FILE"):
        demo = Path(__file__).parent.parent / "demo-schema.graphql"
        if demo.exists():
            os.environ["SCHEMA_FILE"] = str(demo)

    logger.info("MOCK_BACKEND=1 — starting mock GraphQL backend on port %d", port)
    proc = subprocess.Popen(
        [sys.executable, "-m", "mock_backend", "--port", str(port)],
        stdout=sys.stderr,
        stderr=sys.stderr,
    )
    atexit.register(proc.terminate)

    # Poll /schema until the server is ready (up to 10 s).
    url = f"http://127.0.0.1:{port}/schema"
    deadline = time.monotonic() + 10
    import urllib.request
    import urllib.error

    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
            logger.info("Mock backend ready at http://127.0.0.1:%d", port)
            return
        except (urllib.error.URLError, OSError):
            time.sleep(0.25)

    logger.warning("Mock backend did not respond within 10 s — continuing anyway")


def _run_server() -> None:
    _maybe_start_mock_backend()

    from mcp_server_template.config import load_config
    import uvicorn

    config = load_config()
    transport = os.environ.get("MCP_TRANSPORT", "stdio").lower()
    logger.info(
        "mcp-server-template starting — transport=%s  base_url=%s  port=%s",
        transport,
        config.graphql.base_url,
        config.server.port,
    )

    if transport == "http":
        from mcp_server_template.server import build_asgi_app

        uvicorn.run(build_asgi_app(config), host=config.server.host, port=config.server.port)
    elif transport == "stdio":
        from mcp_server_template.server import create_mcp

        mcp = create_mcp(config)
        mcp.run(transport="stdio")
    else:
        logger.error("Unknown MCP_TRANSPORT=%r — valid values: 'http', 'stdio'", transport)
        sys.exit(1)


def _tools_from_schema_file(schema_file: str) -> list:
    """Build the server in-process from a schema file and list its tools
    directly — no live backend or running server needed."""
    import asyncio

    os.environ["SCHEMA_FILE"] = schema_file
    os.environ.setdefault("GRAPHQL_BASE_URL", "http://localhost")
    os.environ.setdefault("FASTMCP_LOG_LEVEL", "WARNING")
    logging.getLogger().setLevel(logging.WARNING)

    from mcp_server_template.config import load_config
    from mcp_server_template.server import create_mcp

    mcp = create_mcp(load_config())
    tools = asyncio.run(mcp.list_tools())
    return sorted(tools, key=lambda t: (t.description or "", t.name))


def _generate(args: argparse.Namespace) -> None:
    tools = _tools_from_schema_file(args.schema_file)

    if not tools:
        print("No tools registered.")
        return

    if args.emit_json:
        output = json.dumps(
            [
                {"name": t.name, "description": t.description, "inputSchema": t.parameters}
                for t in tools
            ],
            indent=2,
        )
    else:
        lines = []
        for tool in tools:
            lines.append(f"\n{tool.name}")
            lines.append(f"  {tool.description}")
            lines.append(f"  {json.dumps(tool.parameters, indent=4)}")
        lines.append(f"\n— {len(tools)} tool(s) —")
        output = "\n".join(lines)

    if args.output:
        from pathlib import Path

        Path(args.output).write_text(output, encoding="utf-8")
        print(f"Written to {args.output}  ({len(tools)} tool(s))")
    else:
        print(output)


if __name__ == "__main__":
    main()
