"""Authentication package for the Story Video Editor API."""

from backend.auth.middleware import get_owner_id, verify_project_ownership

__all__ = ["get_owner_id", "verify_project_ownership"]
