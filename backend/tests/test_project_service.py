"""Property-based tests for ProjectService.

Feature: story-video-editor, Property 4: Project state save/load round trip
Validates: Requirements 3.3, 4.3, 5.1, 10.1, 10.2

Feature: story-video-editor, Property 8: Project ID uniqueness
Validates: Requirements 10.3
"""

import asyncio
import tempfile

import pytest
from hypothesis import given, settings as h_settings, assume
from hypothesis import strategies as st

from backend.config import Settings
from backend.models.project import PipelineProgress, ProjectState
from backend.models.subtitle import Position, SubtitleSegment, SubtitleStyle
from backend.persistence.local import LocalStorageBackend
from backend.services.project_service import ProjectService


# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------

norm_floats = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
positive_floats = st.floats(min_value=0.001, max_value=1e4, allow_nan=False, allow_infinity=False)
hex_colors = st.from_regex(r"#[0-9A-Fa-f]{6}", fullmatch=True)

position_st = st.builds(Position, x=norm_floats, y=norm_floats)

style_st = st.builds(
    SubtitleStyle,
    font_size=st.floats(min_value=0.01, max_value=0.2, allow_nan=False, allow_infinity=False),
    font_color=hex_colors,
    outline_color=hex_colors,
    font_family=st.sampled_from(["Noto Sans CJK SC", "PingFang", "Arial"]),
)



def _subtitle_segment_st():
    """Strategy for a valid SubtitleSegment (start_time < end_time guaranteed)."""
    return st.builds(
        _make_segment,
        seg_id=st.uuids().map(lambda u: u.hex),
        text=st.text(
            alphabet=st.characters(categories=("L", "N", "P")),
            min_size=1,
            max_size=20,
        ),
        start=positive_floats,
        duration=st.floats(min_value=0.1, max_value=100.0, allow_nan=False, allow_infinity=False),
        position=position_st,
        style=style_st,
    )


def _make_segment(
    seg_id: str,
    text: str,
    start: float,
    duration: float,
    position: Position,
    style: SubtitleStyle,
) -> SubtitleSegment:
    return SubtitleSegment(
        id=seg_id,
        text=text,
        start_time=start,
        end_time=start + duration,
        position=position,
        style=style,
    )


pipeline_progress_st = st.builds(
    PipelineProgress,
    stage=st.sampled_from(["narration", "subtitles", "assembly", "complete", "error"]),
    message=st.text(min_size=1, max_size=50),
)

status_st = st.sampled_from(["pending", "processing", "ready", "exporting", "exported", "error"])

