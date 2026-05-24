"""FastAPI application entry point for the Story Video Editor backend."""

import sys

from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.config import settings
from backend.routers.projects import router as projects_router
from backend.routers.voices import router as voices_router
from backend.services.project_service import TitleConflictError
from backend.services.title_validator import (
    TitleErrorCode,
    TitleValidationError,
)


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


# ---------------------------------------------------------------------------
# Exception handlers for project-title validation
# ---------------------------------------------------------------------------


def _title_error_response(code: TitleErrorCode, message: str) -> JSONResponse:
    """Build the structured ``detail`` body used for title errors.

    Status code mapping per design.md:
    - ``title_duplicate`` → 409 Conflict
    - All other ``TitleErrorCode``s → 422 Unprocessable Entity
    """
    status_code = 409 if code == TitleErrorCode.DUPLICATE else 422
    return JSONResponse(
        status_code=status_code,
        content={
            "detail": {
                "error_code": code.value,
                "field": "title",
                "message": message,
            }
        },
    )


@app.exception_handler(TitleValidationError)
async def title_validation_exception_handler(
    request: Request, exc: TitleValidationError
) -> JSONResponse:
    return _title_error_response(exc.code, exc.message)


@app.exception_handler(TitleConflictError)
async def title_conflict_exception_handler(
    request: Request, exc: TitleConflictError
) -> JSONResponse:
    # ``TitleConflictError`` is a service-layer alias for the duplicate case.
    return _title_error_response(
        TitleErrorCode.DUPLICATE,
        str(exc) or "A project with this title already exists.",
    )


@app.exception_handler(RequestValidationError)
async def title_required_validation_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Surface a missing ``title`` on POST /projects as ``title_required``.

    Iterates the Pydantic v2 errors. If any error is a missing-field error
    targeting ``body.title``, return the structured title error shape.
    Otherwise delegate to FastAPI's default validation error handler so all
    other request validation errors keep their standard format.
    """
    for err in exc.errors():
        if err.get("type") == "missing" and tuple(err.get("loc", ())) == (
            "body",
            "title",
        ):
            return _title_error_response(
                TitleErrorCode.REQUIRED, "Title is required."
            )
    return await request_validation_exception_handler(request, exc)


app.include_router(projects_router)
app.include_router(voices_router)


@app.get("/health")
async def health():
    return {"status": "ok"}
