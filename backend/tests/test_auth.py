"""Tests for authentication middleware."""

from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from backend.auth import get_owner_id, verify_project_ownership
from backend.auth import middleware as auth_mod
from backend.config import Settings


# ---------------------------------------------------------------------------
# Helpers — build a tiny app per test scenario
# ---------------------------------------------------------------------------

def _make_app(app_settings: Settings) -> FastAPI:
    """Create a minimal FastAPI app wired with auth using the given settings."""
    app = FastAPI()

    @app.get("/me")
    async def me(owner_id: str = Depends(get_owner_id)):
        return {"owner_id": owner_id}

    # Patch the module-level settings reference.
    auth_mod._default_settings = app_settings

    return app


# ---------------------------------------------------------------------------
# Token validation tests
# ---------------------------------------------------------------------------

class TestTokenValidation:
    def setup_method(self):
        self.settings = Settings.__new__(Settings)
        self.settings.API_SECRET_KEY = "test-secret-key"
        self.settings.DEV_OWNER_ID = "dev-user"
        self.app = _make_app(self.settings)
        self.client = TestClient(self.app)

    def teardown_method(self):
        from backend.config import settings as default_settings
        auth_mod._default_settings = default_settings

    def test_missing_token_returns_401(self):
        resp = self.client.get("/me")
        assert resp.status_code == 401
        assert "Authentication required" in resp.json()["detail"]

    def test_invalid_token_returns_401(self):
        resp = self.client.get("/me", headers={"Authorization": "Bearer wrong-key"})
        assert resp.status_code == 401
        assert "Invalid authentication token" in resp.json()["detail"]

    def test_valid_token_returns_default_owner(self):
        resp = self.client.get(
            "/me",
            headers={"Authorization": "Bearer test-secret-key"},
        )
        assert resp.status_code == 200
        assert resp.json()["owner_id"] == "dev-user"

    def test_valid_token_with_custom_owner_header(self):
        resp = self.client.get(
            "/me",
            headers={
                "Authorization": "Bearer test-secret-key",
                "X-Owner-Id": "user-42",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["owner_id"] == "user-42"


# ---------------------------------------------------------------------------
# Ownership verification tests
# ---------------------------------------------------------------------------

class TestOwnershipVerification:
    def test_matching_owner_passes(self):
        # Should not raise
        verify_project_ownership("user-1", "user-1")

    def test_mismatched_owner_raises_403(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            verify_project_ownership("user-1", "user-2")
        assert exc_info.value.status_code == 403
        assert "Access denied" in exc_info.value.detail
