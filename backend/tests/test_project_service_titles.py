"""Property-based tests for ``ProjectService`` title behaviors.

Feature: project-titles, Properties 6-11

Each property targets one or more acceptance criteria from
``.kiro/specs/project-titles/requirements.md`` and is annotated with the
requirement clauses it validates. Tests use a fresh ``LocalStorageBackend``
per Hypothesis example via ``tmp_path_factory.mktemp`` so state from one
example does not leak into another.

Async service calls are driven from sync property tests via
``asyncio.get_event_loop().run_until_complete``, matching the existing
pattern in ``test_project_service.py``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from hypothesis import HealthCheck, assume, given
from hypothesis import settings as h_settings
from hypothesis import strategies as st

from backend.config import Settings
from backend.models.project import PipelineProgress, ProjectState
from backend.persistence.local import LocalStorageBackend
from backend.services import title_validator
from backend.services.project_service import ProjectService
from backend.services.title_validator import (
    MAX_TITLE_LENGTH,
    TitleErrorCode,
    TitleValidationError,
)


# ---------------------------------------------------------------------------
# Strategies and helpers
# ---------------------------------------------------------------------------

# Restricted alphabet so that ``str.upper()``, ``str.lower()`` and
# ``str.swapcase()`` are length-preserving and yield strings that share a
# casefold-after-strip key with the original. ASCII letters / digits / a few
# punctuation chars / internal space cover the cases we care about (Latin,
# digits, mixed) without dragging in Greek-sigma or German-eszett edge cases.
_SAFE_TITLE_CHARS = (
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789"
    "-_. "
)


def _valid_title_st(min_size: int = 1, max_size: int = 80) -> st.SearchStrategy[str]:
    """Strategy producing strings that pass ``validate_shape``.

    Constrains to a safe ASCII alphabet so that case-flip transformations
    used in Properties 6 and 7 are well-defined and key-preserving.
    """

    return st.text(
        alphabet=_SAFE_TITLE_CHARS, min_size=min_size, max_size=max_size
    ).filter(lambda s: 1 <= len(s.strip()) <= MAX_TITLE_LENGTH)


# Whitespace padding used when building "key-equivalent variant" candidates.
_PAD_CHARS = " \t"


def _case_flip_per_char(s: str, choices: list[int]) -> str:
    """Per-character case flip using a sequence of ``choices`` in [0, 3].

    For each char of ``s`` the choice picks ``identity`` / ``upper`` /
    ``lower`` / ``swapcase``. With the safe alphabet these all preserve
    length and casefold-equivalence with the original char.
    """

    if not choices:
        return s
    out: list[str] = []
    for i, ch in enumerate(s):
        choice = choices[i % len(choices)] % 4
        if choice == 0:
            out.append(ch)
        elif choice == 1:
            out.append(ch.upper())
        elif choice == 2:
            out.append(ch.lower())
        else:
            out.append(ch.swapcase())
    return "".join(out)


def _make_service(tmp_dir: Path) -> ProjectService:
    """Build a ``ProjectService`` backed by a per-example temp directory."""

    storage = LocalStorageBackend(base_dir=str(tmp_dir))
    settings = Settings()
    settings.MAX_PROJECTS_PER_USER = 100
    return ProjectService(storage=storage, settings=settings)


def _run(coro):
    """Run an async coroutine from a sync property test."""

    return asyncio.get_event_loop().run_until_complete(coro)


def _read_all_state_bytes(base_dir: Path) -> dict[str, bytes]:
    """Snapshot the raw bytes of every project's ``state.json`` under ``base_dir``."""

    out: dict[str, bytes] = {}
    projects_dir = base_dir / "projects"
    if not projects_dir.exists():
        return out
    for entry in projects_dir.iterdir():
        state = entry / "state.json"
        if state.exists():
            out[entry.name] = state.read_bytes()
    return out


