"""Unit tests for the OpenAI image-generation adapter.

These tests do **not** call the real OpenAI API. They mock the SDK's
``AsyncOpenAI.images.generate`` and ``.images.edit`` async methods so the
adapter's translation layer (b64 decoding, exception mapping, secret
hygiene) is exercised in isolation.
"""

from __future__ import annotations

import asyncio
import base64
from unittest.mock import AsyncMock, MagicMock, patch

import openai as openai_sdk
import pytest

from backend.models.image_gen import ImageGenerationBackend
from backend.services.image_backends.openai import OpenAIImageBackend
from backend.services.image_job_errors import ProviderAuthenticationError

# A distinctive synthetic API key value the secret-hygiene assertions
# search for in any exception text. The real adapter must never echo the
# key into a message that bubbles to the JobManager.
_TEST_API_KEY = "sk-test-SECRETKEYVALUE-do-not-leak-1234567890"


def _make_response(payloads: list[bytes]) -> MagicMock:
    """Build a mock SDK response object whose ``data`` items expose
    ``b64_json`` strings encoding the given raw image payloads.
    """
    response = MagicMock()
    response.data = [
        MagicMock(b64_json=base64.b64encode(payload).decode("ascii"))
        for payload in payloads
    ]
    return response


def _build_sdk_error(error_cls):
    """Construct an instance of an SDK error class.

    The ``openai`` SDK error classes derive from ``APIStatusError`` which
    requires ``response`` and ``body`` keyword arguments. We bypass that
    by defining a tiny subclass with a permissive ``__init__`` so the
    test only depends on the runtime type — the adapter's ``except``
    clauses match by class hierarchy.
    """
    bypass_cls = type(
        f"_Test{error_cls.__name__}",
        (error_cls,),
        {"__init__": lambda self, msg: Exception.__init__(self, msg)},
    )
    return bypass_cls("provider error with leaked text")


# ---------------------------------------------------------------------------
# Interface conformance
# ---------------------------------------------------------------------------


def test_adapter_implements_abstract_interface() -> None:
    """OpenAIImageBackend is an ImageGenerationBackend."""
    backend = OpenAIImageBackend(api_key=_TEST_API_KEY)
    assert isinstance(backend, ImageGenerationBackend)


def test_adapter_does_not_store_api_key_as_attribute() -> None:
    """The api_key argument must not surface as a public attribute.

    The key is held inside the SDK client. Having it as a plain attribute
    on the adapter would risk accidental leaks via ``repr`` / ``vars``.
    """
    backend = OpenAIImageBackend(api_key=_TEST_API_KEY)
    public_attrs = {k: v for k, v in vars(backend).items() if not k.startswith("_")}
    assert _TEST_API_KEY not in str(public_attrs)
    # Even the private form is fine to hold the SDK client; we only assert
    # the literal key string isn't sitting on the adapter as a raw value.
    assert getattr(backend, "api_key", None) is None
    assert getattr(backend, "_api_key", None) is None


# ---------------------------------------------------------------------------
# generate_candidates: text-to-image path (no reference)
# ---------------------------------------------------------------------------


def test_generate_candidates_decodes_b64_json_into_bytes() -> None:
    """Each ``b64_json`` payload from the SDK is decoded back to raw bytes."""
    backend = OpenAIImageBackend(api_key=_TEST_API_KEY)

    expected = [b"image-bytes-0", b"image-bytes-1", b"image-bytes-2"]
    fake_response = _make_response(expected)

    backend._client.images.generate = AsyncMock(return_value=fake_response)

    result = asyncio.run(
        backend.generate_candidates(
            "a moonlit forest",
            image_count=3,
            reference_image_bytes=None,
        )
    )

    assert result == expected
    backend._client.images.generate.assert_awaited_once()
    kwargs = backend._client.images.generate.await_args.kwargs
    assert kwargs["prompt"] == "a moonlit forest"
    assert kwargs["n"] == 3
    assert kwargs["response_format"] == "b64_json"
    assert kwargs["model"] == "gpt-image-1"


