"""Property-based test for DisabledImageBackend.

Feature: ai-background-generation, Property 8: Disabled backend declines all
generation calls

Tagged docstring per spec: every call into the four generation methods on
:class:`backend.models.image_gen.DisabledImageBackend` must raise
:class:`backend.models.image_gen.ImageGenerationDisabledError`, regardless of
the prompt(s) or parameters supplied.
"""

from __future__ import annotations

import asyncio

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from backend.models.image_gen import (
    DisabledImageBackend,
    ImageGenerationDisabledError,
)

# Method identifiers used to randomize which of the four generation methods
# the property exercises on each example. Using st.sampled_from over these
# keeps the test reading as one property over the cartesian product
# {method} × {arbitrary inputs}.
_METHODS = (
    "generate_single",
    "generate_sectioned",
    "generate_candidates",
    "generate_section_candidates",
)


# Reference-image strategy: arbitrary bytes or None. Bounded length so the
# 100 examples do not allocate large buffers; the disabled backend never
# inspects the bytes regardless.
reference_bytes_strategy = st.one_of(
    st.none(),
    st.binary(min_size=0, max_size=64),
)


# ---------------------------------------------------------------------------
# Property 8: Disabled backend declines all generation calls
# ---------------------------------------------------------------------------
# Feature: ai-background-generation, Property 8: Disabled backend declines all
# generation calls
# Validates: Requirements 8.2, 8.4
@given(
    method=st.sampled_from(_METHODS),
    prompt=st.text(max_size=200),
    prompts=st.lists(st.text(max_size=100), min_size=0, max_size=8),
    image_count=st.integers(min_value=-10, max_value=20),
    reference_image_bytes=reference_bytes_strategy,
)
@settings(max_examples=100)
def test_disabled_backend_declines_all_generation_calls(
    method: str,
    prompt: str,
    prompts: list[str],
    image_count: int,
    reference_image_bytes: bytes | None,
) -> None:
    """Every call into any of the four generation methods raises.

    Feature: ai-background-generation, Property 8: Disabled backend declines all
    generation calls
    Validates: Requirements 8.2, 8.4
    """
    backend = DisabledImageBackend()

    async def _invoke() -> None:
        if method == "generate_single":
            await backend.generate_single(prompt)
        elif method == "generate_sectioned":
            await backend.generate_sectioned(prompts)
        elif method == "generate_candidates":
            await backend.generate_candidates(
                prompt,
                image_count=image_count,
                reference_image_bytes=reference_image_bytes,
            )
        else:  # generate_section_candidates
            await backend.generate_section_candidates(
                prompts,
                image_count=image_count,
                reference_image_bytes=reference_image_bytes,
            )

    with pytest.raises(ImageGenerationDisabledError):
        asyncio.run(_invoke())
