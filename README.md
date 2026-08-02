# mcp-server-template

A production-shaped Model Context Protocol (MCP) server template in Python,
with tools **auto-generated from a GraphQL schema** instead of hand-written one
at a time.

## What you get

- **Schema-driven tool generation** — introspect a GraphQL schema and generate
  `get_*` / `create_*` / `update_*` tools automatically, including nested
  relations exposed via dot-notation include paths.
- **Field awareness** — an entity map you can inject into a system prompt so
  agents know what fields exist *before* calling tools.
- **Both transports** — stdio (for Claude Desktop, etc.) and HTTP.
- **Auth strategies** — pluggable interface with static token and OIDC
  implementations included.
- **Middleware chain** — logging, error normalization, and a hook point for
  custom auth logic.

The schema is `demo-schema.graphql` — a generic project/task management domain
shaped to exercise every branch of the generator (list vs. single-object
queries, create/update pairs, update-only entities, read-only fields,
list-of-object inputs, and overridden queries).

## Quick start

### Build and install

```powershell
# Clone/navigate to the repo
cd mcp-server-template

# Create a Python 3.10+ venv
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Install dependencies
pip install -e ".[dev]"
```

### Run the server (stdio transport)

```powershell
# Copy and customize the example env file
cp .env.example .env
# Edit .env: set GRAPHQL_BASE_URL to your backend, SCHEMA_FILE to your schema

# Then run:
python -m mcp_server_template
```

Or pass env vars inline:

```powershell
# Quick test with demo schema (no live backend needed)
SCHEMA_FILE=./demo-schema.graphql GRAPHQL_BASE_URL=http://localhost python -m mcp_server_template

# Even quicker: auto-start the bundled mock backend alongside the MCP server
MOCK_BACKEND=1 python -m mcp_server_template

# Connect with live backend (SCHEMA_FILE optional; if set, uses cached schema)
GRAPHQL_BASE_URL=https://your-api.com GRAPHQL_API_TOKEN=your-token python -m mcp_server_template
```

The server logs to stderr (so MCP protocol over stdout stays clean). Your MCP
client (Claude Desktop, etc.) connects to this subprocess over stdio.

### Run the server (HTTP transport)

```powershell
# Start listening on http://localhost:3001
MCP_TRANSPORT=http GRAPHQL_BASE_URL=http://localhost python -m mcp_server_template

# With a static token for testing auth:
MCP_TRANSPORT=http DEV_TOKEN=test-token GRAPHQL_BASE_URL=http://localhost python -m mcp_server_template

# With OIDC (production auth):
MCP_TRANSPORT=http `
  GRAPHQL_BASE_URL=https://your-api.com `
  OIDC_DISCOVERY_URL=https://your-oidc-provider/.well-known/openid-configuration `
  OIDC_AUDIENCE=your-api-id `
  python -m mcp_server_template
```