def test_generate_candidates_routes_to_edit_when_reference_provided() -> None:
    """A reference image routes to ``images.edit`` rather than ``.generate``."""
    backend = OpenAIImageBackend(api_key=_TEST_API_KEY)

    fake_response = _make_response([b"edited-0"])
    backend._client.images.edit = AsyncMock(return_value=fake_response)
    backend._client.images.generate = AsyncMock(
        side_effect=AssertionError("generate must not be called when reference present")
    )

    result = asyncio.run(
        backend.generate_candidates(
            "tweak this",
            image_count=1,
            reference_image_bytes=b"\x89PNG\r\n\x1a\nfake-png-bytes",
        )
    )

    assert result == [b"edited-0"]
    backend._client.images.edit.assert_awaited_once()
    backend._client.images.generate.assert_not_awaited()


# ---------------------------------------------------------------------------
# Exception mapping
# ---------------------------------------------------------------------------


def test_authentication_error_maps_to_provider_authentication_error() -> None:
    """SDK ``AuthenticationError`` -> ``ProviderAuthenticationError``."""
    backend = OpenAIImageBackend(api_key=_TEST_API_KEY)

    sdk_err = _build_sdk_error(openai_sdk.AuthenticationError)
    backend._client.images.generate = AsyncMock(side_effect=sdk_err)

    with pytest.raises(ProviderAuthenticationError) as excinfo:
        asyncio.run(
            backend.generate_candidates(
                "anything",
                image_count=1,
                reference_image_bytes=None,
            )
        )

    # ``from None`` was used, so the chain is suppressed.
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__suppress_context__ is True


def test_permission_denied_error_maps_to_provider_authentication_error() -> None:
    """SDK ``PermissionDeniedError`` (403) -> ``ProviderAuthenticationError``."""
    backend = OpenAIImageBackend(api_key=_TEST_API_KEY)

    sdk_err = _build_sdk_error(openai_sdk.PermissionDeniedError)
    backend._client.images.generate = AsyncMock(side_effect=sdk_err)

    with pytest.raises(ProviderAuthenticationError):
        asyncio.run(
            backend.generate_candidates(
                "anything",
                image_count=1,
                reference_image_bytes=None,
            )
        )


def test_other_sdk_errors_map_to_generic_runtime_error() -> None:
    """Non-auth SDK errors map to a generic RuntimeError without inner text."""
    backend = OpenAIImageBackend(api_key=_TEST_API_KEY)

    sdk_err = _build_sdk_error(openai_sdk.RateLimitError)
    backend._client.images.generate = AsyncMock(side_effect=sdk_err)

    with pytest.raises(RuntimeError) as excinfo:
        asyncio.run(
            backend.generate_candidates(
                "anything",
                image_count=1,
                reference_image_bytes=None,
            )
        )

    assert str(excinfo.value) == "image generation failed"
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__suppress_context__ is True


def test_arbitrary_exception_maps_to_generic_runtime_error() -> None:
    """Non-SDK exceptions (network, decode) also map to the generic message."""
    backend = OpenAIImageBackend(api_key=_TEST_API_KEY)

    backend._client.images.generate = AsyncMock(
        side_effect=ConnectionError("network blew up: " + _TEST_API_KEY)
    )

    with pytest.raises(RuntimeError) as excinfo:
        asyncio.run(
            backend.generate_candidates(
                "anything",
                image_count=1,
                reference_image_bytes=None,
            )
        )

    assert str(excinfo.value) == "image generation failed"


def test_malformed_b64_payload_maps_to_generic_runtime_error() -> None:
    """A missing ``b64_json`` field surfaces as the generic RuntimeError."""
    backend = OpenAIImageBackend(api_key=_TEST_API_KEY)

    fake_response = MagicMock()
    fake_response.data = [MagicMock(b64_json=None)]
    backend._client.images.generate = AsyncMock(return_value=fake_response)

    with pytest.raises(RuntimeError) as excinfo:
        asyncio.run(
            backend.generate_candidates(
                "anything",
                image_count=1,
                reference_image_bytes=None,
            )
        )

    assert str(excinfo.value) == "image generation failed"