def _write_state_directly(
    base_dir: Path,
    *,
    project_id: str,
    owner_id: str,
    title: str,
    pipeline_message: str = "initial",
) -> bytes:
    """Bypass the service and write a ``state.json`` with an arbitrary title.

    Used to seed Property 11 with legacy stored titles that may not pass
    ``validate_shape``. Returns the bytes written so callers can assert
    byte-for-byte preservation later.
    """

    state = ProjectState(
        id=project_id,
        owner_id=owner_id,
        title=title,
        story_text="legacy story",
        pipeline_progress=PipelineProgress(stage="narration", message=pipeline_message),
        created_at="2025-01-01T00:00:00+00:00",
        updated_at="2025-01-01T00:00:00+00:00",
    )
    proj_dir = base_dir / "projects" / project_id
    proj_dir.mkdir(parents=True, exist_ok=True)
    data = state.model_dump_json(indent=2).encode()
    (proj_dir / "state.json").write_bytes(data)
    return data


# ---------------------------------------------------------------------------
# Property 6: Duplicate titles under the same owner are rejected without
# side effects.
# ---------------------------------------------------------------------------
# Feature: project-titles, Property 6: Duplicate titles under the same owner
# are rejected without side effects.
# Validates: Requirements 1.4, 2.3, 3.2, 3.3, 5.5
#
# The validator-layer ``check_uniqueness`` raises ``TitleValidationError``
# with code ``DUPLICATE`` (not the ``TitleConflictError`` alias). We assert
# that exact error type, matching the actual service-layer behavior.


@given(
    titles=st.lists(
        _valid_title_st(),
        min_size=1,
        max_size=4,
        unique_by=lambda s: s.strip().casefold(),
    ),
    target_idx=st.integers(min_value=0, max_value=10),
    case_choices=st.lists(
        st.integers(min_value=0, max_value=3), min_size=0, max_size=80
    ),
    pad_left=st.text(alphabet=_PAD_CHARS, min_size=0, max_size=4),
    pad_right=st.text(alphabet=_PAD_CHARS, min_size=0, max_size=4),
)
@h_settings(
    max_examples=200,
    suppress_health_check=[
        HealthCheck.function_scoped_fixture,
        HealthCheck.filter_too_much,
    ],
)
def test_duplicate_create_rejected_no_side_effects(
    titles: list[str],
    target_idx: int,
    case_choices: list[int],
    pad_left: str,
    pad_right: str,
    tmp_path_factory,
) -> None:
    """create_project with a duplicate-by-key title raises and changes nothing."""

    tmp_dir = Path(str(tmp_path_factory.mktemp("p6_create")))
    service = _make_service(tmp_dir)
    owner_id = "owner-p6"

    # Pre-seed projects with the generated titles (all key-distinct by the
    # ``unique_by`` filter on the strategy).
    project_ids: list[str] = []
    for t in titles:
        proj = _run(
            service.create_project(story_text="story", owner_id=owner_id, title=t)
        )
        project_ids.append(proj.id)

    # Pick one of the seeded titles as the duplication target.
    idx = target_idx % len(titles)
    target_title = titles[idx]

    # Build the candidate as a key-equivalent variant: case-flipped + padded.
    flipped = _case_flip_per_char(target_title.strip(), case_choices)
    candidate = pad_left + flipped + pad_right

    # The candidate must clear shape validation so the duplicate branch is
    # the one that fires; key must match the stored target.
    try:
        title_validator.validate_shape(candidate)
    except TitleValidationError:
        assume(False)
    assume(
        title_validator.title_key(candidate.strip())
        == title_validator.title_key(target_title.strip())
    )

    # Snapshot every state file before the failing call.
    before = _read_all_state_bytes(tmp_dir)

    with pytest.raises(TitleValidationError) as exc_info:
        _run(
            service.create_project(
                story_text="another story", owner_id=owner_id, title=candidate
            )
        )
    assert exc_info.value.code == TitleErrorCode.DUPLICATE

    # No side effects: every existing state file is byte-for-byte identical.
    after = _read_all_state_bytes(tmp_dir)
    assert after == before


