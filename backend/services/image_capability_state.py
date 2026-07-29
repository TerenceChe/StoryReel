"""Process-level capability flag for AI image generation.

The capability endpoint and the image-jobs router both consult a single
process-wide flag that the JobManager flips off when the provider returns
an authentication failure during a running job. Once flipped off, the
capability stays disabled for the remainder of the process lifetime — an
operator key rotation re-enables the feature on the next deployment, as
the design's Error Handling section spells out.

Design constraints baked into this module:

* The capability is **operator-opaque**. Nothing in this module touches
  provider names, environment variable names, API key values, or the
  configured ``Settings``. It is a single boolean and the verbs that move
  it.
* The flag is **monotonic during a session**: ``disable_for_session`` is
  one-way for the lifetime of the process. The only way back to enabled is
  a process restart (or, in tests, the explicit :func:`reset` hook).
* The singleton is a **process-level default**. Tests that need
  isolation construct a fresh ``CapabilityState`` instance and inject it
  through the FastAPI dependency layer; production code reaches for the
  module-level :data:`capability_state`.
"""

from __future__ import annotations


class CapabilityState:
    """Tracks whether AI image generation is available for this process.

    The class deliberately holds *only* a boolean. Any policy decision
    (e.g. "is the bound backend the disabled fallback?") lives at the
    capability route — this object's single job is to remember an
    auth-failure flip.
    """

    def __init__(self) -> None:
        self._enabled: bool = True

    @property
    def is_enabled(self) -> bool:
        """``True`` while the capability is allowed for this session."""
        return self._enabled

    def disable_for_session(self) -> None:
        """Flip the capability off for the remainder of the process.

        Called by the JobManager when a provider authentication failure
        is observed. Idempotent — repeated calls are a no-op once
        disabled.
        """
        self._enabled = False

    def reset(self) -> None:
        """Restore the enabled state.

        Intended for tests and for explicit operator-driven reinitialization
        in development. Production code does not call this; an authentic
        re-enable happens via process restart so the new
        :class:`backend.models.image_gen.ImageGenerationBackend` is bound
        from a fresh environment.
        """
        self._enabled = True


# Process-level default singleton. Imported as
# ``from backend.services.image_capability_state import capability_state``
# by both the JobManager (to flip on auth failure) and the capability
# route (to read).
capability_state = CapabilityState()


__all__ = ["CapabilityState", "capability_state"]