# ---------------------------------------------------------------------------
# Secret hygiene: bubbled exceptions never carry the API key value
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "trigger_exc",
    [
        _build_sdk_error(openai_sdk.AuthenticationError),
        _build_sdk_error(openai_sdk.PermissionDeniedError),
        _build_sdk_error(openai_sdk.RateLimitError),
        _build_sdk_error(openai_sdk.BadRequestError),
        ConnectionError("boom"),
        ValueError("bad json"),
    ],
)
def test_bubbled_exceptions_do_not_contain_api_key_or_sdk_text(trigger_exc) -> None:
    """No mapped exception (or its chain) includes the API key value or
    SDK-internal text.
    """
    backend = OpenAIImageBackend(api_key=_TEST_API_KEY)
    backend._client.images.generate = AsyncMock(side_effect=trigger_exc)

    raised: BaseException
    try:
        asyncio.run(
            backend.generate_candidates(
                "anything",
                image_count=1,
                reference_image_bytes=None,
            )
        )
        pytest.fail("expected an exception")
    except BaseException as exc:
        raised = exc

    # The bubbled exception's own message must be either the typed
    # auth-failure user message or the generic RuntimeError string.
    msg = str(raised)
    assert _TEST_API_KEY not in msg
    assert "provider error" not in msg
    assert "leaked" not in msg
    assert "api_key" not in msg.lower()

    # ``from None`` drops the cause/context. Verify nothing in the chain
    # carries the original SDK text either.
    assert raised.__cause__ is None
    assert raised.__suppress_context__ is True


# ---------------------------------------------------------------------------
# generate_section_candidates: per-prompt fan-out
# ---------------------------------------------------------------------------


def test_generate_section_candidates_aggregates_per_prompt() -> None:
    """Returns a ``list[list[bytes]]`` matching prompts × image_count."""
    backend = OpenAIImageBackend(api_key=_TEST_API_KEY)

    # Each prompt produces 2 images; we set up generate() to return a fresh
    # response each call.
    payloads_per_call = [
        [b"p0-img0", b"p0-img1"],
        [b"p1-img0", b"p1-img1"],
        [b"p2-img0", b"p2-img1"],
    ]
    responses = [_make_response(p) for p in payloads_per_call]
    backend._client.images.generate = AsyncMock(side_effect=responses)

    result = asyncio.run(
        backend.generate_section_candidates(
            ["prompt-a", "prompt-b", "prompt-c"],
            image_count=2,
            reference_image_bytes=None,
        )
    )

    assert result == payloads_per_call
    assert backend._client.images.generate.await_count == 3


# ---------------------------------------------------------------------------
# generate_single / generate_sectioned compatibility
# ---------------------------------------------------------------------------


def test_generate_single_returns_first_candidate_bytes() -> None:
    backend = OpenAIImageBackend(api_key=_TEST_API_KEY)

    backend._client.images.generate = AsyncMock(
        return_value=_make_response([b"only-image"])
    )

    result = asyncio.run(backend.generate_single("hi"))
    assert result == b"only-image"

    kwargs = backend._client.images.generate.await_args.kwargs
    assert kwargs["n"] == 1


def test_generate_sectioned_returns_one_image_per_prompt() -> None:
    backend = OpenAIImageBackend(api_key=_TEST_API_KEY)

    backend._client.images.generate = AsyncMock(
        side_effect=[
            _make_response([b"section-0"]),
            _make_response([b"section-1"]),
        ]
    )

    result = asyncio.run(backend.generate_sectioned(["p0", "p1"]))
    assert result == [b"section-0", b"section-1"]


# ---------------------------------------------------------------------------
# Constructor doesn't trigger any network activity
# ---------------------------------------------------------------------------


def test_constructor_does_not_perform_network_call() -> None:
    """Constructing the adapter only initializes the SDK client."""
    with patch.object(openai_sdk, "AsyncOpenAI") as mock_client_cls:
        OpenAIImageBackend(api_key=_TEST_API_KEY)
        mock_client_cls.assert_called_once_with(api_key=_TEST_API_KEY)