# Feature: project-titles, Property 6 (rename path)
# Validates: Requirements 2.3, 3.3, 5.5
@given(
    titles=st.lists(
        _valid_title_st(),
        min_size=2,
        max_size=4,
        unique_by=lambda s: s.strip().casefold(),
    ),
    rename_idx=st.integers(min_value=0, max_value=10),
    target_idx=st.integers(min_value=0, max_value=10),
    case_choices=st.lists(
        st.integers(min_value=0, max_value=3), min_size=0, max_size=80
    ),
    pad_left=st.text(alphabet=_PAD_CHARS, min_size=0, max_size=4),
    pad_right=st.text(alphabet=_PAD_CHARS, min_size=0, max_size=4),
)
@h_settings(
    max_examples=200,
    suppress_health_check=[
        HealthCheck.function_scoped_fixture,
        HealthCheck.filter_too_much,
    ],
)
def test_duplicate_rename_rejected_no_side_effects(
    titles: list[str],
    rename_idx: int,
    target_idx: int,
    case_choices: list[int],
    pad_left: str,
    pad_right: str,
    tmp_path_factory,
) -> None:
    """rename_title to a duplicate-by-key title raises and changes nothing."""

    tmp_dir = Path(str(tmp_path_factory.mktemp("p6_rename")))
    service = _make_service(tmp_dir)
    owner_id = "owner-p6r"

    # Pre-seed the projects.
    project_ids: list[str] = []
    versions: dict[str, int] = {}
    for t in titles:
        proj = _run(
            service.create_project(story_text="story", owner_id=owner_id, title=t)
        )
        project_ids.append(proj.id)
        versions[proj.id] = proj.version

    # Pick the project to rename and a *different* project's title to collide with.
    rename_pos = rename_idx % len(titles)
    target_pos = target_idx % len(titles)
    if target_pos == rename_pos:
        target_pos = (target_pos + 1) % len(titles)

    rename_id = project_ids[rename_pos]
    target_title = titles[target_pos]

    # Build the candidate.
    flipped = _case_flip_per_char(target_title.strip(), case_choices)
    candidate = pad_left + flipped + pad_right

    try:
        title_validator.validate_shape(candidate)
    except TitleValidationError:
        assume(False)
    assume(
        title_validator.title_key(candidate.strip())
        == title_validator.title_key(target_title.strip())
    )

    before = _read_all_state_bytes(tmp_dir)

    with pytest.raises(TitleValidationError) as exc_info:
        _run(
            service.rename_title(
                project_id=rename_id,
                owner_id=owner_id,
                candidate=candidate,
                expected_version=versions[rename_id],
            )
        )
    assert exc_info.value.code == TitleErrorCode.DUPLICATE

    after = _read_all_state_bytes(tmp_dir)
    assert after == before


# ---------------------------------------------------------------------------
# Property 7: Renaming a project to a key-equivalent variant of its own
# current title succeeds.
# ---------------------------------------------------------------------------
# Feature: project-titles, Property 7: A self-rename to a string with the
# same trim-and-casefold key as the stored title succeeds; the post-call
# stored ``title_key`` equals the pre-call key.
# Validates: Requirements 3.4


