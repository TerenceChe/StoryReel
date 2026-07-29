"""Capability endpoint for AI background image generation.

This router exposes a single read-only endpoint that the editor's
frontend polls once per page session to decide whether to enable the
"Generate AI background" controls. The response is **operator-opaque**
by design — it carries a single boolean and nothing else. Provider
names, environment variable names, README pointers, configuration
instructions, and any other operator-facing setup detail MUST NOT
appear in the response body. Property 1 in the design document
(`.kiro/specs/ai-background-generation/design.md`) pins this
constraint, and Requirement 1.4 plus Requirement 1.7 enforce it.

The capability is computed from two signals:

* The runtime type of the bound :class:`ImageGenerationBackend`. When
  no provider is configured at startup the dependency layer binds
  :class:`DisabledImageBackend`, and we report the feature as
  unavailable.
* The process-wide :class:`CapabilityState` flag. The JobManager flips
  this off when the provider returns an authentication failure during
  a running job (Requirement 5.5). Once flipped off the capability
  stays disabled until the process restarts.

Both signals must be true for the capability to report enabled. This
matches the design's "and" semantics: a provider-configured deployment
that has observed an auth failure mid-session is still reported as
unavailable, and a deployment with the disabled fallback is reported
as unavailable regardless of any later capability flip.

The route is included in ``backend/main.py`` by Task 15.2; this
module's only job is to define the router so it can be imported and
mounted later.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from backend.auth.middleware import get_owner_id
from backend.dependencies import get_image_backend
from backend.models.image_gen import (
    DisabledImageBackend,
    ImageGenerationBackend,
)
from backend.services.image_capability_state import capability_state

router = APIRouter(prefix="/image-generation", tags=["image-generation"])


@router.get("/capability")
async def get_capability(
    _owner_id: str = Depends(get_owner_id),
    backend: ImageGenerationBackend = Depends(get_image_backend),
) -> dict:
    """Return whether AI background generation is available.

    The response shape is intentionally minimal:

    .. code-block:: json

        {"image_generation_enabled": true}

    No other fields appear in the body. The client uses this single
    boolean to enable or disable its generation controls and must not
    expect any operator-facing metadata in the response.
    """
    enabled = (
        not isinstance(backend, DisabledImageBackend)
        and capability_state.is_enabled
    )
    return {"image_generation_enabled": enabled}
