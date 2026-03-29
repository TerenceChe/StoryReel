"""Pluggable authentication middleware for the Story Video Editor API.

Supports:
- Bearer token validation against API_SECRET_KEY (timing-safe comparison)
- Ownership checks for project endpoints
"""

from __future__ import annotations

import secrets

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.config import Settings, settings as _default_settings

# Optional bearer scheme — auto_error=False so we can return a clear 401 ourselves.
_bearer_scheme = HTTPBearer(auto_error=False)


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

    The Bearer token is validated against API_SECRET_KEY using a
    constant-time comparison to prevent timing attacks.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not secrets.compare_digest(
        credentials.credentials, app_settings.API_SECRET_KEY or ""
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # With a simple shared secret every authenticated caller is the same
    # "user".  X-Owner-Id lets trusted callers (e.g. internal services)
    # specify identity.  This header is NOT safe for untrusted clients —
    # the future JWT/Cognito migration will extract identity from the token.
    owner_id = request.headers.get("X-Owner-Id", app_settings.DEV_OWNER_ID)
    return owner_id


def verify_project_ownership(project_owner_id: str, request_owner_id: str) -> None:
    """Raise 403 if the requesting user does not own the project."""
    if project_owner_id != request_owner_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: you do not own this project",
        )
