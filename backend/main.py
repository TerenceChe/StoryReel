"""FastAPI application entry point for the Story Video Editor backend."""

import sys

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.config import settings
from backend.routers.projects import router as projects_router
from backend.routers.voices import router as voices_router


def _check_auth_config() -> None:
    """Fail-closed auth startup check.

    Refuse to start if AUTH0_DOMAIN is not configured, unless auth is disabled.
    """
    if settings.DISABLE_AUTH:
        print("WARNING: Auth is disabled. All requests run as the local user.", file=sys.stderr)
        return
    if not settings.AUTH0_DOMAIN:
        print(
            "ERROR: AUTH0_DOMAIN is not set. "
            "Set AUTH0_DOMAIN to your Auth0 tenant domain (e.g., myapp-dev.us.auth0.com), "
            "or set DISABLE_AUTH=true to run without authentication.",
            file=sys.stderr,
        )
        sys.exit(1)


_check_auth_config()

app = FastAPI(title="Story Video Editor API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(projects_router)
app.include_router(voices_router)


@app.get("/health")
async def health():
    return {"status": "ok"}