@given(
    base_title=_valid_title_st(),
    case_choices=st.lists(
        st.integers(min_value=0, max_value=3), min_size=0, max_size=80
    ),
    pad_left=st.text(alphabet=_PAD_CHARS, min_size=0, max_size=4),
    pad_right=st.text(alphabet=_PAD_CHARS, min_size=0, max_size=4),
)
@h_settings(
    max_examples=200,
    suppress_health_check=[
        HealthCheck.function_scoped_fixture,
        HealthCheck.filter_too_much,
    ],
)
def test_self_rename_to_key_equivalent_succeeds(
    base_title: str,
    case_choices: list[int],
    pad_left: str,
    pad_right: str,
    tmp_path_factory,
) -> None:
    tmp_dir = Path(str(tmp_path_factory.mktemp("p7")))
    service = _make_service(tmp_dir)
    owner_id = "owner-p7"

    project = _run(
        service.create_project(
            story_text="story", owner_id=owner_id, title=base_title
        )
    )
    pre_key = title_validator.title_key(
        title_validator.normalize(project.title)
    )

    # Build a key-equivalent candidate from the *stored* (already trimmed)
    # title, then add whitespace padding around it.
    flipped = _case_flip_per_char(project.title, case_choices)
    candidate = pad_left + flipped + pad_right

    # Sanity: the candidate must pass shape validation and share the key.
    try:
        title_validator.validate_shape(candidate)
    except TitleValidationError:
        assume(False)
    assume(title_validator.title_key(candidate.strip()) == pre_key)

    renamed = _run(
        service.rename_title(
            project_id=project.id,
            owner_id=owner_id,
            candidate=candidate,
            expected_version=project.version,
        )
    )

    post_key = title_validator.title_key(
        title_validator.normalize(renamed.title)
    )
    assert post_key == pre_key
    # And the trimmed candidate is what gets stored.
    assert renamed.title == candidate.strip()


# ---------------------------------------------------------------------------
# Property 8: Uniqueness is scoped per owner.
# ---------------------------------------------------------------------------
# Feature: project-titles, Property 8: Two distinct owners can each
# successfully create or rename to the same title without raising,
# regardless of order.
# Validates: Requirements 3.5


@given(
    title=_valid_title_st(),
    op_a=st.sampled_from(["create", "rename"]),
    op_b=st.sampled_from(["create", "rename"]),
    a_first=st.booleans(),
)
@h_settings(
    max_examples=200,
    suppress_health_check=[
        HealthCheck.function_scoped_fixture,
        HealthCheck.filter_too_much,
    ],
)
def test_uniqueness_scoped_per_owner(
    title: str, op_a: str, op_b: str, a_first: bool, tmp_path_factory
) -> None:
    tmp_dir = Path(str(tmp_path_factory.mktemp("p8")))
    service = _make_service(tmp_dir)
    owner_a = "owner-p8-A"
    owner_b = "owner-p8-B"

    # Seed each owner with a placeholder project so the rename branches are
    # exercisable. The seed titles are key-distinct from the candidate.
    seed_a = _run(
        service.create_project(
            story_text="seed", owner_id=owner_a, title="p8-seed-AAA"
        )
    )
    seed_b = _run(
        service.create_project(
            story_text="seed", owner_id=owner_b, title="p8-seed-BBB"
        )
    )

    candidate_key = title_validator.title_key(title.strip())
    seed_keys = {
        title_validator.title_key("p8-seed-AAA"),
        title_validator.title_key("p8-seed-BBB"),
    }
    assume(candidate_key not in seed_keys)

    def _do(owner_id: str, op: str, seed_state: ProjectState):
        if op == "create":
            return _run(
                service.create_project(
                    story_text="story", owner_id=owner_id, title=title
                )
            )
        return _run(
            service.rename_title(
                project_id=seed_state.id,
                owner_id=owner_id,
                candidate=title,
                expected_version=seed_state.version,
            )
        )

    # Both orderings are valid; neither should raise.
    if a_first:
        result_a = _do(owner_a, op_a, seed_a)
        result_b = _do(owner_b, op_b, seed_b)
    else:
        result_b = _do(owner_b, op_b, seed_b)
        result_a = _do(owner_a, op_a, seed_a)

    assert result_a.title == title.strip()
    assert result_b.title == title.strip()
    # Each owner sees only their own project under that title.
    assert result_a.owner_id == owner_a
    assert result_b.owner_id == owner_b


# ---------------------------------------------------------------------------
# Property 9: Title persistence round-trip across create / rename / list / get.
# ---------------------------------------------------------------------------
# Feature: project-titles, Property 9: After every successful create or
# rename, every subsequent ``get_project`` and ``list_projects`` (until the
# next rename) returns ``title == candidate.strip()``.
# Validates: Requirements 1.1, 2.2, 6.1, 6.2


