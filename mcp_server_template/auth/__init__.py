"""Pluggable authentication: AuthProvider protocol + reference implementations."""
from mcp_server_template.auth.jwt_validator import JwtValidator
from mcp_server_template.auth.provider import (
    AuthenticationError,
    AuthProvider,
    OidcAuthProvider,
    StaticTokenAuthProvider,
)

__all__ = [
    "AuthenticationError",
    "AuthProvider",
    "JwtValidator",
    "OidcAuthProvider",
    "StaticTokenAuthProvider",
]
