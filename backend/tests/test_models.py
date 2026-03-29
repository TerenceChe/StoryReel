"""Property-based tests for data models.

Feature: story-video-editor, Property 5: Subtitle timing validation
Validates: Requirements 5.2, 5.3
"""

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st
from pydantic import ValidationError

from backend.models import SubtitleSegment, Position, SubtitleStyle


# Reusable strategies
reasonable_floats = st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False)

def make_segment(start_time: float, end_time: float) -> SubtitleSegment:
    """Helper to build a SubtitleSegment with the given timing."""
    return SubtitleSegment(
        id="test-id",
        text="测试",
        start_time=start_time,
        end_time=end_time,
        position=Position(x=0.5, y=0.85),
        style=SubtitleStyle(),
    )


class TestSubtitleTimingValidation:
    """Property 5: Subtitle timing validation.

    For any pair of (start_time, end_time) values, a subtitle timing update
    should succeed if and only if start_time < end_time. When start_time >= end_time,
    the update should be rejected with a validation error.
    """

    @given(
        start=reasonable_floats,
        end=reasonable_floats,
    )
    @settings(max_examples=200)
    def test_valid_timing_accepted(self, start: float, end: float):
        """When start_time < end_time, the segment should be created successfully."""
        assume(start < end)
        segment = make_segment(start, end)
        assert segment.start_time == start
        assert segment.end_time == end

    @given(
        value=reasonable_floats,
    )
    @settings(max_examples=200)
    def test_equal_timing_rejected(self, value: float):
        """When start_time == end_time, creation should raise ValidationError."""
        with pytest.raises(ValidationError, match="start_time"):
            make_segment(value, value)

    @given(
        start=reasonable_floats,
        end=reasonable_floats,
    )
    @settings(max_examples=200)
    def test_start_greater_than_end_rejected(self, start: float, end: float):
        """When start_time > end_time, creation should raise ValidationError."""
        assume(start > end)
        with pytest.raises(ValidationError, match="start_time"):
            make_segment(start, end)

    @given(
        start=reasonable_floats,
        end=reasonable_floats,
    )
    @settings(max_examples=200)
    def test_timing_validation_is_exhaustive(self, start: float, end: float):
        """For any start/end pair, creation succeeds iff start < end."""
        if start < end:
            segment = make_segment(start, end)
            assert segment.start_time == start
            assert segment.end_time == end
        else:
            with pytest.raises(ValidationError):
                make_segment(start, end)
