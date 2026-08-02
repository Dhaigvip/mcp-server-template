"""
Starlette app exposing two endpoints:

  POST /graphql   — Strawberry GraphQL execution (also GET for GraphiQL playground)
  GET  /schema    — raw SDL text of demo-schema.graphql

Usage:
  Set GRAPHQL_BASE_URL=http://localhost:4000 when running the MCP server.
  Optionally set GRAPHQL_SCHEMA_ENDPOINT=/schema to exercise the schema-fetch path.
"""

from pathlib import Path

from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Mount, Route
from strawberry.asgi import GraphQL

from mock_backend.schema import schema

_SDL_PATH = Path(__file__).parent.parent / "demo-schema.graphql"


async def schema_handler(request: Request) -> PlainTextResponse:
    return PlainTextResponse(_SDL_PATH.read_text(encoding="utf-8"), media_type="text/plain")


graphql_app = GraphQL(schema)

# Mount("/graphql", graphql_app) would 307-redirect POST /graphql → /graphql/.
# Instead, mount the GraphQL app at root so any path (including /graphql) reaches
# it directly; the /schema Route is evaluated first and takes priority.
app = Starlette(
    routes=[
        Route("/schema", endpoint=schema_handler, methods=["GET"]),
        Mount("/", app=graphql_app),
    ]
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