def _verify_titles(
    service: ProjectService, owner_id: str, expected: dict[str, str]
) -> None:
    """Assert get_project and list_projects agree with ``expected``."""

    for pid, t in expected.items():
        got = _run(service.get_project(pid))
        assert got.title == t, (
            f"get_project({pid}) returned {got.title!r}, expected {t!r}"
        )
    listed = _run(service.list_projects(owner_id))
    listed_titles = {s["id"]: s["title"] for s in listed}
    assert listed_titles == expected


@given(
    n=st.integers(min_value=1, max_value=4),
    rename_ops=st.lists(
        st.tuples(
            st.integers(min_value=0, max_value=10),
            st.integers(min_value=0, max_value=20),
        ),
        min_size=0,
        max_size=8,
    ),
)
@h_settings(
    max_examples=100,
    suppress_health_check=[
        HealthCheck.function_scoped_fixture,
        HealthCheck.filter_too_much,
    ],
)
def test_title_persistence_round_trip(
    n: int, rename_ops: list[tuple[int, int]], tmp_path_factory
) -> None:
    tmp_dir = Path(str(tmp_path_factory.mktemp("p9")))
    service = _make_service(tmp_dir)
    owner_id = "owner-p9"

    # Each pool entry is key-distinct: distinct by suffix and case-fold-equal
    # to its own lowercase form. We never reuse a pool index, so once a key
    # is consumed it is never re-introduced even after a rename.
    title_pool = [f"p9-title-{i}" for i in range(60)]

    expected: dict[str, str] = {}  # project_id -> stored title
    versions: dict[str, int] = {}
    project_order: list[str] = []
    next_idx = 0

    # Initial creates.
    for _ in range(n):
        t = title_pool[next_idx]
        next_idx += 1
        proj = _run(
            service.create_project(story_text="story", owner_id=owner_id, title=t)
        )
        # Strategy never produces leading/trailing whitespace, so trim is a no-op.
        assert proj.title == t.strip()
        expected[proj.id] = t.strip()
        versions[proj.id] = proj.version
        project_order.append(proj.id)
        _verify_titles(service, owner_id, expected)

    # Renames - each consumes a fresh title-pool entry to guarantee uniqueness.
    for proj_pos, _ in rename_ops:
        if not project_order or next_idx >= len(title_pool):
            break
        pid = project_order[proj_pos % len(project_order)]
        new_t = title_pool[next_idx]
        next_idx += 1
        renamed = _run(
            service.rename_title(
                project_id=pid,
                owner_id=owner_id,
                candidate=new_t,
                expected_version=versions[pid],
            )
        )
        assert renamed.title == new_t.strip()
        expected[pid] = new_t.strip()
        versions[pid] = renamed.version
        _verify_titles(service, owner_id, expected)


# ---------------------------------------------------------------------------
# Property 10: Rename advances version and timestamp.
# ---------------------------------------------------------------------------
# Feature: project-titles, Property 10: After a successful ``rename_title``,
# ``version`` increments by 1 and ``updated_at`` is non-decreasing under
# ISO-8601 UTC string comparison.
# Validates: Requirements 6.3, 6.4


@given(
    initial_title=_valid_title_st(),
    new_title=_valid_title_st(),
)
@h_settings(
    max_examples=200,
    suppress_health_check=[
        HealthCheck.function_scoped_fixture,
        HealthCheck.filter_too_much,
    ],
)
def test_rename_advances_version_and_timestamp(
    initial_title: str, new_title: str, tmp_path_factory
) -> None:
    tmp_dir = Path(str(tmp_path_factory.mktemp("p10")))
    service = _make_service(tmp_dir)
    owner_id = "owner-p10"

    project = _run(
        service.create_project(
            story_text="story", owner_id=owner_id, title=initial_title
        )
    )
    pre_version = project.version
    pre_updated_at = project.updated_at

    # Self is excluded from the uniqueness check; any shape-valid candidate
    # is accepted regardless of whether it shares a key with the existing
    # title (no other siblings exist).
    renamed = _run(
        service.rename_title(
            project_id=project.id,
            owner_id=owner_id,
            candidate=new_title,
            expected_version=pre_version,
        )
    )

    assert renamed.version == pre_version + 1
    # ISO-8601 UTC strings (with the same offset suffix) sort lexicographically.
    assert renamed.updated_at >= pre_updated_at


