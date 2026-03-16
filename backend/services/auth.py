"""
NovaSync — FastAPI dependency for Supabase JWT verification.

Supabase "Enhanced Auth" projects sign JWTs with ES256 (ECDSA P-256).
We verify tokens using Supabase's public JWKS endpoint so we never need
to store a private key — only the public key is required for verification.
"""

from __future__ import annotations

import os

import jwt
from jwt import PyJWKClient
from fastapi import HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_bearer = HTTPBearer(auto_error=True)

_jwks_client: PyJWKClient | None = None


def _get_jwks_client() -> PyJWKClient:
    """Return a cached JWKS client pointing at this project's Supabase public keys."""
    global _jwks_client
    if _jwks_client is None:
        supabase_url = os.environ.get("SUPABASE_URL")
        if not supabase_url:
            raise RuntimeError("SUPABASE_URL environment variable not set")
        jwks_url = f"{supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"
        # cache_keys=True means the client caches fetched keys in memory,
        # so it only hits the network on the first verification (or key rotation).
        _jwks_client = PyJWKClient(jwks_url, cache_keys=True)
    return _jwks_client


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Security(_bearer),
) -> dict:
    """Verify a Supabase ES256 JWT and return the decoded payload.

    The payload's ``sub`` field is the user's UUID.
    Raises HTTP 401 on any verification failure.
    """
    token = credentials.credentials
    try:
        client = _get_jwks_client()
        # Fetches the matching public key from JWKS (by the token's "kid" header)
        signing_key = client.get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["ES256"],
            audience="authenticated",
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
        )
    except jwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {exc}",
        )
    return payload
