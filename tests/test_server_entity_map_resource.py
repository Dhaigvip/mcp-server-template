"""End-to-end check that the entity map is actually fetchable, not just
buildable. `render_entity_map()` existed since Task 8 with nothing calling
it — this confirms create_mcp() now registers it as a real MCP resource a
client can read, using the repo's own demo-schema.graphql (no live GraphQL
backend needed, same as tool generation itself).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastmcp import Client

from mcp_server_template.config import AppConfig, GraphQLConfig, ServerConfig
from mcp_server_template.server import create_mcp

_DEMO_SCHEMA = Path(__file__).parent.parent / "demo-schema.graphql"

_ENTITY_MAP_URI = "resource://entity-map"


def _config_with_schema_file() -> AppConfig:
    return AppConfig(
        # base_url is never dialed — schema comes from the file, and this
        # test never calls a tool, only reads the entity-map resource.
        graphql=GraphQLConfig(base_url="http://unused.invalid"),
        server=ServerConfig(schema_file=str(_DEMO_SCHEMA)),
    )


@pytest.mark.asyncio
async def test_entity_map_resource_is_registered_and_listed():
    mcp = create_mcp(_config_with_schema_file())
    async with Client(mcp) as client:
        resources = await client.list_resources()
        uris = {str(r.uri) for r in resources}
        assert _ENTITY_MAP_URI in uris


@pytest.mark.asyncio
async def test_entity_map_resource_returns_real_schema_content():
    mcp = create_mcp(_config_with_schema_file())
    async with Client(mcp) as client:
        contents = await client.read_resource(_ENTITY_MAP_URI)
        assert len(contents) == 1
        text = contents[0].text

        # Real entities from demo-schema.graphql, not a placeholder.
        assert "projects (get_projects):" in text
        assert "tasks (get_tasks):" in text
        # Nested relations render with their include path.
        assert '[include="tasks"]' in text


@pytest.mark.asyncio
async def test_entity_map_resource_absent_when_no_schema_file_configured():
    # No schema_file → no tools registered (see create_mcp's own warning
    # path) → nothing to build an entity map from, so the resource shouldn't
    # be registered either rather than existing and returning an empty string.
    config = AppConfig(graphql=GraphQLConfig(base_url="http://unused.invalid"))
    mcp = create_mcp(config)
    async with Client(mcp) as client:
        resources = await client.list_resources()
        uris = {str(r.uri) for r in resources}
        assert _ENTITY_MAP_URI not in uris