# ---------------------------------------------------------------------------
# Property 11: Pre-existing stored titles are preserved on read-write.
# ---------------------------------------------------------------------------
# Feature: project-titles, Property 11: Loading a state with an arbitrary
# stored title and re-persisting via a non-rename operation (a no-op
# ``update_project`` that keeps the title's casefold-after-trim key
# identical) leaves the stored title byte-for-byte unchanged.
# Validates: Requirements 5.1, 5.4
#
# The strategy intentionally allows stored titles that would now fail
# ``validate_shape`` (empty, over-length, control chars) and lets two
# projects under the same owner share a title key, exercising the legacy
# duplicate-by-key scenario described in Requirement 5.4.

_LEGACY_TITLE_CHARS = st.characters(
    # ``Cs`` (lone surrogates) cannot survive JSON round-trip; everything
    # else - including ``Cc`` and the empty string - is fair game.
    blacklist_categories=("Cs",),
)


@given(
    stored_title=st.text(alphabet=_LEGACY_TITLE_CHARS, min_size=0, max_size=150),
    extra_title=st.text(alphabet=_LEGACY_TITLE_CHARS, min_size=0, max_size=150),
    new_pipeline_message=st.text(min_size=1, max_size=40),
    duplicate_under_owner=st.booleans(),
)
@h_settings(
    max_examples=200,
    suppress_health_check=[
        HealthCheck.function_scoped_fixture,
        HealthCheck.filter_too_much,
    ],
)
def test_preexisting_stored_titles_preserved(
    stored_title: str,
    extra_title: str,
    new_pipeline_message: str,
    duplicate_under_owner: bool,
    tmp_path_factory,
) -> None:
    tmp_dir = Path(str(tmp_path_factory.mktemp("p11")))
    service = _make_service(tmp_dir)
    owner_id = "owner-p11"

    # Project 1: directly written with an arbitrary stored title.
    pid1 = "p11-project-1"
    _write_state_directly(
        tmp_dir,
        project_id=pid1,
        owner_id=owner_id,
        title=stored_title,
        pipeline_message="initial-1",
    )

    # Project 2: optionally a duplicate-by-key under the same owner. When
    # ``duplicate_under_owner`` is True the stored titles share a casefold
    # key (modulo trimming), exactly matching the legacy duplicate scenario.
    pid2 = "p11-project-2"
    second_title = stored_title if duplicate_under_owner else extra_title
    _write_state_directly(
        tmp_dir,
        project_id=pid2,
        owner_id=owner_id,
        title=second_title,
        pipeline_message="initial-2",
    )

    # get_project must return the title byte-for-byte.
    got1 = _run(service.get_project(pid1))
    assert got1.title == stored_title
    got2 = _run(service.get_project(pid2))
    assert got2.title == second_title

    # No-op update on project 1: change pipeline_progress.message but leave
    # the title field equal to the currently-stored title. ``update_project``
    # detects key equality and preserves the stored title byte-for-byte
    # without invoking ``validate_shape``.
    incoming = got1.model_copy(deep=True)
    incoming.pipeline_progress = PipelineProgress(
        stage=got1.pipeline_progress.stage,
        message=new_pipeline_message,
    )
    # Title is left at the loaded value (== stored_title).
    updated = _run(service.update_project(pid1, incoming))
    assert updated.title == stored_title

    # Re-load from disk to confirm persistence.
    reloaded1 = _run(service.get_project(pid1))
    assert reloaded1.title == stored_title

    # Project 2 was not touched; it is still byte-for-byte intact.
    reloaded2 = _run(service.get_project(pid2))
    assert reloaded2.title == second_title
