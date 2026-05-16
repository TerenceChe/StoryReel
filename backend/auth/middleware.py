"""Authentication middleware for the Story Video Editor API.

Validates Auth0-issued JWTs against the JWKS endpoint derived from AUTH0_DOMAIN.
Extracts the sub claim as the owner_id for project ownership.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.config import Settings, settings as _default_settings

# Optional bearer scheme — auto_error=False so we can return a clear 401 ourselves.
_bearer_scheme = HTTPBearer(auto_error=False)

# ---------------------------------------------------------------------------
# JWKS cache (thread-safe, lazy-loaded)
# ---------------------------------------------------------------------------

_jwks_cache: dict[str, Any] = {}
_jwks_cache_lock = threading.Lock()
_JWKS_CACHE_TTL = 3600  # 1 hour


def _fetch_jwks(jwks_uri: str) -> dict[str, Any]:
    """Fetch JWKS from the provider endpoint. Uses httpx synchronously."""
    import httpx

    resp = httpx.get(jwks_uri, timeout=10)
    resp.raise_for_status()
    return resp.json()


def get_jwks(jwks_uri: str) -> dict[str, Any]:
    """Return cached JWKS keys, refreshing if stale."""
    with _jwks_cache_lock:
        cached = _jwks_cache.get(jwks_uri)
        now = time.monotonic()
        if cached and now - cached["fetched_at"] < _JWKS_CACHE_TTL:
            return cached["data"]

    data = _fetch_jwks(jwks_uri)
    with _jwks_cache_lock:
        _jwks_cache[jwks_uri] = {"data": data, "fetched_at": time.monotonic()}
    return data


def clear_jwks_cache() -> None:
    """Clear the JWKS cache (useful for testing)."""
    with _jwks_cache_lock:
        _jwks_cache.clear()


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------

def _get_signing_key(token: str, jwks_uri: str) -> jwt.algorithms.RSAAlgorithm:
    """Find the signing key from JWKS that matches the token's kid header."""
    jwks_data = get_jwks(jwks_uri)
    try:
        unverified_header = jwt.get_unverified_header(token)
    except jwt.DecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token header",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    kid = unverified_header.get("kid")
    if not kid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing kid header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    for key_data in jwks_data.get("keys", []):
        if key_data.get("kid") == kid:
            return jwt.algorithms.RSAAlgorithm.from_jwk(key_data)

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Signing key not found",
        headers={"WWW-Authenticate": "Bearer"},
    )


def decode_jwt(token: str, app_settings: Settings) -> dict[str, Any]:
    """Decode and validate a JWT token against the configured OIDC provider."""
    jwks_uri = app_settings.JWT_JWKS_URI
    if not jwks_uri:
        # Derive from issuer if not explicitly set
        issuer = app_settings.JWT_ISSUER or ""
        jwks_uri = issuer.rstrip("/") + "/.well-known/jwks.json"

    public_key = _get_signing_key(token, jwks_uri)

    decode_options: dict[str, Any] = {}
    algorithms = ["RS256"]

    kwargs: dict[str, Any] = {
        "jwt": token,
        "key": public_key,
        "algorithms": algorithms,
        "issuer": app_settings.JWT_ISSUER,
        "options": decode_options,
    }
    if app_settings.JWT_AUDIENCE:
        kwargs["audience"] = app_settings.JWT_AUDIENCE

    try:
        return jwt.decode(**kwargs)
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except jwt.InvalidIssuerError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token issuer",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except jwt.InvalidAudienceError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token audience",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except (jwt.InvalidTokenError, jwt.DecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------

def get_settings() -> Settings:
    """Return the application settings. Extracted as a standalone dependency
    so it can be cleanly overridden in tests."""
    return _default_settings


async def get_owner_id(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    app_settings: Settings = Depends(get_settings),
) -> str:
    """Extract and validate the owner identity from the request.

    Validates the token as a JWT and extracts the owner_id from the ``sub`` claim.
    Supports ?token= query parameter for SSE connections that cannot set headers.

    When ``DISABLE_AUTH`` is set, returns the configured local owner id without
    requiring or validating any token. Useful for local single-user development.
    """
    if app_settings.DISABLE_AUTH:
        return app_settings.LOCAL_OWNER_ID

    if credentials is None:
        query_token = request.query_params.get("token")
        if query_token:
            credentials = HTTPAuthorizationCredentials(
                scheme="Bearer", credentials=query_token
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
                headers={"WWW-Authenticate": "Bearer"},
            )

    payload = decode_jwt(credentials.credentials, app_settings)
    sub = payload.get("sub")
    if not sub:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing sub claim",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return sub


def verify_project_ownership(project_owner_id: str, request_owner_id: str) -> None:
    """Raise 403 if the requesting user does not own the project."""
    if project_owner_id != request_owner_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: you do not own this project",
        )
