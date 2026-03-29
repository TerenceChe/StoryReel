"""Persistence layer — storage backend abstraction and implementations."""

from backend.persistence.base import StorageBackend
from backend.persistence.local import LocalStorageBackend

__all__ = ["StorageBackend", "LocalStorageBackend"]
