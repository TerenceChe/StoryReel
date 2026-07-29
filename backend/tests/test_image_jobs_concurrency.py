"""Property-based test for per-user concurrency cap.

Feature: ai-background-generation, Property 7: Per-user concurrency cap is honored
Validates: Requirements 7.2, 7.3

Stress-tests the JobManager's per-owner concurrency budget via randomized
submit / drive / drain / gate sequences. The invariants encode the design
guarantees:

* ``len(_running_per_owner[owner]) <= MAX_CONCURRENT_IMAGE_JOBS_PER_USER``
  at every observed moment (Requirement 7.2).
* A submission attempted while at the cap raises
  :class:`ImageJobConcurrencyError`; the router maps this to HTTP 429
  (Requirement 7.2).
* When a job reaches a terminal state (``succeeded``/``failed``), its slot
  is released so a subsequent submission within the cap is accepted
  (Requirement 7.3).

Stateful testing via Hypothesis's ``RuleBasedStateMachine`` fits because
the property is about a *trace* of operations, not a single input. To
exercise the cap-exceeded path reliably the worker pipeline needs to be
*pausable* — a bare :class:`FakeImageBackend` runs synchronously through
its awaits, so a worker started during ``run_until_complete`` will
typically race to completion before the call returns. The
:class:`_GatedImageBackend` below wraps the fake with an
:class:`asyncio.Event` gate that lets the test hold workers in their
``running`` state while it fills the concurrency budget.
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

from hypothesis import settings as hyp_settings
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from backend.config import Settings
from backend.models.image_gen import ImageGenerationBackend
from backend.models.image_jobs import GenerationTarget
from backend.models.project import PipelineProgress, ProjectState
from backend.persistence.base import StorageBackend
from backend.services.image_capability_state import CapabilityState
from backend.services.image_job_errors import ImageJobConcurrencyError
from backend.services.image_job_service import JobManager
from backend.services.project_service import ProjectService
from backend.tests._image_fakes import FakeImageBackend


# Single owner is sufficient — the cap is per-owner, so cross-owner
# interleavings would dilute coverage of the bounded budget the property is
# about. A small cap keeps "fill the cap" sequences short and tractable.
_OWNER_ID = "owner-test"
_CAP = 2



class _InMemoryStorage(StorageBackend):
    """Minimal in-memory ``StorageBackend`` for the JobManager worker.

    The worker calls ``save_file`` (for persisted candidates) and
    ``get_file_url``. ``load_file`` is exercised only when a reference
    image is attached, which this test never does. Every method is
    implemented for interface completeness.
    """

    def __init__(self) -> None:
        self._files: dict[tuple[str, str], bytes] = {}

    async def save_file(
        self, project_id: str, filename: str, data: AsyncIterator[bytes]
    ) -> str:
        chunks: list[bytes] = []
        async for chunk in data:
            chunks.append(chunk)
        self._files[(project_id, filename)] = b"".join(chunks)
        return f"/projects/{project_id}/media/{filename}"

    async def load_file(
        self, project_id: str, filename: str
    ) -> AsyncIterator[bytes]:
        try:
            payload = self._files[(project_id, filename)]
        except KeyError as exc:
            raise FileNotFoundError(f"{project_id}/{filename}") from exc

        async def _gen() -> AsyncIterator[bytes]:
            yield payload

        return _gen()

    async def get_file_url(self, project_id: str, filename: str) -> str:
        return f"/projects/{project_id}/media/{filename}"

    async def delete_project(self, project_id: str) -> None:
        self._files = {
            k: v for k, v in self._files.items() if k[0] != project_id
        }


class _GatedImageBackend(ImageGenerationBackend):
    """:class:`FakeImageBackend` wrapped with a controllable async gate.

    Workers driving generation via ``generate_candidates`` /
    ``generate_section_candidates`` block on ``self.gate`` before producing
    bytes. With the gate cleared, jobs pile up in ``running`` state, which
    lets the state machine fill the per-owner concurrency budget and
    observe cap-exceeded submissions raise ``ImageJobConcurrencyError``.
    With the gate set (the default), generation proceeds the same way the
    bare fake would — drains and the "completion frees a slot" property
    work normally.

    The wrapper preserves the duck-typed candidate-method contract the
    JobManager worker relies on (per the design's "Provider Adapter"
    section) without modifying the shared :class:`FakeImageBackend` fixture.
    """

    def __init__(self) -> None:
        self._fake = FakeImageBackend()
        # The Event is created lazily on first use so it binds to the
        # event loop the state machine runs against. Constructing it eagerly
        # at __init__ time risks attaching to the wrong loop on Python
        # versions where ``asyncio.Event`` captures ``get_event_loop()``.
        self._gate: asyncio.Event | None = None

    def _ensure_gate(self) -> asyncio.Event:
        if self._gate is None:
            self._gate = asyncio.Event()
            self._gate.set()  # default: open, jobs proceed normally
        return self._gate

    def open(self) -> None:
        """Allow paused workers to proceed."""
        self._ensure_gate().set()

    def close(self) -> None:
        """Hold subsequent workers in their await on ``generate_candidates``."""
        self._ensure_gate().clear()

    # -- abstract-base methods (kept for interface completeness) --------

    async def generate_single(self, prompt: str) -> bytes:
        await self._ensure_gate().wait()
        return await self._fake.generate_single(prompt)

    async def generate_sectioned(self, prompts: list[str]) -> list[bytes]:
        await self._ensure_gate().wait()
        return await self._fake.generate_sectioned(prompts)

    # -- duck-typed candidate methods (the JobManager actually uses these) --

    async def generate_candidates(
        self,
        prompt: str,
        *,
        image_count: int,
        reference_image_bytes: bytes | None,
    ) -> list[bytes]:
        await self._ensure_gate().wait()
        return await self._fake.generate_candidates(
            prompt,
            image_count=image_count,
            reference_image_bytes=reference_image_bytes,
        )

    async def generate_section_candidates(
        self,
        prompts: list[str],
        *,
        image_count: int,
        reference_image_bytes: bytes | None,
    ) -> list[list[bytes]]:
        await self._ensure_gate().wait()
        return await self._fake.generate_section_candidates(
            prompts,
            image_count=image_count,
            reference_image_bytes=reference_image_bytes,
        )



def _build_settings() -> Settings:
    """Build a ``Settings`` instance with the small cap used by this test.

    Instance-attribute assignment shadows the class-level default, so other
    tests sharing the module-level :data:`backend.config.settings` are
    unaffected.
    """
    s = Settings()
    s.MAX_CONCURRENT_IMAGE_JOBS_PER_USER = _CAP
    s.MAX_IMAGES_PER_JOB = 4
    return s


def _build_project() -> ProjectState:
    return ProjectState(
        id="project-concurrency-test",
        owner_id=_OWNER_ID,
        title="Concurrency Test",
        story_text="A short story for concurrency testing.",
        pipeline_progress=PipelineProgress(stage="narration", message="Queued"),
        created_at="2025-01-01T00:00:00+00:00",
        updated_at="2025-01-01T00:00:00+00:00",
    )


class _PerUserConcurrencyCapMachine(RuleBasedStateMachine):
    """Random submit / drive / drain / gate operations on a single owner.

    Feature: ai-background-generation, Property 7: Per-user concurrency cap is honored
    Validates: Requirements 7.2, 7.3
    """

    def __init__(self) -> None:
        super().__init__()
        # One event loop per machine instance so all asyncio.Task objects
        # created inside submit() stay attached to the same loop across
        # rule invocations. ``run_until_complete`` makes this loop the
        # running loop for the duration of each call, which is what
        # ``asyncio.create_task`` looks up to schedule the worker.
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

        self.storage = _InMemoryStorage()
        self.settings = _build_settings()
        self.backend = _GatedImageBackend()
        self.project = _build_project()
        self.project_service = ProjectService(self.storage, self.settings)
        # Per-machine isolated CapabilityState — the singleton in
        # backend.services.image_capability_state is shared globally and
        # we don't want a previous test's auth-failure flip to bleed in.
        self.manager = JobManager(
            storage=self.storage,
            project_service=self.project_service,
            settings=self.settings,
            backend=self.backend,
            capability_state=CapabilityState(),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _running_set(self) -> set[str]:
        """Snapshot of the per-owner concurrency-budgeted job IDs.

        ``_running_per_owner`` tracks every job that is *not yet finalized*
        — i.e. ``pending`` plus ``running`` jobs. The slot is released
        inside ``_run_job``'s ``finally`` block when the worker reaches
        ``succeeded`` or ``failed``.
        """
        return set(self.manager._running_per_owner.get(_OWNER_ID, set()))

    def _active_count(self) -> int:
        return len(self._running_set())

    def _owned_jobs(self):
        return [
            job
            for job in self.manager._jobs.values()
            if job.owner_id == _OWNER_ID
        ]


    # ------------------------------------------------------------------
    # Rules
    # ------------------------------------------------------------------

    @rule()
    def try_submit(self) -> None:
        """Attempt a job submission.

        Mirrors the router's behavior: if the per-owner budget is full
        *before* the call, the submission must raise
        ``ImageJobConcurrencyError`` (which the router maps to 429).
        Otherwise it must succeed and the new job's ID must appear in
        the registry.

        ``run_until_complete(submit)`` can run other ready tasks as a side
        effect — for example, an open-gate rule earlier in the trace may
        have released the gate, and workers parked at ``gate.wait()`` will
        wake and finish (releasing their slots) the next time the loop
        runs. To make the pre/post predictions stable we explicitly drain
        the loop *before* taking the snapshot. After that drain, the only
        tasks still consuming slots are workers parked at ``gate.wait()``
        under the *current* gate state. While the gate state stays
        constant during this rule's body, those workers cannot release
        their slots, so ``before`` is a faithful predictor of the cap.
        """

        async def _stabilize_then_submit():
            # Yield repeatedly so every ready task has a chance to run
            # under the current gate state. Workers either reach
            # ``gate.wait()`` (gate closed) or finish (gate open). Once
            # this loop completes, the running set is stable until the
            # gate or new tasks change it.
            for _ in range(8):
                await asyncio.sleep(0)
            # Snapshot inside the same coroutine, after stabilization,
            # so no event-loop tick happens between the snapshot and the
            # submit call's first lock acquisition.
            before_local = len(
                self.manager._running_per_owner.get(_OWNER_ID, set())
            )
            cap = self.settings.MAX_CONCURRENT_IMAGE_JOBS_PER_USER
            try:
                job = await self.manager.submit(
                    _OWNER_ID,
                    self.project,
                    prompt="background prompt",
                    image_count=1,
                    target=GenerationTarget(kind="whole_video"),
                )
            except ImageJobConcurrencyError:
                # The cap was full at lock-acquisition time — exactly the
                # behavior Requirement 7.2 mandates. Verify the snapshot
                # agrees: a rejected submission can only happen when the
                # running set was already at the cap.
                return ("rejected", before_local, cap)
            return ("accepted", before_local, cap, job)

        outcome = self.loop.run_until_complete(_stabilize_then_submit())

        if outcome[0] == "rejected":
            _, before_local, cap = outcome
            assert before_local >= cap, (
                f"Submission was rejected with ImageJobConcurrencyError but "
                f"only {before_local} slots were in use against cap {cap}; "
                f"rejection must imply the cap was full"
            )
        else:
            _, before_local, cap, job = outcome
            assert before_local < cap, (
                f"Submission was accepted but the running set already held "
                f"{before_local} jobs against cap {cap}; acceptance must "
                f"imply room under the cap (Requirement 7.2)"
            )
            assert job.owner_id == _OWNER_ID
            assert job.id in self.manager._jobs, (
                "A successful submit must register the job in the registry"
            )

    @rule()
    def drive_loop(self) -> None:
        """Yield to the event loop so worker tasks can make progress.

        ``asyncio.Task`` objects created in submit() are pending until the
        loop is given a chance to schedule them. A handful of zero-delay
        sleeps is enough for ``FakeImageBackend``'s deterministic,
        await-free bodies to drain through ``_run_job`` *when the gate is
        open*. With the gate closed, workers stay parked in their
        ``await gate.wait()`` regardless of how many ticks the loop runs.
        """

        async def _tick():
            for _ in range(8):
                await asyncio.sleep(0)

        self.loop.run_until_complete(_tick())

    @rule()
    def open_gate(self) -> None:
        """Allow paused workers to finish.

        Idempotent — opening an already-open gate is a no-op. After this
        rule the gate alone is open; the workers still need a few loop
        ticks (delivered by ``drive_loop`` or ``drain_all``) to actually
        observe the change and reach a terminal state.
        """
        self.backend.open()

    @rule()
    def close_gate(self) -> None:
        """Force subsequent workers to park in ``running`` state.

        After this rule, any newly-submitted job will block at
        ``await gate.wait()`` inside ``generate_candidates``, occupying a
        per-owner slot. This is what lets the state machine observe the
        cap actually being reached and exercise the
        ``ImageJobConcurrencyError`` branch.
        """
        self.backend.close()

    @rule()
    def drain_all(self) -> None:
        """Open the gate and wait for every in-flight worker to complete.

        After this rule the per-owner running set MUST be empty: every
        submitted job has reached ``succeeded`` (or ``failed``) and the
        ``finally`` block in ``_run_job`` has discarded the slot. Direct
        check on Requirement 7.3 — completion frees the slot.

        We open the gate here (not just in ``open_gate``) so a long
        cap-closed run can be drained in a single rule: leaving workers
        parked at the end of an example would leak event-loop resources
        through teardown.
        """
        self.backend.open()
        in_flight = [t for t in self.manager._tasks.values() if not t.done()]
        if not in_flight:
            return

        async def _wait_all():
            await asyncio.wait(set(in_flight), return_when=asyncio.ALL_COMPLETED)
            # One extra tick so the worker's finally-block lock release is
            # observable on the JobManager registry by the time we look.
            await asyncio.sleep(0)

        self.loop.run_until_complete(_wait_all())

        assert self._active_count() == 0, (
            "After all workers complete, the per-owner running set must be "
            "empty (Requirement 7.3 — completion frees the slot)"
        )


    # ------------------------------------------------------------------
    # Invariants — checked by Hypothesis after every rule.
    # ------------------------------------------------------------------

    @invariant()
    def cap_never_exceeded(self) -> None:
        """``len(running_for_owner) <= MAX_CONCURRENT_IMAGE_JOBS_PER_USER``.

        Headline invariant of Property 7 and the direct encoding of
        Requirement 7.2's "exceeding the cap" guard. If the JobManager
        ever permitted a third concurrent slot for the owner under any
        rule sequencing, this invariant fails.
        """
        cap = self.settings.MAX_CONCURRENT_IMAGE_JOBS_PER_USER
        active = self._active_count()
        assert active <= cap, (
            f"Per-owner running set size {active} exceeds cap {cap}"
        )

    @invariant()
    def terminal_jobs_release_slot(self) -> None:
        """A job in ``succeeded`` or ``failed`` MUST not occupy a slot.

        Direct encoding of Requirement 7.3 — the slot is released when
        the job reaches a terminal state. Asserted as a continuous
        invariant (not just at the end of a drain) so any ordering bug
        in the worker's ``finally`` block is caught immediately.
        """
        running = self._running_set()
        for job in self._owned_jobs():
            if job.status in ("succeeded", "failed"):
                assert job.id not in running, (
                    f"Job {job.id} is in terminal state {job.status} but "
                    f"still occupies a concurrency slot"
                )

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def teardown(self) -> None:
        """Release any leftover gated workers and close the loop.

        ``RuleBasedStateMachine`` re-instantiates the machine per example,
        so leftover tasks from one example must not leak into the next.
        We open the gate first so any worker parked on ``gate.wait()`` can
        observe ``CancelledError`` at its next await point and exit
        cleanly.
        """

        async def _cleanup():
            self.backend.open()
            tasks = list(self.manager._tasks.values())
            for t in tasks:
                if not t.done():
                    t.cancel()
            for t in tasks:
                try:
                    await t
                except BaseException:  # noqa: BLE001 — best-effort drain
                    pass

        try:
            self.loop.run_until_complete(_cleanup())
        finally:
            self.loop.close()
            # Install a fresh, unstarted loop on the thread so subsequent
            # tests that call ``asyncio.get_event_loop()`` (a pattern used
            # in several other test modules in this suite) find a usable
            # loop. Setting ``None`` here would leave the thread with no
            # current loop and break those tests with
            # ``RuntimeError: There is no current event loop in thread``.
            asyncio.set_event_loop(asyncio.new_event_loop())


# Hypothesis exposes the state machine as a ``unittest.TestCase`` subclass
# under ``.TestCase``. Attach the requested settings (max_examples=100,
# deadline=None — workers run real asyncio so timing is environment-sensitive
# and we don't want spurious deadline failures) and re-export under a
# property-tagged name so pytest collects it.
_PerUserConcurrencyCapMachine.TestCase.settings = hyp_settings(
    max_examples=100, deadline=None
)


TestPerUserConcurrencyCap = _PerUserConcurrencyCapMachine.TestCase
TestPerUserConcurrencyCap.__doc__ = (
    "Feature: ai-background-generation, "
    "Property 7: Per-user concurrency cap is honored"
)
