"""
Provider-agnostic JWT validator.

Works with any OIDC-compliant provider (Keycloak, Azure AD, Auth0, Cognito…).
Only needs two config values:
  OIDC_DISCOVERY_URL — the provider's /.well-known/openid-configuration URL
  OIDC_AUDIENCE      — the expected 'aud' claim (e.g. "my-mcp-server")

JWKS keys are cached in memory by PyJWKClient and automatically refreshed
when a token references an unknown kid (key rotation is transparent).
"""
from __future__ import annotations

import logging
from typing import Any

import httpx
import jwt
from jwt import PyJWKClient

logger = logging.getLogger(__name__)


class JwtValidator:
    def __init__(self, jwks_uri: str, audience: str | None = None) -> None:
        # lifespan=300 — cache JWKS for 5 min, then re-fetch
        self._jwks_client = PyJWKClient(jwks_uri, cache_keys=True, lifespan=300)
        self._audience = audience

    # ── Factory ───────────────────────────────────────────────────────────────

    @classmethod
    async def from_discovery(cls, discovery_url: str, audience: str | None = None) -> "JwtValidator":
        """
        Resolve jwks_uri from the provider's OIDC discovery document, then
        construct a JwtValidator.

        Works with any OIDC-compliant provider — Keycloak, Azure AD, etc.
        Examples:
          Keycloak: https://sso.example.com/auth/realms/{realm}/.well-known/openid-configuration
          Azure AD: https://login.microsoftonline.com/{tenant}/v2.0/.well-known/openid-configuration
        """
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(discovery_url)
            resp.raise_for_status()
            meta = resp.json()

        jwks_uri = meta.get("jwks_uri")
        if not jwks_uri:
            raise RuntimeError(f"OIDC discovery at {discovery_url} did not return a jwks_uri")

        logger.info("OIDC: jwks_uri resolved from discovery → %s", jwks_uri)
        return cls(jwks_uri=jwks_uri, audience=audience)

    @classmethod
    async def from_jwks_uri(cls, jwks_uri: str, audience: str | None = None) -> "JwtValidator":
        """Use a known JWKS URI directly, skipping discovery."""
        return cls(jwks_uri=jwks_uri, audience=audience)

    # ── Validation ────────────────────────────────────────────────────────────

    def validate(self, authorization: str) -> dict[str, Any]:
        """
        Validate a Bearer token and return its decoded claims.

        Accepts either the raw JWT or "Bearer <jwt>".
        Raises jwt.InvalidTokenError (or a subclass) on any failure:
          - signature invalid
          - token expired
          - audience mismatch (when OIDC_AUDIENCE is configured)
          - unknown signing key
        """
        token = authorization.removeprefix("Bearer ").strip()

        signing_key = self._jwks_client.get_signing_key_from_jwt(token)

        decode_options: dict[str, Any] = {}
        decode_kwargs: dict[str, Any] = {
            "key": signing_key.key,
            "algorithms": ["RS256", "RS384", "RS512", "ES256", "ES384", "ES512"],
            "options": decode_options,
        }

        if self._audience:
            decode_kwargs["audience"] = self._audience
        else:
            decode_options["verify_aud"] = False

        return jwt.decode(token, **decode_kwargs)
