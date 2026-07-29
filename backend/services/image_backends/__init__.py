"""Concrete provider adapters for the :class:`ImageGenerationBackend` interface.

Each module in this package implements one provider integration. The
package boundary makes it explicit that **only** these modules import a
real provider SDK — the router and JobManager layers depend solely on the
abstract interface (plus the duck-typed candidate methods) and never
import a concrete adapter directly. The dependency container in
``backend/dependencies.py`` is the single place that selects an adapter
at startup based on environment configuration.
"""

__all__ = ["OpenAIImageBackend"]

from backend.services.image_backends.openai import OpenAIImageBackend  # noqa: E402,F401
