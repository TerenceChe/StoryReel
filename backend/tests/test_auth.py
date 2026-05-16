"""Tests for authentication middleware (Auth0 JWT-only mode)."""

from __future__ import annotations

import sys
import time
import uuid

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from hypothesis import given, settings as hyp_settings
from hypothesis import strategies as st

from backend.auth import get_owner_id, get_settings, verify_project_ownership
from backend.auth.middleware import clear_jwks_cache
from backend.config import Settings


# ---------------------------------------------------------------------------
# RSA key helpers for JWT tests
# ---------------------------------------------------------------------------

def _generate_rsa_keypair():
    """Generate a fresh RSA private key and return (private_key, public_key)."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


def _private_key_pem(private_key) -> bytes:
    return private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def _public_key_jwk(public_key, kid: str = "test-kid-1") -> dict:
    """Convert an RSA public key to a JWK dict."""
    numbers = public_key.public_numbers()

    def _b64url(n: int, length: int) -> str:
        import base64
        return base64.urlsafe_b64encode(n.to_bytes(length, "big")).rstrip(b"=").decode()

    return {
        "kty": "RSA",
        "kid": kid,
        "use": "sig",
        "alg": "RS256",
        "n": _b64url(numbers.n, 256),
        "e": _b64url(numbers.e, 3),
    }


def _make_jwt(
    private_key,
    kid: str = "test-kid-1",
    sub: str = "user-123",
    issuer: str = "https://test-issuer.example.com/",
    audience: str | None = None,
    exp_offset: int = 3600,
    extra_claims: dict | None = None,
    omit_sub: bool = False,
) -> str:
    """Create a signed JWT for testing."""
    now = int(time.time())
    payload: dict = {
        "iss": issuer,
        "iat": now,
        "exp": now + exp_offset,
    }
    if not omit_sub:
        payload["sub"] = sub
    if audience:
        payload["aud"] = audience
    if extra_claims:
        payload.update(extra_claims)

    return pyjwt.encode(
        payload,
        _private_key_pem(private_key),
        algorithm="RS256",
        headers={"kid": kid},
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_app(app_settings: Settings) -> FastAPI:
    """Create a minimal FastAPI app wired with auth using the given settings."""
    app = FastAPI()

    @app.get("/me")
    async def me(owner_id: str = Depends(get_owner_id)):
        return {"owner_id": owner_id}

    # Override the settings dependency cleanly.
    app.dependency_overrides[get_settings] = lambda: app_settings

    return app


# ---------------------------------------------------------------------------
# Ownership verification tests
# ---------------------------------------------------------------------------

class TestOwnershipVerification:
    def test_matching_owner_passes(self):
        verify_project_ownership("user-1", "user-1")

    def test_mismatched_owner_raises_403(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            verify_project_ownership("user-1", "user-2")
        assert exc_info.value.status_code == 403
        assert "Access denied" in exc_info.value.detail


# ---------------------------------------------------------------------------
# JWT validation tests
# ---------------------------------------------------------------------------

class TestJWTValidation:
    """Tests for JWT-based authentication mode."""

    ISSUER = "https://test-issuer.example.com/"
    AUDIENCE = "test-audience"
    KID = "test-kid-1"

    def setup_method(self):
        clear_jwks_cache()
        self.private_key, self.public_key = _generate_rsa_keypair()
        self.jwk = _public_key_jwk(self.public_key, kid=self.KID)

        self.settings = Settings()
        self.settings.AUTH0_DOMAIN = "test-issuer.example.com"
        self.settings.AUTH0_AUDIENCE = self.AUDIENCE

        self.app = _make_app(self.settings)
        self.client = TestClient(self.app)

    def _patch_jwks(self, monkeypatch, jwks_data=None):
        """Patch the JWKS fetch to return our test key."""
        if jwks_data is None:
            jwks_data = {"keys": [self.jwk]}

        import backend.auth.middleware as mw
        monkeypatch.setattr(mw, "_fetch_jwks", lambda uri: jwks_data)

    # -- Happy path --

    def test_valid_jwt_extracts_sub_as_owner_id(self, monkeypatch):
        self._patch_jwks(monkeypatch)
        token = _make_jwt(
            self.private_key,
            kid=self.KID,
            sub="cognito-user-abc",
            issuer=self.ISSUER,
            audience=self.AUDIENCE,
        )
        resp = self.client.get("/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.json()["owner_id"] == "cognito-user-abc"

    # -- Expired token --

    def test_expired_jwt_returns_401(self, monkeypatch):
        self._patch_jwks(monkeypatch)
        token = _make_jwt(
            self.private_key,
            kid=self.KID,
            sub="user-1",
            issuer=self.ISSUER,
            audience=self.AUDIENCE,
            exp_offset=-3600,  # expired 1 hour ago
        )
        resp = self.client.get("/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401
        assert "expired" in resp.json()["detail"].lower()

    # -- Invalid signature --

    def test_invalid_signature_returns_401(self, monkeypatch):
        self._patch_jwks(monkeypatch)
        # Sign with a different key
        other_private, _ = _generate_rsa_keypair()
        token = _make_jwt(
            other_private,
            kid=self.KID,  # same kid, but wrong key
            sub="user-1",
            issuer=self.ISSUER,
            audience=self.AUDIENCE,
        )
        resp = self.client.get("/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401

    # -- Missing sub claim --

    def test_missing_sub_claim_returns_401(self, monkeypatch):
        self._patch_jwks(monkeypatch)
        token = _make_jwt(
            self.private_key,
            kid=self.KID,
            issuer=self.ISSUER,
            audience=self.AUDIENCE,
            omit_sub=True,
        )
        resp = self.client.get("/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401
        assert "sub" in resp.json()["detail"].lower()

    # -- Wrong issuer --

    def test_wrong_issuer_returns_401(self, monkeypatch):
        self._patch_jwks(monkeypatch)
        token = _make_jwt(
            self.private_key,
            kid=self.KID,
            sub="user-1",
            issuer="https://wrong-issuer.example.com",
            audience=self.AUDIENCE,
        )
        resp = self.client.get("/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401

    # -- Wrong audience --

    def test_wrong_audience_returns_401(self, monkeypatch):
        self._patch_jwks(monkeypatch)
        token = _make_jwt(
            self.private_key,
            kid=self.KID,
            sub="user-1",
            issuer=self.ISSUER,
            audience="wrong-audience",
        )
        resp = self.client.get("/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401

    # -- kid not found in JWKS --

    def test_unknown_kid_returns_401(self, monkeypatch):
        self._patch_jwks(monkeypatch)
        token = _make_jwt(
            self.private_key,
            kid="unknown-kid",
            sub="user-1",
            issuer=self.ISSUER,
            audience=self.AUDIENCE,
        )
        resp = self.client.get("/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401
        assert "Signing key not found" in resp.json()["detail"]

    # -- Garbage token --

    def test_garbage_token_returns_401(self, monkeypatch):
        self._patch_jwks(monkeypatch)
        resp = self.client.get("/me", headers={"Authorization": "Bearer not.a.jwt"})
        assert resp.status_code == 401

    # -- No audience configured (audience check skipped) --

    def test_jwt_without_audience_config_skips_aud_check(self, monkeypatch):
        self._patch_jwks(monkeypatch)
        self.settings.AUTH0_AUDIENCE = None
        token = _make_jwt(
            self.private_key,
            kid=self.KID,
            sub="user-no-aud",
            issuer=self.ISSUER,
            audience=None,
        )
        resp = self.client.get("/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.json()["owner_id"] == "user-no-aud"

    # -- X-Owner-Id header is ignored in JWT mode --

    def test_x_owner_id_header_ignored_in_jwt_mode(self, monkeypatch):
        self._patch_jwks(monkeypatch)
        token = _make_jwt(
            self.private_key,
            kid=self.KID,
            sub="jwt-user",
            issuer=self.ISSUER,
            audience=self.AUDIENCE,
        )
        resp = self.client.get(
            "/me",
            headers={
                "Authorization": f"Bearer {token}",
                "X-Owner-Id": "should-be-ignored",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["owner_id"] == "jwt-user"


# ---------------------------------------------------------------------------
# Startup check tests
# ---------------------------------------------------------------------------

class TestStartupCheck:
    def test_missing_auth0_domain_exits(self, monkeypatch):
        from backend.main import _check_auth_config
        s = Settings()
        s.AUTH0_DOMAIN = None
        monkeypatch.setattr("backend.main.settings", s)
        with pytest.raises(SystemExit) as exc_info:
            _check_auth_config()
        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# Property-based tests (hypothesis)
# ---------------------------------------------------------------------------


class TestPropertyAuth0DomainDerivation:
    """Feature: user-accounts, Property 4: AUTH0_DOMAIN derivation

    Validates: Requirements 5.2, 5.3
    """

    @given(domain=st.text(
        alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz0123456789-."),
        min_size=1,
        max_size=63,
    ).filter(lambda d: not d.startswith("-") and not d.endswith("-") and ".." not in d))
    @hyp_settings(max_examples=100)
    def test_jwt_issuer_derived_from_domain(self, domain):
        """**Validates: Requirements 5.2**"""
        s = Settings()
        s.AUTH0_DOMAIN = domain
        assert s.JWT_ISSUER == f"https://{domain}/"

    @given(domain=st.text(
        alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz0123456789-."),
        min_size=1,
        max_size=63,
    ).filter(lambda d: not d.startswith("-") and not d.endswith("-") and ".." not in d))
    @hyp_settings(max_examples=100)
    def test_jwt_jwks_uri_derived_from_domain(self, domain):
        """**Validates: Requirements 5.3**"""
        s = Settings()
        s.AUTH0_DOMAIN = domain
        assert s.JWT_JWKS_URI == f"https://{domain}/.well-known/jwks.json"


class TestPropertySubClaimExtraction:
    """Feature: user-accounts, Property 3: Sub claim extraction as owner_id

    Validates: Requirements 4.4, 6.1, 6.2
    """

    KID = "prop-test-kid"
    DOMAIN = "prop-test.example.com"
    AUDIENCE = "prop-test-audience"

    def setup_method(self):
        clear_jwks_cache()
        self.private_key, self.public_key = _generate_rsa_keypair()
        self.jwk = _public_key_jwk(self.public_key, kid=self.KID)
        self.settings = Settings()
        self.settings.AUTH0_DOMAIN = self.DOMAIN
        self.settings.AUTH0_AUDIENCE = self.AUDIENCE
        self.app = _make_app(self.settings)
        self.client = TestClient(self.app)
        # Patch JWKS fetch at module level for the duration of the test
        import backend.auth.middleware as mw
        self._original_fetch = mw._fetch_jwks
        mw._fetch_jwks = lambda uri: {"keys": [self.jwk]}

    def teardown_method(self):
        import backend.auth.middleware as mw
        mw._fetch_jwks = self._original_fetch
        clear_jwks_cache()

    @given(sub=st.text(min_size=1, max_size=128).filter(lambda s: s.strip() == s and len(s) > 0))
    @hyp_settings(max_examples=100)
    def test_sub_claim_returned_as_owner_id(self, sub):
        """**Validates: Requirements 4.4, 6.1, 6.2**"""
        clear_jwks_cache()
        token = _make_jwt(
            self.private_key,
            kid=self.KID,
            sub=sub,
            issuer=f"https://{self.DOMAIN}/",
            audience=self.AUDIENCE,
        )
        resp = self.client.get("/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.json()["owner_id"] == sub


class TestPropertyInvalidJWTRejection:
    """Feature: user-accounts, Property 2: Invalid JWT claims are rejected

    Validates: Requirements 4.3, 4.5, 4.6, 4.7
    """

    KID = "prop-test-kid"
    DOMAIN = "prop-test.example.com"
    AUDIENCE = "prop-test-audience"

    def setup_method(self):
        clear_jwks_cache()
        self.private_key, self.public_key = _generate_rsa_keypair()
        self.wrong_private_key, _ = _generate_rsa_keypair()
        self.jwk = _public_key_jwk(self.public_key, kid=self.KID)
        self.settings = Settings()
        self.settings.AUTH0_DOMAIN = self.DOMAIN
        self.settings.AUTH0_AUDIENCE = self.AUDIENCE
        self.app = _make_app(self.settings)
        self.client = TestClient(self.app)
        import backend.auth.middleware as mw
        self._original_fetch = mw._fetch_jwks
        mw._fetch_jwks = lambda uri: {"keys": [self.jwk]}

    def teardown_method(self):
        import backend.auth.middleware as mw
        mw._fetch_jwks = self._original_fetch
        clear_jwks_cache()

    @given(scenario=st.sampled_from(["wrong_key", "expired", "wrong_issuer", "wrong_audience"]))
    @hyp_settings(max_examples=100)
    def test_invalid_jwt_rejected_with_401(self, scenario):
        """**Validates: Requirements 4.3, 4.5, 4.6, 4.7**"""
        clear_jwks_cache()
        issuer = f"https://{self.DOMAIN}/"
        audience = self.AUDIENCE
        key = self.private_key
        exp_offset = 3600

        if scenario == "wrong_key":
            key = self.wrong_private_key
        elif scenario == "expired":
            exp_offset = -3600
        elif scenario == "wrong_issuer":
            issuer = "https://wrong-issuer.example.com/"
        elif scenario == "wrong_audience":
            audience = "wrong-audience"

        token = _make_jwt(key, kid=self.KID, sub="user-1", issuer=issuer, audience=audience, exp_offset=exp_offset)
        resp = self.client.get("/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401


class TestPropertyAuth0SubFormatAcceptance:
    """Feature: user-accounts, Property 5: Auth0 sub format acceptance

    Validates: Requirements 6.3
    """

    KID = "prop-test-kid"
    DOMAIN = "prop-test.example.com"
    AUDIENCE = "prop-test-audience"

    def setup_method(self):
        clear_jwks_cache()
        self.private_key, self.public_key = _generate_rsa_keypair()
        self.jwk = _public_key_jwk(self.public_key, kid=self.KID)
        self.settings = Settings()
        self.settings.AUTH0_DOMAIN = self.DOMAIN
        self.settings.AUTH0_AUDIENCE = self.AUDIENCE
        self.app = _make_app(self.settings)
        self.client = TestClient(self.app)
        import backend.auth.middleware as mw
        self._original_fetch = mw._fetch_jwks
        mw._fetch_jwks = lambda uri: {"keys": [self.jwk]}

    def teardown_method(self):
        import backend.auth.middleware as mw
        mw._fetch_jwks = self._original_fetch
        clear_jwks_cache()

    @given(
        provider=st.sampled_from(["auth0", "google-oauth2", "facebook", "github", "twitter", "windowslive"]),
        user_id=st.text(alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz0123456789"), min_size=1, max_size=32),
    )
    @hyp_settings(max_examples=100)
    def test_auth0_sub_format_accepted(self, provider, user_id):
        """**Validates: Requirements 6.3**"""
        clear_jwks_cache()
        sub = f"{provider}|{user_id}"
        token = _make_jwt(
            self.private_key,
            kid=self.KID,
            sub=sub,
            issuer=f"https://{self.DOMAIN}/",
            audience=self.AUDIENCE,
        )
        resp = self.client.get("/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.json()["owner_id"] == sub
