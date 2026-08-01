"""
Pluggable authentication interface.

AuthProvider is a structural protocol (duck-typed, no base class required) —
any object with a matching authenticate() method can be plugged in wherever
an AuthProvider is expected. Two implementations ship here:

  StaticTokenAuthProvider — trivial reference implementation, compares
    against a single configured token. Good for local dev/testing.

  OidcAuthProvider — wraps JwtValidator (see jwt_validator.py) to validate
    real OIDC-issued JWTs from any provider (Keycloak, Azure AD, Auth0...).

Both satisfy the same authenticate(credential) contract, so swapping one
for the other is a one-line change wherever a provider is constructed —
not a rewrite of whatever calls authenticate().
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from mcp_server_template.auth.jwt_validator import JwtValidator


class AuthenticationError(Exception):
    """Raised by an AuthProvider when a credential is missing or invalid."""


@runtime_checkable
class AuthProvider(Protocol):
    def authenticate(self, credential: str | None) -> None:
        """Validate a credential. Raise AuthenticationError if invalid."""
        ...


class StaticTokenAuthProvider:
    """Reference implementation: compares against one configured token."""

    def __init__(self, expected_token: str) -> None:
        self._expected_token = expected_token

    def authenticate(self, credential: str | None) -> None:
        token = (credential or "").removeprefix("Bearer ").strip()
        if token != self._expected_token:
            raise AuthenticationError("invalid or missing token")


class OidcAuthProvider:
    """Adapts JwtValidator to the AuthProvider contract."""

    def __init__(self, validator: JwtValidator) -> None:
        self._validator = validator

    def authenticate(self, credential: str | None) -> None:
        if not credential:
            raise AuthenticationError("missing token")
        try:
            self._validator.validate(credential)
        except Exception as exc:
            raise AuthenticationError(str(exc)) from exc
