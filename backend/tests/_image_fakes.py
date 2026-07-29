"""Fake image-generation backend for image-job tests.

This module is named with a leading underscore so pytest does not try to
collect it as a test module; the property tests in
``backend/tests/test_image_jobs_*.py`` (and ``test_image_backend_disabled.py``)
import :class:`FakeImageBackend` directly.

The fake satisfies two contracts:

1. The abstract :class:`backend.models.image_gen.ImageGenerationBackend`
   interface (``generate_single``, ``generate_sectioned``).
2. The duck-typed candidate-method contract used by the JobManager worker
   (``generate_candidates``, ``generate_section_candidates``) per the
   design's "Provider Adapter" section.

Behavior is deterministic so property tests can assert on call counts
without flakiness:

* Candidate methods return exactly ``image_count`` payloads. Each payload
  embeds a stable prompt hash and the candidate index, e.g.
  ``b"fake-image-<8 hex chars>-0"``.
* ``generate_single`` returns a single deterministic payload.
* ``generate_sectioned`` returns one payload per prompt, each tagged with
  the prompt hash.

Test toggles:

* ``simulate_auth_failure`` — when ``True``, every method raises
  :class:`backend.services.image_job_errors.ProviderAuthenticationError`.
  This is what the JobManager's auth-failure-flips-capability path keys on.
* ``calls`` — list of ``{"method": str, ...kwargs}`` dicts recording every
  invocation in order, for assertions in tests.
* ``last_reference_image`` — the most recent ``reference_image_bytes`` the
  fake observed via the candidate methods, so tests can assert the
  reference attachment path actually feeds bytes into the backend.
* ``api_key_marker`` — an optional synthetic value the response-safety
  property test (Property 1) injects to verify it never appears in any
  HTTP response body. The fake stores the marker on the instance so the
  test can reference it for assertions, but the fake **never** echoes
  the marker into call records, return payloads, or exception messages.
  The point of the property is to assert no leak occurs; the fake's job
  is to simply hold the marker as a stand-in for a real provider key.
"""

from __future__ import annotations

import hashlib

from backend.models.image_gen import ImageGenerationBackend
from backend.services.image_job_errors import ProviderAuthenticationError


def _prompt_hash(prompt: str) -> str:
    """Return a short, stable hex digest of ``prompt``.

    ``hashlib.sha256`` is process-stable (unlike ``hash()``, which is
    randomized via ``PYTHONHASHSEED``), so the resulting bytes are fully
    reproducible across test runs and across worker processes.
    """
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:8]


class FakeImageBackend(ImageGenerationBackend):
    """In-memory fake that records calls and returns deterministic bytes."""

    def __init__(self, api_key_marker: str | None = None) -> None:
        # Per-instance state — each test gets a fresh instance via the
        # ``fake_image_backend`` fixture, so these never need to be reset
        # explicitly inside the test body.
        self.simulate_auth_failure: bool = False
        self.calls: list[dict] = []
        self.last_reference_image: bytes | None = None
        # Synthetic API key value, recorded but never echoed. The
        # response-safety property test (Property 1, Task 13.2) injects
        # a distinctive string here and asserts it never appears in any
        # HTTP response. Storing it on the instance models the
        # real-adapter pattern — concrete adapters carry the operator
        # key as instance state but must never let it cross the
        # backend/frontend boundary.
        self.api_key_marker: str | None = api_key_marker

    # ------------------------------------------------------------------
    # Abstract base methods
    # ------------------------------------------------------------------

    async def generate_single(self, prompt: str) -> bytes:
        self.calls.append({"method": "generate_single", "prompt": prompt})
        if self.simulate_auth_failure:
            raise ProviderAuthenticationError(
                "AI background generation is currently unavailable"
            )
        return f"fake-image-{_prompt_hash(prompt)}".encode("utf-8")

    async def generate_sectioned(self, prompts: list[str]) -> list[bytes]:
        self.calls.append({"method": "generate_sectioned", "prompts": list(prompts)})
        if self.simulate_auth_failure:
            raise ProviderAuthenticationError(
                "AI background generation is currently unavailable"
            )
        return [
            f"fake-image-{_prompt_hash(p)}".encode("utf-8") for p in prompts
        ]

    # ------------------------------------------------------------------
    # Duck-typed candidate methods (used by JobManager worker)
    # ------------------------------------------------------------------

    async def generate_candidates(
        self,
        prompt: str,
        *,
        image_count: int,
        reference_image_bytes: bytes | None,
    ) -> list[bytes]:
        self.calls.append(
            {
                "method": "generate_candidates",
                "prompt": prompt,
                "image_count": image_count,
                "has_reference": reference_image_bytes is not None,
            }
        )
        self.last_reference_image = reference_image_bytes
        if self.simulate_auth_failure:
            raise ProviderAuthenticationError(
                "AI background generation is currently unavailable"
            )
        ph = _prompt_hash(prompt)
        return [f"fake-image-{ph}-{i}".encode("utf-8") for i in range(image_count)]

    async def generate_section_candidates(
        self,
        prompts: list[str],
        *,
        image_count: int,
        reference_image_bytes: bytes | None,
    ) -> list[list[bytes]]:
        self.calls.append(
            {
                "method": "generate_section_candidates",
                "prompts": list(prompts),
                "image_count": image_count,
                "has_reference": reference_image_bytes is not None,
            }
        )
        self.last_reference_image = reference_image_bytes
        if self.simulate_auth_failure:
            raise ProviderAuthenticationError(
                "AI background generation is currently unavailable"
            )
        result: list[list[bytes]] = []
        for prompt in prompts:
            ph = _prompt_hash(prompt)
            result.append(
                [f"fake-image-{ph}-{i}".encode("utf-8") for i in range(image_count)]
            )
        return result
