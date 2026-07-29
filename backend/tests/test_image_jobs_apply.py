"""Property-based test for image-job apply semantics.

Feature: ai-background-generation, Property 5: Successful job candidates and apply semantics
Validates: Requirements 3.5, 3.6, 4.4, 5.3

Hypothesis randomizes ``(image_count, target_kind, candidate_index,
subtitles_len, start_index, end_index)`` tuples across both target
kinds. For every drawn tuple the test:

1. Creates a fresh project on a per-example tmp-dir
   :class:`backend.persistence.local.LocalStorageBackend`. A real
   :class:`backend.services.project_service.ProjectService` is required
   here — the apply path goes through
   :meth:`ProjectService.update_project` so the optimistic-concurrency
   check on ``version`` runs in the canonical path. A bare in-memory
   stub (as used by the submit-shape and section-validation property
   tests) wouldn't exercise that contract.

2. Pre-populates the project's ``background_image`` and
   ``section_backgrounds`` so the "preserved" half of the property is
   meaningfully observable: a whole-video apply must leave a
   pre-existing ``SectionBackground`` entry untouched
   (Requirement 3.6's fallback rule); a section apply must leave the
   pre-existing ``background_image`` untouched (also Requirement 3.6).

3. Submits a job through :meth:`JobManager.submit` (HTTP isn't needed
   here — Property 9 already covers the HTTP submit shape; this test
   focuses on apply semantics, which the JobManager owns end-to-end)
   and awaits the worker task. With the deterministic
   :class:`FakeImageBackend` driving the worker, the job reaches
   ``succeeded`` with ``image_count`` candidates persisted under
   ``imgjobs/{job_id}/candidate-{cid}.png`` (Requirement 4.4).

4. Calls :meth:`JobManager.apply_candidate` for the selected
   ``candidate_index`` and asserts:

   * ``len(candidates) == image_count`` (Property 5.1).
   * ``whole_video`` apply: ``background_image`` is set to the chosen
     candidate's URL AND ``section_backgrounds`` is preserved
     unchanged (Property 5.2 / Requirements 3.5, 3.6).
   * ``section`` apply: a :class:`SectionBackground` keyed on
     ``(start_index, end_index)`` carrying the chosen candidate's URL
     is present, the pre-existing ``background_image`` is preserved
     unchanged, and any pre-existing :class:`SectionBackground` whose
     ``(start_index, end_index)`` differs from the applied one survives
     (Property 5.3 / Requirement 3.6).

The worker pipeline runs entirely against :class:`FakeImageBackend`,
which produces deterministic byte payloads — no real provider SDK is
imported (Requirement 8.3 is enforced separately by the static
import-check in Task 15.3, but we honor the same discipline here).

The Hypothesis profile uses ``max_examples=50`` (rather than the
``max_examples=100`` profile other property tests in this suite use)
because each example does on-disk I/O via the real
:class:`LocalStorageBackend` (project ``state.json`` write on create,
update, and apply — three writes per example minimum) plus candidate
file persistence. 50 examples still gives broad coverage of the
``(image_count, target_kind, candidate_index, indices)`` space the
property is about while keeping the test runtime reasonable.
"""

from __future__ import annotations

import asyncio
import tempfile

from hypothesis import given, settings as hyp_settings, strategies as st

from backend.config import Settings
from backend.models.image_jobs import GenerationTarget
from backend.models.project import SectionBackground
from backend.models.subtitle import Position, SubtitleSegment, SubtitleStyle
from backend.persistence.local import LocalStorageBackend
from backend.services.image_capability_state import CapabilityState
from backend.services.image_job_service import JobManager
from backend.services.project_service import ProjectService
from backend.tests._image_fakes import FakeImageBackend


_OWNER_ID = "owner-apply-test"
# Same default value as ``MAX_IMAGES_PER_JOB`` in ``backend/config.py``.
# Pinned at the module level so the strategy and the manager's range
# check share the same constant.
_MAX_IMAGES = 4

