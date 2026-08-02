"""
Run the mock GraphQL backend.

  python -m mock_backend                  # http://127.0.0.1:4000
  python -m mock_backend --port 8080
  python -m mock_backend --host 0.0.0.0

Then point the MCP server at it:
  GRAPHQL_BASE_URL=http://localhost:4000 SCHEMA_FILE=./demo-schema.graphql \\
    python -m mcp_server_template
"""

import argparse
import logging

import uvicorn

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


def main() -> None:
    parser = argparse.ArgumentParser(description="Mock GraphQL backend for MCP server testing")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4000)
    args = parser.parse_args()

    print(f"Mock GraphQL backend → http://{args.host}:{args.port}/graphql")
    print(f"GraphiQL playground  → http://{args.host}:{args.port}/graphql")
    print(f"SDL schema           → http://{args.host}:{args.port}/schema")
    uvicorn.run("mock_backend.app:app", host=args.host, port=args.port, reload=False)


if __name__ == "__main__":
    main()