Server listens on `HOST` (default `0.0.0.0`) and `PORT` (default `3001`). See
[Consuming the server](#consuming-the-server) and
[Authentication](#authentication) below for how to call it.

## How to build

### Prerequisites

- Python 3.10+
- pip

### Installation

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

The `[dev]` extras include pytest, black, and ruff for testing and linting.

## How to test

### Inspect generated tools

Before running the server, you can see what tools will be generated from your
schema:

```powershell
# Print tools to stdout (text format)
python -m mcp_server_template generate --schema-file demo-schema.graphql

# Or as JSON to a file
python -m mcp_server_template generate --schema-file demo-schema.graphql --json -o tools.json
```

### Mock backend (no real GraphQL API needed)

`mock_backend` is a Strawberry/Python GraphQL server that mirrors
`demo-schema.graphql` with hardcoded fixture data (2 projects, 4 tasks, 3
members, labels, milestones). Use it to run the full MCP server stack locally
without a real backend.

**Option A — auto-start alongside the MCP server (recommended for quick testing)**

```powershell
# Sets GRAPHQL_BASE_URL=http://127.0.0.1:4000 and SCHEMA_FILE automatically.
MOCK_BACKEND=1 python -m mcp_server_template

# HTTP transport variant:
MOCK_BACKEND=1 MCP_TRANSPORT=http python -m mcp_server_template
```

The MCP server starts the mock backend as a subprocess on port 4000, waits
for it to be ready, then starts itself. Both shut down together.

**Option B — run them in separate terminals**

```powershell
# Terminal 1: mock backend (GraphiQL at http://127.0.0.1:4000/graphql)
python -m mock_backend

# Terminal 2: MCP server pointing at it
$env:GRAPHQL_BASE_URL = "http://127.0.0.1:4000"
$env:SCHEMA_FILE      = "./demo-schema.graphql"
python -m mcp_server_template
```

The mock backend also exposes `GET /schema` returning the raw SDL text, which
exercises the `GRAPHQL_SCHEMA_ENDPOINT` path if you set it.

Mutations in the mock backend update in-memory state for the lifetime of the
process — create a task, then query tasks and the new one appears.

### Test over stdio transport

Start the server and call tools via MCP JSON-RPC. This requires an MCP client
library (e.g., `anthropic-sdk` with MCP support, or the browser-based MCP
debugger).

```powershell
# Terminal 1: start the server
SCHEMA_FILE=./demo-schema.graphql GRAPHQL_BASE_URL=http://localhost python -m mcp_server_template

# Terminal 2: call tools (using mcp-cli or similar)
# mcp-cli tools list
# mcp-cli tools/get_projects
```

If you have `anthropic-sdk` with MCP support installed, you can test
interactively:

```python
import asyncio
import subprocess
from anthropic import Anthropic

async def test():
    proc = subprocess.Popen(
        ["python", "-m", "mcp_server_template"],
        stdout=subprocess.PIPE,
        stdin=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env={
            "SCHEMA_FILE": "./demo-schema.graphql",
            "GRAPHQL_BASE_URL": "http://localhost",
        }
    )
    # Use Anthropic SDK's MCP client to send tools/list, tools/call requests
```

### Test over HTTP transport

```powershell
# Terminal 1: start the server over HTTP
MCP_TRANSPORT=http SCHEMA_FILE=./demo-schema.graphql GRAPHQL_BASE_URL=http://localhost python -m mcp_server_template

# Terminal 2: list tools
curl -X POST http://localhost:3001/mcp/tools/list -H "Content-Type: application/json" -d '{}'

# Call a tool (e.g., get_workspace)
curl -X POST http://localhost:3001/mcp/tools/call -H "Content-Type: application/json" -d '{
  "name": "get_workspace",
  "arguments": {}
}'
```

With auth enabled (see [Authentication](#authentication) below):

```powershell
# Pass the Bearer token
curl -X POST http://localhost:3001/mcp/tools/list \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{}'
```

## Consuming the server

### Via stdio (Claude Desktop, local agents)

In your MCP client config (e.g., `~/.claude/tools/mcp-servers.json`):

```json
{
  "mcp-server-template": {
    "command": "python",
    "args": ["-m", "mcp_server_template"],
    "env": {
      "SCHEMA_FILE": "./demo-schema.graphql",
      "GRAPHQL_BASE_URL": "https://your-graphql-backend.com",
      "GRAPHQL_API_TOKEN": "your-static-token"
    }
  }
}
```

The server will start as a subprocess, and your client will speak to it over
stdio.

### Via HTTP (server-hosted, multiple clients)

```powershell
MCP_TRANSPORT=http python -m mcp_server_template

# Clients then make POST requests to http://localhost:3001/mcp/tools/list
# and http://localhost:3001/mcp/tools/call
```

### Environment variables (see `.env.example`)

| Variable | Required | Purpose |
|----------|----------|---------|
| `GRAPHQL_BASE_URL` | Yes | GraphQL backend URL (e.g., `https://api.example.com`) |
| `SCHEMA_FILE` | No | Path to a schema file (`.graphql` or `.json`); if set, tools are generated from this file at startup instead of fetching from the backend |
| `GRAPHQL_API_TOKEN` | No | Static Bearer token the server sends to the backend on every request |
| `GRAPHQL_ENDPOINT` | No | GraphQL endpoint path (default: `/graphql`) |
| `GRAPHQL_VERIFY_SSL` | No | Verify SSL certificates; set to `false` for self-signed certs (default: `true`) |
| `MCP_TRANSPORT` | No | Transport type: `stdio` (default) or `http` |
| `HOST` | No | HTTP server bind address (default: `0.0.0.0`) |
| `PORT` | No | HTTP server port (default: `3001`) |
| `MOCK_BACKEND` | No | Set to `1` to auto-start the bundled mock GraphQL backend alongside the MCP server (dev/test only) |
| `MOCK_BACKEND_PORT` | No | Port for the mock backend when `MOCK_BACKEND=1` (default: `4000`) |

## Authentication

### No auth (development)

By default, the server accepts requests without authentication. Useful for
local testing against a public or trusted backend.

### Static token (local testing)

Set `DEV_TOKEN` to test auth without a real OIDC provider:

```powershell
DEV_TOKEN=my-test-token MCP_TRANSPORT=http python -m mcp_server_template
```

Then include the token in requests:

```powershell
curl -X POST http://localhost:3001/mcp/tools/list \
  -H "Authorization: Bearer my-test-token"
```

Without the token (or with the wrong one), the server returns 401.

### OIDC (production)

For real deployments, validate incoming Bearer tokens against an OIDC provider
(Keycloak, Azure AD, Auth0, Cognito, etc.):

```powershell
$env:OIDC_DISCOVERY_URL = "https://login.microsoftonline.com/your-tenant/v2.0/.well-known/openid-configuration"
$env:OIDC_AUDIENCE = "your-api-identifier"
MCP_TRANSPORT=http python -m mcp_server_template
```

Alternatively, if you have a JWKS URI directly:

```powershell
$env:OIDC_JWKS_URI = "https://your-provider.com/.well-known/jwks.json"
$env:OIDC_AUDIENCE = "your-api-identifier"
MCP_TRANSPORT=http python -m mcp_server_template
```

The server will validate the token on every request. Invalid or missing tokens
return 401.

See `mcp_server_template/auth/provider.py` for the pluggable auth interface.

## Architecture

- **`server.py`** — `create_mcp()` builds the tool registry and middleware
  chain; `build_asgi_app()` wraps it for HTTP.
- **`graphql/introspection.py`** — Introspects a GraphQL schema into a
  normalized internal representation.
- **`registry/tool_factory.py`** — Generates MCP tools from introspected
  schema.
- **`registry/entity_map.py`** — Renders an entity map for system prompts.
- **`graphql/builder.py`** — Builds GraphQL queries (with include-path
  support).
- **`config.py`** — Loads configuration from environment variables and
  `.env`.
- **`auth/provider.py`** — Pluggable auth interface; includes static token
  and OIDC implementations.
- **`middleware/http_auth.py`** — HTTP auth middleware (stdio has no auth
  concept).

For deeper context, see `CLAUDE.md`.

## License

MIT — see `LICENSE`.