# Pre-existing values seeded into the project before each apply. Both
# are deliberately distinct from any URL the worker will mint
# (candidate URLs follow the ``/projects/{pid}/media/imgjobs/{job_id}/
# candidate-{cid}.png`` pattern produced by ``LocalStorageBackend``), so
# the "unchanged" assertions below cannot accidentally pass against a
# coincidence between a pre-existing value and a freshly-generated one.
_PRE_BACKGROUND_URL = "/projects/preset/media/preset-background.png"
_PRE_SECTION_URL = "/projects/preset/media/preset-section.png"
# Pre-existing ``SectionBackground`` entry seeded into the project.
# The ``(0, 0)`` indices serve two purposes: they always fit the
# strategy's ``subtitles_len >= 1`` floor for section targets, and they
# coincide with the section indices Hypothesis happens to draw a
# fraction of the time — exercising the upsert-replaces-existing branch
# in :meth:`JobManager.apply_candidate` alongside the
# upsert-as-fresh-insert branch.
_PRE_SECTION_INDICES: tuple[int, int] = (0, 0)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_subtitle(i: int) -> SubtitleSegment:
    """Build a placeholder subtitle for the project's ``subtitles`` list.

    Each segment occupies a half-second window starting at ``i`` so the
    :meth:`SubtitleSegment.validate_timing` invariant
    (``start_time < end_time``) holds for every ``i >= 0``.
    """
    return SubtitleSegment(
        id=f"seg-{i}",
        text=f"Line {i}",
        start_time=float(i),
        end_time=float(i) + 0.5,
        position=Position(x=0.5, y=0.85),
        style=SubtitleStyle(),
    )


def _build_settings() -> Settings:
    """Per-example :class:`Settings` with the test's relevant overrides.

    A fresh ``Settings`` instance per example keeps mutations (e.g. a
    raised concurrency cap so the property doesn't trip Requirement 7.2
    while exercising apply semantics) from leaking into other tests
    sharing the module-level :data:`backend.config.settings`.
    """
    s = Settings()
    s.MAX_IMAGES_PER_JOB = _MAX_IMAGES
    # Generous cap so back-to-back examples can submit without tripping
    # the per-user concurrency limit. Property 7 covers cap honoring
    # elsewhere; this test is about apply semantics.
    s.MAX_CONCURRENT_IMAGE_JOBS_PER_USER = 1000
    return s


# ---------------------------------------------------------------------------
# Hypothesis strategy
# ---------------------------------------------------------------------------


@st.composite
def _scenario(
    draw,
) -> tuple[int, str, int, int, int | None, int | None]:
    """Draw ``(image_count, target_kind, candidate_index, subtitles_len,
    start_index, end_index)``.

    * ``image_count`` ∈ [1, MAX_IMAGES_PER_JOB] — the valid range for
      an accepted submission. The strategy never proposes an
      out-of-range value because rejection paths are covered by
      Property 4 (``test_image_jobs_image_count``); this test focuses
      on the success path.
    * ``candidate_index`` ∈ [0, image_count - 1] — every drawn index
      points at an existing candidate, so the apply call always
      succeeds (the candidate-not-found rejection path is implicit in
      :class:`backend.services.image_job_errors.ImageJobCandidateNotFoundError`
      and out of scope here).
    * ``target_kind`` ∈ {whole_video, section} — both branches of
      Requirement 3.5/3.6 are exercised.
    * ``subtitles_len`` ∈ [0, 8] for whole_video, [1, 8] for section.
      The whole_video branch leaves the field underspecified on
      purpose — the apply path doesn't consult subtitles at all, so
      randomizing the length surfaces any accidental coupling.
    * ``(start_index, end_index)`` — for section only; constrained to
      ``0 <= start <= end < subtitles_len`` so the JobManager accepts
      the submission (Requirement 3.4).
    """
    image_count = draw(st.integers(min_value=1, max_value=_MAX_IMAGES))
    candidate_index = draw(st.integers(min_value=0, max_value=image_count - 1))
    target_kind = draw(st.sampled_from(["whole_video", "section"]))

    if target_kind == "whole_video":
        subtitles_len = draw(st.integers(min_value=0, max_value=8))
        start_index: int | None = None
        end_index: int | None = None
    else:
        subtitles_len = draw(st.integers(min_value=1, max_value=8))
        start_index = draw(st.integers(min_value=0, max_value=subtitles_len - 1))
        end_index = draw(
            st.integers(min_value=start_index, max_value=subtitles_len - 1)
        )

    return (
        image_count,
        target_kind,
        candidate_index,
        subtitles_len,
        start_index,
        end_index,
    )


# ---------------------------------------------------------------------------
# Property 5: Successful job candidates and apply semantics
# ---------------------------------------------------------------------------
# Feature: ai-background-generation, Property 5: Successful job candidates and apply semantics
# Validates: Requirements 3.5, 3.6, 4.4, 5.3