project_state_st = st.builds(
    ProjectState,
    id=st.uuids().map(lambda u: u.hex),
    owner_id=st.text(min_size=1, max_size=20, alphabet=st.characters(categories=("L", "N"))),
    title=st.text(min_size=1, max_size=50),
    story_text=st.text(min_size=1, max_size=200),
    voice=st.sampled_from(["zh-CN-XiaoxiaoNeural", "zh-CN-YunxiNeural", "zh-CN-YunjianNeural"]),
    status=status_st,
    version=st.integers(min_value=1, max_value=1000),
    pipeline_progress=pipeline_progress_st,
    subtitles=st.lists(_subtitle_segment_st(), min_size=0, max_size=5),
    background_image=st.one_of(st.none(), st.just("/images/bg.png")),
    video_url=st.one_of(st.none(), st.just("/media/preview.mp4")),
    audio_url=st.one_of(st.none(), st.just("/media/narration.mp3")),
    audio_duration=st.one_of(st.none(), st.floats(min_value=1.0, max_value=600.0, allow_nan=False, allow_infinity=False)),
    export_url=st.one_of(st.none(), st.just("/media/export.mp4")),
    created_at=st.just("2025-01-01T00:00:00+00:00"),
    updated_at=st.just("2025-01-01T00:00:00+00:00"),
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_storage(tmp_path):
    """Create a LocalStorageBackend backed by a temporary directory."""
    return LocalStorageBackend(base_dir=str(tmp_path))


@pytest.fixture
def default_settings():
    s = Settings()
    s.MAX_PROJECTS_PER_USER = 100
    return s


@pytest.fixture
def service(tmp_storage, default_settings):
    return ProjectService(storage=tmp_storage, settings=default_settings)



# ---------------------------------------------------------------------------
# Property 4: Project state save/load round trip
# ---------------------------------------------------------------------------


class TestProjectStateSaveLoadRoundTrip:
    """Property 4: Project state save/load round trip.

    For any valid project state containing arbitrary subtitle positions,
    styles, and timings, saving the state to the backend and then loading
    it back should produce an equivalent project state.
    """

    @given(state=project_state_st)
    @h_settings(max_examples=200)
    def test_round_trip_preserves_state(self, state: ProjectState, tmp_path_factory):
        """Save then load should return an identical ProjectState."""
        tmp_dir = tmp_path_factory.mktemp("rt")
        storage = LocalStorageBackend(base_dir=str(tmp_dir))
        svc_settings = Settings()
        svc_settings.MAX_PROJECTS_PER_USER = 100
        svc = ProjectService(storage=storage, settings=svc_settings)

        async def _run():
            await svc._save_state(state)
            loaded = await svc._load_state(state.id)
            return loaded

        loaded = asyncio.get_event_loop().run_until_complete(_run())

        # Core identity
        assert loaded.id == state.id
        assert loaded.owner_id == state.owner_id
        assert loaded.title == state.title
        assert loaded.story_text == state.story_text
        assert loaded.voice == state.voice
        assert loaded.status == state.status
        assert loaded.version == state.version

        # Pipeline progress
        assert loaded.pipeline_progress.stage == state.pipeline_progress.stage
        assert loaded.pipeline_progress.message == state.pipeline_progress.message

        # Media references
        assert loaded.background_image == state.background_image
        assert loaded.video_url == state.video_url
        assert loaded.audio_url == state.audio_url
        assert loaded.audio_duration == state.audio_duration
        assert loaded.export_url == state.export_url

        # Timestamps
        assert loaded.created_at == state.created_at
        assert loaded.updated_at == state.updated_at

        # Subtitles — full structural equality
        assert len(loaded.subtitles) == len(state.subtitles)
        for orig, rt in zip(state.subtitles, loaded.subtitles):
            assert rt.id == orig.id
            assert rt.text == orig.text
            assert rt.start_time == orig.start_time
            assert rt.end_time == orig.end_time
            assert rt.position.x == orig.position.x
            assert rt.position.y == orig.position.y
            assert rt.style.font_size == orig.style.font_size
            assert rt.style.font_color == orig.style.font_color
            assert rt.style.outline_color == orig.style.outline_color
            assert rt.style.font_family == orig.style.font_family

    @given(state=project_state_st)
    @h_settings(max_examples=200)
    def test_round_trip_model_equality(self, state: ProjectState, tmp_path_factory):
        """The loaded model should be equal to the original via model_dump."""
        tmp_dir = tmp_path_factory.mktemp("eq")
        storage = LocalStorageBackend(base_dir=str(tmp_dir))
        svc_settings = Settings()
        svc_settings.MAX_PROJECTS_PER_USER = 100
        svc = ProjectService(storage=storage, settings=svc_settings)

        async def _run():
            await svc._save_state(state)
            return await svc._load_state(state.id)

        loaded = asyncio.get_event_loop().run_until_complete(_run())
        assert loaded.model_dump() == state.model_dump()



# ---------------------------------------------------------------------------
# Property 8: Project ID uniqueness
# ---------------------------------------------------------------------------


class TestProjectIDUniqueness:
    """Property 8: Project ID uniqueness.

    For any number of sequentially created projects, all assigned project
    IDs should be distinct.
    """

    @given(
        count=st.integers(min_value=2, max_value=20),
        owner=st.text(min_size=1, max_size=10, alphabet=st.characters(categories=("L",))),
        story=st.text(min_size=1, max_size=100),
    )
    @h_settings(max_examples=200)
    def test_sequential_project_ids_are_unique(
        self, count: int, owner: str, story: str, tmp_path_factory
    ):
        """Creating N projects sequentially must yield N distinct IDs."""
        tmp_dir = tmp_path_factory.mktemp("uniq")
        storage = LocalStorageBackend(base_dir=str(tmp_dir))
        svc_settings = Settings()
        svc_settings.MAX_PROJECTS_PER_USER = 100
        svc = ProjectService(storage=storage, settings=svc_settings)

        async def _run():
            ids = []
            for _ in range(count):
                project = await svc.create_project(story_text=story, owner_id=owner)
                ids.append(project.id)
            return ids

        ids = asyncio.get_event_loop().run_until_complete(_run())
        assert len(set(ids)) == len(ids), f"Duplicate IDs found: {ids}"

    @given(
        owner=st.text(min_size=1, max_size=10, alphabet=st.characters(categories=("L",))),
    )
    @h_settings(max_examples=100)
    def test_created_project_has_valid_uuid_hex(self, owner: str, tmp_path_factory):
        """Each project ID should be a valid 32-char hex string (UUID without dashes)."""
        tmp_dir = tmp_path_factory.mktemp("fmt")
        storage = LocalStorageBackend(base_dir=str(tmp_dir))
        svc_settings = Settings()
        svc_settings.MAX_PROJECTS_PER_USER = 100
        svc = ProjectService(storage=storage, settings=svc_settings)

        async def _run():
            return await svc.create_project(story_text="测试故事", owner_id=owner)

        project = asyncio.get_event_loop().run_until_complete(_run())
        assert len(project.id) == 32
        assert all(c in "0123456789abcdef" for c in project.id)
