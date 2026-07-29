"""Tests for ProjectState section_backgrounds field.

Feature: ai-background-generation
Validates: Requirement 3.6
"""

from backend.models.project import ProjectState, SectionBackground


def _base_payload() -> dict:
    """Build a minimal valid ProjectState dict with all required fields filled in.

    Required fields on ProjectState (no defaults): id, owner_id, title,
    story_text, pipeline_progress, created_at, updated_at.
    """
    return {
        "id": "proj-123",
        "owner_id": "user-abc",
        "title": "My Project",
        "story_text": "Once upon a time...",
        "pipeline_progress": {
            "stage": "narration",
            "message": "Starting narration",
        },
        "created_at": "2025-01-01T00:00:00+00:00",
        "updated_at": "2025-01-01T00:00:00+00:00",
    }


class TestProjectStateSectionBackgroundsBackwardsCompat:
    """Validates: Requirement 3.6.

    A pre-feature `state.json` payload (one without the
    `section_backgrounds` key at all) MUST continue to load successfully,
    with `section_backgrounds` defaulting to an empty list.
    """

    def test_load_without_section_backgrounds_defaults_to_empty_list(self):
        """ProjectState JSON missing `section_backgrounds` parses with []."""
        payload = _base_payload()
        # Sanity-check: the field is genuinely absent from the payload, so
        # this exercises the default rather than a present-but-empty list.
        assert "section_backgrounds" not in payload

        state = ProjectState.model_validate(payload)

        assert state.section_backgrounds == []

    def test_load_with_populated_section_backgrounds_typed_correctly(self):
        """A payload WITH `section_backgrounds` parses entries as typed instances."""
        payload = _base_payload()
        payload["section_backgrounds"] = [
            {"start_index": 0, "end_index": 2, "image_url": "/media/bg-a.png"},
            {"start_index": 3, "end_index": 5, "image_url": "/media/bg-b.png"},
        ]

        state = ProjectState.model_validate(payload)

        assert len(state.section_backgrounds) == 2
        for entry in state.section_backgrounds:
            assert isinstance(entry, SectionBackground)

        first, second = state.section_backgrounds
        assert (first.start_index, first.end_index, first.image_url) == (0, 2, "/media/bg-a.png")
        assert (second.start_index, second.end_index, second.image_url) == (3, 5, "/media/bg-b.png")
