"""Property-based tests for the Title_Validator pure rules.

Feature: project-titles, Properties 1-5: validate_shape and title_key

Each property targets one acceptance criterion or related cluster from
``.kiro/specs/project-titles/requirements.md`` and is annotated with the
requirement clauses it validates. Tests are pure functions over the
validator module - no service or storage involvement.
"""

from __future__ import annotations

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from backend.services.title_validator import (
    MAX_TITLE_LENGTH,
    TitleErrorCode,
    TitleValidationError,
    title_key,
    validate_shape,
)


# ---------------------------------------------------------------------------
# Reusable strategies
# ---------------------------------------------------------------------------

# Any non-control character. Excludes Cc so that valid-shape strategies stay
# in the validator's accept-set; control characters are reintroduced
# explicitly in Property 4's strategy.
non_control_chars = st.characters(exclude_categories=("Cc",))

# Pure whitespace characters. Combines Unicode space-separator categories
# with the empty string to exercise both the Zs/Zl/Zp branch and len==0.
whitespace_chars = st.characters(categories=("Zs", "Zl", "Zp"))

# Control characters (Cc) for Property 4 contamination.
control_chars = st.characters(categories=("Cc",))


# ---------------------------------------------------------------------------
# Property 1: Valid titles are accepted and trimmed
# ---------------------------------------------------------------------------
# Feature: project-titles, Property 1: For any string with trimmed length
# 1..100 and no Cc characters, validate_shape returns the trimmed value.
# Validates: Requirements 1.1, 4.1, 4.5
@given(
    s=st.text(alphabet=non_control_chars, min_size=1, max_size=100).filter(
        lambda s: 1 <= len(s.strip()) <= MAX_TITLE_LENGTH
    )
)
@settings(max_examples=200, suppress_health_check=[HealthCheck.filter_too_much])
def test_valid_titles_are_accepted_and_trimmed(s: str) -> None:
    """validate_shape(s) returns s.strip() for any in-range, Cc-free input."""
    result = validate_shape(s)
    assert result == s.strip()
    # Returned value is purely the trim - no other transformation.
    assert result in s
    assert 1 <= len(result) <= MAX_TITLE_LENGTH


# ---------------------------------------------------------------------------
# Property 2: Empty or whitespace-only titles are rejected
# ---------------------------------------------------------------------------
# Feature: project-titles, Property 2: For any string whose s.strip() has
# length 0, validate_shape raises TitleValidationError with code title_empty;
# validate_shape(None) raises with code title_required.
# Validates: Requirements 1.3, 4.2, 1.2
@given(
    s=st.text(alphabet=whitespace_chars, min_size=0, max_size=10),
)
@settings(max_examples=200)
def test_whitespace_only_titles_are_rejected(s: str) -> None:
    """Any whitespace-only string (including the empty string) raises EMPTY."""
    # Sanity-check the strategy: every generated value must trim to "".
    assume(s.strip() == "")
    with pytest.raises(TitleValidationError) as exc_info:
        validate_shape(s)
    assert exc_info.value.code == TitleErrorCode.EMPTY


def test_none_title_raises_required() -> None:
    """validate_shape(None) raises TitleValidationError with code title_required."""
    with pytest.raises(TitleValidationError) as exc_info:
        validate_shape(None)
    assert exc_info.value.code == TitleErrorCode.REQUIRED


# ---------------------------------------------------------------------------
# Property 3: Over-length titles are rejected
# ---------------------------------------------------------------------------
# Feature: project-titles, Property 3: For any string whose s.strip() length
# is strictly greater than 100 code points, validate_shape raises with code
# title_too_long.
# Validates: Requirements 4.3
@given(
    s=st.text(alphabet=non_control_chars, min_size=101, max_size=500).filter(
        lambda s: len(s.strip()) > MAX_TITLE_LENGTH
    )
)
@settings(max_examples=200, suppress_health_check=[HealthCheck.filter_too_much])
def test_over_length_titles_are_rejected(s: str) -> None:
    """Trimmed length > 100 raises TOO_LONG (and not EMPTY or CONTROL_CHARS)."""
    with pytest.raises(TitleValidationError) as exc_info:
        validate_shape(s)
    assert exc_info.value.code == TitleErrorCode.TOO_LONG


# ---------------------------------------------------------------------------
# Property 4: Control characters are rejected
# ---------------------------------------------------------------------------
# Feature: project-titles, Property 4: For any string s such that s.strip()
# contains at least one Cc character, validate_shape raises with code
# title_control_chars.
# Validates: Requirements 4.4
@given(
    base=st.text(alphabet=non_control_chars, min_size=1, max_size=50).filter(
        lambda s: 1 <= len(s.strip()) <= MAX_TITLE_LENGTH - 5
    ),
    injected=st.lists(control_chars, min_size=1, max_size=5),
    data=st.data(),
)
@settings(max_examples=200, suppress_health_check=[HealthCheck.filter_too_much])
def test_control_characters_are_rejected(
    base: str, injected: list[str], data: st.DataObject
) -> None:
    """Inserting any Cc chars into a valid base yields a CONTROL_CHARS rejection."""
    # Insert each control char at a random index inside the trimmed region of
    # `base` so the Cc chars survive `strip()` and reach the category check.
    chars = list(base)
    for ch in injected:
        # Constrain index away from the leading/trailing whitespace run so
        # `strip()` cannot remove the injected control char. We pick from
        # the inclusive range covering the non-whitespace span.
        stripped_lead = len(base) - len(base.lstrip())
        stripped_tail = len(base) - len(base.rstrip())
        # The post-strip span lives at indices [stripped_lead, len(base)-stripped_tail].
        # Insertion indices into the live `chars` list shift as we go, but we
        # only need the resulting trimmed string to contain at least one Cc.
        low = stripped_lead
        high = max(low, len(chars) - stripped_tail)
        idx = data.draw(st.integers(min_value=low, max_value=high))
        chars.insert(idx, ch)
    s = "".join(chars)
    # Sanity-check: the trimmed string must actually contain a Cc char,
    # otherwise the property's premise does not apply.
    import unicodedata

    assume(any(unicodedata.category(ch) == "Cc" for ch in s.strip()))
    # Sanity-check: trimmed length must not exceed the limit, else TOO_LONG
    # would fire first and the property would still hold but for the wrong
    # reason. We assert the specific code, so keep the input in-range.
    assume(len(s.strip()) <= MAX_TITLE_LENGTH)
    with pytest.raises(TitleValidationError) as exc_info:
        validate_shape(s)
    assert exc_info.value.code == TitleErrorCode.CONTROL_CHARS


# ---------------------------------------------------------------------------
# Property 5: Title comparison key is trim-and-casefold
# ---------------------------------------------------------------------------
# Feature: project-titles, Property 5: For any pair of strings s and t,
# title_key(s.strip()) == title_key(t.strip()) iff
# s.strip().casefold() == t.strip().casefold().
# Validates: Requirements 3.1
@given(
    s=st.text(max_size=200),
    t=st.text(max_size=200),
)
@settings(max_examples=200)
def test_title_key_is_trim_and_casefold(s: str, t: str) -> None:
    """The comparison key is exactly trim-then-casefold (biconditional)."""
    keys_equal = title_key(s.strip()) == title_key(t.strip())
    casefold_equal = s.strip().casefold() == t.strip().casefold()
    assert keys_equal == casefold_equal
    # Also assert the direct identity for any single string: title_key is
    # the casefold of the trimmed value.
    assert title_key(s.strip()) == s.strip().casefold()
    assert title_key(t.strip()) == t.strip().casefold()
