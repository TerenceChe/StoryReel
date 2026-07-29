"""OpenAI concrete adapter for the :class:`ImageGenerationBackend` interface.

This is the only module in the codebase that imports a real provider SDK.
The HTTP routing layer and the :class:`JobManager` depend solely on the
abstract :class:`backend.models.image_gen.ImageGenerationBackend` plus the
duck-typed ``generate_candidates`` / ``generate_section_candidates``
methods; the dependency container selects this adapter at startup when
``IMAGE_GEN_PROVIDER=openai`` and ``OPENAI_API_KEY`` is provisioned.

Security discipline (Requirements 9.1, 9.2):

* The API key is received once at construction from the dependency
  container (which reads it from the environment). It is **never**
  re-read inside methods, **never** logged, and **never** placed into an
  exception message that bubbles to the JobManager. The key is held only
  inside the ``AsyncOpenAI`` client instance — this adapter does not keep
  it as its own attribute.
* All provider exceptions are mapped to one of two outward-facing types:

  - :class:`ProviderAuthenticationError` for 401 / 403 responses, which
    the JobManager catches specifically to flip the capability state for
    the rest of the session (Requirement 5.5).
  - A generic :class:`RuntimeError` (``"image generation failed"``) for
    every other failure mode — network errors, malformed responses,
    rate limits, decoding failures, etc. The JobManager logs only a
    sanitized category at WARN level and surfaces a generic
    user-facing error message; raw provider error text never reaches a
    client or a log line.

  Both raises use ``from None`` so the original SDK exception (which may
  carry sensitive request context) is dropped from the chain entirely.

Filename note:

    The module is named ``openai.py`` to match the package convention,
    which collides with the third-party ``openai`` distribution name.
    Python 3's absolute imports keep ``import openai as openai_sdk`` at
    the top of this file resolving to the SDK rather than to this very
    module, but we use the explicit alias to make the intent obvious to
    future readers.
"""

from __future__ import annotations

import base64
import io

import openai as openai_sdk

from backend.models.image_gen import ImageGenerationBackend
from backend.services.image_job_errors import ProviderAuthenticationError

# The modern OpenAI image model. Held as a module constant so swapping it
# in the future is a one-line change.
_IMAGE_MODEL = "gpt-image-1"

# Generic, operator-opaque error string that bubbles to the JobManager
# when a non-auth provider failure occurs. The JobManager substitutes its
# own user-facing message into the failed-job ``error_message`` field;
# this constant exists only so reviewers can see the exact text that
# leaves the adapter.
_GENERIC_PROVIDER_FAILURE = "image generation failed"


class OpenAIImageBackend(ImageGenerationBackend):
    """Concrete :class:`ImageGenerationBackend` backed by the OpenAI API."""

    def __init__(self, api_key: str) -> None:
        # Hand the credential straight to the SDK client. We do not keep
        # ``api_key`` as an attribute on this object so an accidental
        # ``repr(backend)`` or ``vars(backend)`` cannot leak it.
        self._client = openai_sdk.AsyncOpenAI(api_key=api_key)

    # ------------------------------------------------------------------
    # Abstract base methods (kept for backward compatibility with code
    # paths that haven't migrated to the candidate variants).
    # ------------------------------------------------------------------

    async def generate_single(self, prompt: str) -> bytes:
        results = await self.generate_candidates(
            prompt, image_count=1, reference_image_bytes=None
        )
        return results[0]

    async def generate_sectioned(self, prompts: list[str]) -> list[bytes]:
        return [await self.generate_single(prompt) for prompt in prompts]

    # ------------------------------------------------------------------
    # Duck-typed candidate methods used by the JobManager worker.
    # ------------------------------------------------------------------

    async def generate_candidates(
        self,
        prompt: str,
        *,
        image_count: int,
        reference_image_bytes: bytes | None,
    ) -> list[bytes]:
        """Generate ``image_count`` candidates for a single prompt.

        When ``reference_image_bytes`` is provided, the call routes to
        ``images.edit`` so the provider conditions on the reference; when
        ``None``, it routes to ``images.generate`` for plain text-to-image.
        Each result's ``b64_json`` payload is decoded back to raw image
        bytes before returning.
        """
        try:
            if reference_image_bytes is None:
                response = await self._client.images.generate(
                    model=_IMAGE_MODEL,
                    prompt=prompt,
                    n=image_count,
                    response_format="b64_json",
                )
            else:
                # The SDK accepts a file-like object for the reference.
                # ``BytesIO`` keeps the bytes in memory; we never persist
                # the reference inside this adapter.
                reference_stream = io.BytesIO(reference_image_bytes)
                # ``images.edit`` inspects the stream's ``name`` attribute
                # to infer the content type. We give it a stable filename
                # so the SDK can construct a valid multipart part without
                # exposing operator-side filesystem paths.
                reference_stream.name = "reference.png"
                response = await self._client.images.edit(
                    model=_IMAGE_MODEL,
                    image=reference_stream,
                    prompt=prompt,
                    n=image_count,
                    response_format="b64_json",
                )
            return [_decode_b64_image(item.b64_json) for item in response.data]
        except (
            openai_sdk.AuthenticationError,
            openai_sdk.PermissionDeniedError,
        ):
            # Map 401 / 403 to the typed auth error so the JobManager can
            # flip capability state. ``from None`` drops the original
            # exception chain so its message never reaches a log or
            # response surface via ``__cause__``.
            raise ProviderAuthenticationError(
                "AI background generation is currently unavailable"
            ) from None
        except Exception:
            # Any other failure mode — network error, malformed response,
            # decoding failure, rate limit, internal server error — maps
            # to a generic RuntimeError. Suppressing the chain keeps the
            # provider's error text out of the bubbled exception.
            raise RuntimeError(_GENERIC_PROVIDER_FAILURE) from None

    async def generate_section_candidates(
        self,
        prompts: list[str],
        *,
        image_count: int,
        reference_image_bytes: bytes | None,
    ) -> list[list[bytes]]:
        """Per-prompt fan-out of :meth:`generate_candidates`.

        The JobManager aggregates the resulting ``list[list[bytes]]`` into
        section-scoped candidates. Each prompt receives the same
        ``reference_image_bytes`` (the operator-uploaded reference applies
        across the whole section job) and the same ``image_count``.
        """
        results: list[list[bytes]] = []
        for prompt in prompts:
            results.append(
                await self.generate_candidates(
                    prompt,
                    image_count=image_count,
                    reference_image_bytes=reference_image_bytes,
                )
            )
        return results


def _decode_b64_image(b64_payload: str | None) -> bytes:
    """Decode a base64 image payload to raw bytes.

    Raises :class:`ValueError` on a missing or malformed payload; the
    caller wraps that into the generic :class:`RuntimeError` the
    JobManager surfaces. Kept as a module-level helper so the unit tests
    can exercise the decoding path directly without instantiating the
    full adapter.
    """
    if not b64_payload:
        raise ValueError("missing image payload")
    return base64.b64decode(b64_payload)
