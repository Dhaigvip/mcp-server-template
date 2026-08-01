from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Walk up from this file's directory to find .env — works regardless of cwd.
load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")


@dataclass
class GraphQLConfig:
    base_url: str
    graphql_endpoint: str = "/graphql"
    schema_endpoint: str | None = None
    verify_ssl: bool = True  # set False for self-signed certs
    # Static credential GraphQLClient sends as "Authorization: Bearer <token>"
    # on every outbound call to the backend. Distinct from ServerConfig.dev_token
    # (which authenticates *inbound* callers of this MCP server, see auth/).
    api_token: str | None = None

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")


@dataclass
class OidcConfig:
    """
    Provider-agnostic OIDC/JWT validation config.

    Set either discovery_url OR jwks_uri — not both.
    When neither is set, JWT validation is skipped (dev/test mode only).

    Keycloak discovery URL pattern:
      https://<host>/auth/realms/<realm>/.well-known/openid-configuration

    Azure AD discovery URL pattern:
      https://login.microsoftonline.com/<tenant>/v2.0/.well-known/openid-configuration
    """

    discovery_url: str | None = None  # preferred — resolves jwks_uri automatically
    jwks_uri: str | None = None  # alternative — skip discovery, use directly
    audience: str | None = None  # expected 'aud' claim


@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 3001
    # Static token fallback for local testing without a real OIDC provider.
    dev_token: str | None = None
    # Path to a downloaded schema file (JSON introspection or SDL .graphql).
    # When set, tools are generated from the file at startup — no live backend needed.
    schema_file: str | None = None


@dataclass
class AppConfig:
    graphql: GraphQLConfig
    server: ServerConfig = field(default_factory=ServerConfig)
    oidc: OidcConfig = field(default_factory=OidcConfig)


_PROJECT_ROOT = Path(__file__).parent.parent


def _resolve_schema_file(value: str | None) -> str | None:
    """Resolve schema file path relative to the project root when it starts with './'."""
    if not value:
        return None
    p = Path(value)
    if not p.is_absolute():
        p = _PROJECT_ROOT / p
    return str(p)


def load_config() -> AppConfig:
    base_url = os.environ.get("GRAPHQL_BASE_URL")
    if not base_url:
        raise RuntimeError("GRAPHQL_BASE_URL environment variable is required")

    return AppConfig(
        graphql=GraphQLConfig(
            base_url=base_url,
            graphql_endpoint=os.environ.get("GRAPHQL_ENDPOINT", "/graphql"),
            schema_endpoint=os.environ.get("GRAPHQL_SCHEMA_ENDPOINT"),
            verify_ssl=os.environ.get("GRAPHQL_VERIFY_SSL", "true").lower() != "false",
            api_token=os.environ.get("GRAPHQL_API_TOKEN"),
        ),
        server=ServerConfig(
            host=os.environ.get("HOST", "0.0.0.0"),
            port=int(os.environ.get("PORT", "3001")),
            dev_token=os.environ.get("DEV_TOKEN"),
            schema_file=_resolve_schema_file(os.environ.get("SCHEMA_FILE")),
        ),
        oidc=OidcConfig(
            discovery_url=os.environ.get("OIDC_DISCOVERY_URL"),
            jwks_uri=os.environ.get("OIDC_JWKS_URI"),
            audience=os.environ.get("OIDC_AUDIENCE"),
        ),
    )
