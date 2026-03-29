"""FastAPI application entry point for the Story Video Editor backend."""

import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.config import settings


def _check_auth_config() -> None:
    """Fail-closed auth startup check.

    Refuse to start if no API_SECRET_KEY is set and AUTH_DISABLED is not true.
    """
    if not settings.AUTH_DISABLED and not settings.API_SECRET_KEY:
        print(
            "ERROR: API_SECRET_KEY is not set and AUTH_DISABLED is not true. "
            "Set API_SECRET_KEY or AUTH_DISABLED=true to start the server.",
            file=sys.stderr,
        )
        sys.exit(1)


_check_auth_config()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle manager."""
    yield


app = FastAPI(title="Story Video Editor API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"status": "ok"}
