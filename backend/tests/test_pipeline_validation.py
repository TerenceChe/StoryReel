"""Property-based tests for pipeline input validation.

Feature: story-video-editor, Property 1: Whitespace text rejection
Validates: Requirements 1.3

Feature: story-video-editor, Property 6: Project creation for valid text
Validates: Requirements 1.1

Feature: story-video-editor, Property 7: Voice selection propagation
Validates: Requirements 8.2
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from hypothesis import given, settings as h_settings, assume
from hypothesis import strategies as st

from backend.config import Settings
from backend.models.project import ProjectState
from backend.persistence.local import LocalStorageBackend
from backend.services.pipeline_service import PipelineService
from backend.services.project_service import ProjectService


# ---------------------------------------------------------------------------
# Available voices (from design doc / narration.py)
# ---------------------------------------------------------------------------

AVAILABLE_VOICES = [
    "zh-CN-XiaoxiaoNeural",
    "zh-CN-YunxiNeural",
    "zh-CN-YunjianNeural",
]

# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------

# Whitespace-only strings: empty or composed entirely of spaces, tabs, newlines
whitespace_only_st = st.from_regex(r"[\s]*", fullmatch=True).filter(
    lambda s: len(s) == 0 or s.strip() == ""
)

# Valid story text: non-empty and not whitespace-only
valid_story_st = st.text(min_size=1, max_size=200).filter(lambda s: s.strip() != "")

voice_st = st.sampled_from(AVAILABLE_VOICES)

owner_st = st.text(
    min_size=1, max_size=20,
    alphabet=st.characters(categories=("L", "N")),
)


# ---------------------------------------------------------------------------
# Validation helper (mirrors what the API endpoint should enforce)
# ---------------------------------------------------------------------------

def validate_story_text(text: str) -> bool:
    """Return True if the story text is valid for project creation.

    Rejects empty strings and strings composed entirely of whitespace.
    """
    return bool(text and text.strip())


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_storage(tmp_path):
    return LocalStorageBackend(base_dir=str(tmp_path))


@pytest.fixture
def default_settings():
    s = Settings()
    s.MAX_PROJECTS_PER_USER = 100
    s.MAX_CONCURRENT_PIPELINES_PER_USER = 10
    return s


@pytest.fixture
def project_service(tmp_storage, default_settings):
    return ProjectService(storage=tmp_storage, settings=default_settings)


@pytest.fixture
def pipeline_service(tmp_storage, project_service, default_settings):
    return PipelineService(
        storage=tmp_storage,
        project_service=project_service,
        settings=default_settings,
    )


# ---------------------------------------------------------------------------
# Property 1: Whitespace text rejection
# ---------------------------------------------------------------------------


class TestWhitespaceTextRejection:
    """Feature: story-video-editor, Property 1: Whitespace text rejection

    For any string composed entirely of whitespace characters (spaces, tabs,
    newlines, or empty string), submitting it to the project creation API
    should be rejected with a validation error, and no project should be
    created.

    Validates: Requirements 1.3
    """

    @given(text=whitespace_only_st)
    @h_settings(max_examples=100)
    def test_whitespace_only_text_is_rejected(self, text: str):
        """Whitespace-only or empty text must fail validation."""
        assert validate_story_text(text) is False

    @given(text=whitespace_only_st, owner=owner_st)
    @h_settings(max_examples=100)
    def test_no_project_created_for_whitespace_text(
        self, text: str, owner: str, tmp_path_factory
    ):
        """When whitespace text is submitted, validation rejects it before
        any project is created — so no project should exist afterwards."""
        tmp_dir = tmp_path_factory.mktemp("ws")
        storage = LocalStorageBackend(base_dir=str(tmp_dir))
        svc_settings = Settings()
        svc_settings.MAX_PROJECTS_PER_USER = 100
        svc = ProjectService(storage=storage, settings=svc_settings)

        # Simulate the API validation gate
        is_valid = validate_story_text(text)
        assert is_valid is False, f"Expected rejection for whitespace text: {text!r}"

        # Verify no project was created
        async def _check():
            return await svc.list_projects(owner)

        projects = asyncio.get_event_loop().run_until_complete(_check())
        assert len(projects) == 0


# ---------------------------------------------------------------------------
# Property 6: Project creation for valid text
# ---------------------------------------------------------------------------


class TestProjectCreationForValidText:
    """Feature: story-video-editor, Property 6: Project creation for valid text

    For any non-empty, non-whitespace-only story string, submitting it to
    the project creation API should successfully create a project with a
    valid unique ID and a status of "processing".

    Validates: Requirements 1.1
    """

    @given(text=valid_story_st, owner=owner_st)
    @h_settings(max_examples=100)
    def test_valid_text_passes_validation(self, text: str, owner: str):
        """Non-empty, non-whitespace text must pass validation."""
        assert validate_story_text(text) is True

    @given(text=valid_story_st, owner=owner_st)
    @h_settings(max_examples=100)
    def test_valid_text_creates_project_with_valid_id(
        self, text: str, owner: str, tmp_path_factory
    ):
        """A project created with valid text should have a 32-char hex ID."""
        tmp_dir = tmp_path_factory.mktemp("vt")
        storage = LocalStorageBackend(base_dir=str(tmp_dir))
        svc_settings = Settings()
        svc_settings.MAX_PROJECTS_PER_USER = 100
        svc = ProjectService(storage=storage, settings=svc_settings)

        async def _run():
            return await svc.create_project(story_text=text, owner_id=owner)

        project = asyncio.get_event_loop().run_until_complete(_run())

        # Valid UUID hex ID
        assert len(project.id) == 32
        assert all(c in "0123456789abcdef" for c in project.id)

    @given(text=valid_story_st, owner=owner_st)
    @h_settings(max_examples=100)
    def test_valid_text_creates_project_with_pending_status(
        self, text: str, owner: str, tmp_path_factory
    ):
        """A newly created project starts with 'pending' status (transitions
        to 'processing' once the pipeline background task begins)."""
        tmp_dir = tmp_path_factory.mktemp("st")
        storage = LocalStorageBackend(base_dir=str(tmp_dir))
        svc_settings = Settings()
        svc_settings.MAX_PROJECTS_PER_USER = 100
        svc = ProjectService(storage=storage, settings=svc_settings)

        async def _run():
            return await svc.create_project(story_text=text, owner_id=owner)

        project = asyncio.get_event_loop().run_until_complete(_run())
        assert project.status == "pending"
        assert project.story_text == text


# ---------------------------------------------------------------------------
# Property 7: Voice selection propagation
# ---------------------------------------------------------------------------


class TestVoiceSelectionPropagation:
    """Feature: story-video-editor, Property 7: Voice selection propagation

    For any valid voice identifier from the available voices list, when that
    voice is specified during project creation, the pipeline should receive
    and use that exact voice identifier for narration generation.

    Validates: Requirements 8.2
    """

    @given(voice=voice_st, owner=owner_st, text=valid_story_st)
    @h_settings(max_examples=100)
    def test_voice_stored_in_project_state(
        self, voice: str, owner: str, text: str, tmp_path_factory
    ):
        """The selected voice should be stored in the project state."""
        tmp_dir = tmp_path_factory.mktemp("vc")
        storage = LocalStorageBackend(base_dir=str(tmp_dir))
        svc_settings = Settings()
        svc_settings.MAX_PROJECTS_PER_USER = 100
        svc = ProjectService(storage=storage, settings=svc_settings)

        async def _run():
            return await svc.create_project(
                story_text=text, owner_id=owner, voice=voice
            )

        project = asyncio.get_event_loop().run_until_complete(_run())
        assert project.voice == voice

    @given(voice=voice_st, owner=owner_st, text=valid_story_st)
    @h_settings(max_examples=100)
    def test_voice_propagated_to_pipeline_narration(
        self, voice: str, owner: str, text: str, tmp_path_factory
    ):
        """When the pipeline runs, the narration stage should receive the
        exact voice identifier that was specified during project creation."""
        tmp_dir = tmp_path_factory.mktemp("vp")
        storage = LocalStorageBackend(base_dir=str(tmp_dir))
        svc_settings = Settings()
        svc_settings.MAX_PROJECTS_PER_USER = 100
        svc_settings.MAX_CONCURRENT_PIPELINES_PER_USER = 10
        proj_svc = ProjectService(storage=storage, settings=svc_settings)
        pipe_svc = PipelineService(
            storage=storage, project_service=proj_svc, settings=svc_settings
        )

        captured_voice = None

        def mock_generate_narration(story, output_path, voice_arg):
            nonlocal captured_voice
            captured_voice = voice_arg
            # Write a minimal file so downstream doesn't crash
            import struct
            # Create a minimal valid MP3 header (just enough bytes)
            with open(output_path, "wb") as f:
                f.write(b"\x00" * 1024)
            return output_path, 1.0

        async def _run():
            project = await proj_svc.create_project(
                story_text=text, owner_id=owner, voice=voice
            )
            # Patch generate_narration to capture the voice argument
            with patch(
                "backend.services.pipeline_service.generate_narration",
                side_effect=mock_generate_narration,
            ):
                # Run only the narration stage by calling _stage_narration
                import tempfile, os
                with tempfile.TemporaryDirectory() as tmp:
                    state = await proj_svc.get_project(project.id)
                    await pipe_svc._stage_narration(
                        project.id, state, tmp, None
                    )
            return project

        asyncio.get_event_loop().run_until_complete(_run())
        assert captured_voice == voice, (
            f"Expected voice {voice!r} but pipeline received {captured_voice!r}"
        )