@given(scenario=_scenario())
@hyp_settings(max_examples=50, deadline=None)
def test_apply_candidate_semantics(
    scenario: tuple[int, str, int, int, int | None, int | None],
) -> None:
    """Feature: ai-background-generation, Property 5: Successful job candidates and apply semantics

    Validates: Requirements 3.5, 3.6, 4.4, 5.3

    For any successful Generation_Job J with ``image_count = n``:

    1. ``len(J.candidates) == n``.
    2. Applying any candidate ``c`` of J to a ``whole_video`` target
       sets ``project.background_image = c.url`` and leaves
       ``project.section_backgrounds`` unchanged.
    3. Applying any candidate ``c`` of J to a ``section`` target with
       ``(start_index, end_index)`` upserts a
       :class:`SectionBackground` entry carrying ``c.url`` keyed on
       those indices and leaves ``project.background_image`` unchanged.
    """
    (
        image_count,
        target_kind,
        candidate_index,
        subtitles_len,
        start_index,
        end_index,
    ) = scenario

    async def _run() -> None:
        # Each example gets its own tmp dir so the LocalStorageBackend
        # never observes stale state from a previous draw — project ids
        # are uuid-hex so collisions are vanishingly unlikely, but tmp
        # dir isolation makes the cleanup story trivial.
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = LocalStorageBackend(base_dir=tmpdir)
            test_settings = _build_settings()
            project_service = ProjectService(storage, test_settings)
            fake_backend = FakeImageBackend()
            # Per-example isolated CapabilityState so a stray flip from
            # another test in this process can't make the JobManager
            # submission gate misbehave. The apply path doesn't touch
            # capability state, but the worker reads it on auth-failure
            # paths and the JobManager constructor takes it explicitly.
            manager = JobManager(
                storage=storage,
                project_service=project_service,
                settings=test_settings,
                backend=fake_backend,
                capability_state=CapabilityState(),
            )

            # ----------------------------------------------------------
            # Step 1: create the project and seed it with subtitles plus
            # the pre-existing background fields whose preservation we
            # are about to verify. A single ``update_project`` call
            # mutates all three so the version bump (1 -> 2) is
            # localized and easy to track.
            # ----------------------------------------------------------
            project = await project_service.create_project(
                story_text="Apply semantics test story.",
                owner_id=_OWNER_ID,
                title="Apply Semantics Test",
            )
            project.subtitles = [
                _make_subtitle(i) for i in range(subtitles_len)
            ]
            project.background_image = _PRE_BACKGROUND_URL
            project.section_backgrounds = [
                SectionBackground(
                    start_index=_PRE_SECTION_INDICES[0],
                    end_index=_PRE_SECTION_INDICES[1],
                    image_url=_PRE_SECTION_URL,
                )
            ]
            project = await project_service.update_project(project.id, project)
            # update_project bumps version: created at 1, now 2. Pin
            # the value via the returned state rather than relying on
            # an arithmetic prediction so a future bump-policy change
            # doesn't silently break this test.
            version_after_seed = project.version

            # ----------------------------------------------------------
            # Step 2: submit the job and drive the worker to completion.
            # ----------------------------------------------------------
            if target_kind == "whole_video":
                target = GenerationTarget(kind="whole_video")
            else:
                target = GenerationTarget(
                    kind="section",
                    start_index=start_index,
                    end_index=end_index,
                )

            job = await manager.submit(
                _OWNER_ID,
                project,
                prompt="Apply semantics property test prompt",
                image_count=image_count,
                target=target,
            )

            # Wait for the worker task to finish. The fake backend
            # produces bytes synchronously through its awaits, so the
            # task reaches ``succeeded`` after a few loop ticks.
            task = manager._tasks.get(job.id)
            if task is not None:
                await task

            succeeded_job = await manager.get(_OWNER_ID, job.id)
            assert succeeded_job.status == "succeeded", (
                f"Job must reach status='succeeded' under FakeImageBackend; "
                f"got {succeeded_job.status!r} (error_message="
                f"{succeeded_job.error_message!r})"
            )

            # Property 5.1: the success path produces exactly
            # ``image_count`` candidates (Requirement 4.4).
            assert len(succeeded_job.candidates) == image_count, (
                f"Successful job must produce exactly image_count="
                f"{image_count} candidates; got "
                f"{len(succeeded_job.candidates)}"
            )
            # Defense in depth — every candidate must carry a non-empty
            # URL routed via the existing project media path
            # (Requirement 4.4 / Property 5.1).
            for c in succeeded_job.candidates:
                assert c.url, (
                    f"Candidate {c.id!r} has empty url; the apply "
                    f"semantics property assumes every candidate has a "
                    f"servable URL"
                )

            chosen = succeeded_job.candidates[candidate_index]

            # ----------------------------------------------------------
            # Step 3: apply the chosen candidate and assert the
            # resulting state matches Property 5.2 / 5.3.
            # ----------------------------------------------------------
            updated = await manager.apply_candidate(
                _OWNER_ID,
                job.id,
                candidate_id=chosen.id,
                version=version_after_seed,
            )

            if target_kind == "whole_video":
                # Property 5.2 — Requirement 3.5: whole_video apply sets
                # background_image to the chosen candidate's URL.
                assert updated.background_image == chosen.url, (
                    f"whole_video apply must set background_image to the "
                    f"chosen candidate URL {chosen.url!r}; got "
                    f"{updated.background_image!r}"
                )
                # Property 5.2 — Requirement 3.6 fallback rule:
                # section_backgrounds must be preserved unchanged.
                assert len(updated.section_backgrounds) == 1, (
                    f"whole_video apply must preserve the pre-existing "
                    f"section_backgrounds list; expected length 1, got "
                    f"{len(updated.section_backgrounds)}"
                )
                preserved = updated.section_backgrounds[0]
                assert (
                    preserved.start_index == _PRE_SECTION_INDICES[0]
                    and preserved.end_index == _PRE_SECTION_INDICES[1]
                    and preserved.image_url == _PRE_SECTION_URL
                ), (
                    f"whole_video apply must leave the pre-existing "
                    f"SectionBackground untouched; got {preserved!r}"
                )
            else:
                assert start_index is not None and end_index is not None
                # Property 5.3 — Requirement 3.6: section apply
                # preserves the existing background_image.
                assert updated.background_image == _PRE_BACKGROUND_URL, (
                    f"section apply must preserve background_image; "
                    f"expected {_PRE_BACKGROUND_URL!r}, got "
                    f"{updated.background_image!r}"
                )
                # Find the entry keyed on the applied (start, end). It
                # must exist and carry the chosen candidate's URL.
                target_entries = [
                    sb
                    for sb in updated.section_backgrounds
                    if sb.start_index == start_index
                    and sb.end_index == end_index
                ]
                assert len(target_entries) == 1, (
                    f"section apply must produce exactly one "
                    f"SectionBackground keyed on "
                    f"({start_index}, {end_index}); got "
                    f"{len(target_entries)} entries: {target_entries!r}"
                )
                assert target_entries[0].image_url == chosen.url, (
                    f"section apply must set the entry's image_url to "
                    f"the chosen candidate URL {chosen.url!r}; got "
                    f"{target_entries[0].image_url!r}"
                )
                # Pre-existing entries with a different (start, end)
                # must survive. When the applied indices coincide with
                # the seeded (0, 0) entry, the upsert replaces it in
                # place — that's the upsert-replaces-existing branch
                # and is covered by the same assertion above.
                if (start_index, end_index) == _PRE_SECTION_INDICES:
                    assert len(updated.section_backgrounds) == 1, (
                        f"section apply with indices matching the "
                        f"seeded entry must replace it in place; "
                        f"expected length 1, got "
                        f"{len(updated.section_backgrounds)}"
                    )
                else:
                    assert len(updated.section_backgrounds) == 2, (
                        f"section apply with indices distinct from the "
                        f"seeded entry must append; expected length 2, "
                        f"got {len(updated.section_backgrounds)}"
                    )
                    preserved = next(
                        sb
                        for sb in updated.section_backgrounds
                        if sb.start_index == _PRE_SECTION_INDICES[0]
                        and sb.end_index == _PRE_SECTION_INDICES[1]
                    )
                    assert preserved.image_url == _PRE_SECTION_URL, (
                        f"Pre-existing SectionBackground must survive "
                        f"the apply unchanged; got {preserved!r}"
                    )

    try:
        asyncio.run(_run())
    finally:
        # ``asyncio.run`` closes the event loop it created and leaves
        # the thread without a current event loop. Other tests in the
        # suite still rely on a usable loop being installed on the main
        # thread (matching the hygiene used by
        # ``test_image_jobs_submit_shape``,
        # ``test_image_jobs_section_validation``,
        # ``test_image_jobs_image_count``).
        asyncio.set_event_loop(asyncio.new_event_loop())
