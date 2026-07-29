"""Property-based test for ownership enforcement on job-scoped routes.

Feature: ai-background-generation, Property 6: Ownership enforcement on all job-scoped routes
Validates: Requirements 6.1, 6.2, 6.3, 9.4, 9.5

Hypothesis randomizes ``(project_owner, caller, route)`` triples where
the caller is **not** the project owner. For every drawn triple the test
asserts the route returns HTTP 403, regardless of the body content
fuzzed alongside it. Five routes are covered:

* ``POST /projects/{P}/image-jobs`` — submit
* ``POST /projects/{P}/image-jobs/{job_id}/reference`` — reference upload
* ``GET  /projects/{P}/image-jobs/{job_id}`` — status
* ``POST /projects/{P}/image-jobs/{job_id}/apply`` — apply candidate
* ``GET  /projects/{P}/media/{filename}`` — media serving for any
  imgjobs-style filename

Design notes baked into this test:

* Ownership-check fires inside ``_load_owned_project`` (mirrored on both
  the image-jobs router and the projects router). Because that helper
  runs before any JobManager / storage call, a synthetic ``job_id`` is
  enough to drive the property — the JobManager registry never needs an
  actual matching job, the storage never needs an actual candidate
  file. The 403 comes from ``verify_project_ownership`` long before
  any of those sub-systems is touched.
* The submit handler additionally guards on
  ``_require_image_generation``. This dependency is resolved alongside
  the other deps before the handler body runs, so the test wires
  :class:`FakeImageBackend` (capability enabled) to ensure the gate
  passes and the property exclusively pins the ownership behavior, not
  the capability gate (Property 10 covers the gate elsewhere).
* Bodies sent to the routes are randomized by Hypothesis to honor the
  "regardless of body content" clause. They are constrained to be
  *Pydantic-schema-valid* so FastAPI never short-circuits with 422
  before the handler runs (an invalid body would surface 422, not 403,
  and dilute the property).
* For the media route, the ``{filename}`` path parameter is
  single-segment only (Starlette default). The URL is constructed with
  a single-segment imgjobs-style name (e.g. ``imgjobs-<token>.png``)
  so the route matches and the in-handler slash-check does not fire
  before the ownership check — both prerequisites for observing 403.
* The capability flag is reset at the start of every example so a
  prior test in the same process that tripped a simulated provider
  authentication failure cannot turn this example's submit into a 503
  via the ``_require_image_generation`` gate.
"""

from __future__ import annotations

import asyncio
import string
from typing import AsyncIterator

import httpx
from fastapi import FastAPI
from httpx import ASGITransport
from hypothesis import assume, given, settings as hyp_settings, strategies as st

from backend.auth.middleware import get_owner_id
from backend.auth.middleware import get_settings as get_auth_settings
from backend.config import Settings
from backend.dependencies import (
    get_image_backend,
    get_job_manager,
    get_project_service,
    get_settings,
    get_storage,
)
from backend.models.project import PipelineProgress, ProjectState
from backend.persistence.base import StorageBackend
from backend.routers.image_jobs import router as image_jobs_router
from backend.routers.projects import router as projects_router
from backend.services.image_capability_state import capability_state
from backend.services.image_job_service import JobManager
from backend.services.project_service import ProjectNotFoundError
from backend.tests._image_fakes import FakeImageBackend


# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------

_PROJECT_ID = "project-authorization-test"
# A synthetic job id is sufficient — every route's ownership check fires
# before the JobManager is consulted, so the job never has to exist.
_JOB_ID = "synthetic-job-id-for-auth-test"

# Small fixed pool of owner identifiers. Hypothesis explores the cross
# product (project_owner, caller) with the constraint caller != owner.
# Four owners give twelve distinct (owner, caller) pairs, which 100
# examples cover several times each while keeping the strategy's domain
# tractable.
_OWNERS = ("owner-A", "owner-B", "owner-C", "owner-D")

# Five routes the property covers. Order matches the feature's design
# section "Property 6: Ownership enforcement on all job-scoped routes".
_ROUTES = ("submit", "reference", "status", "apply", "media")

# Reference upload domains — values cover both allowed and disallowed
# combinations so the body-content fuzzing is meaningful. The handler's
# 422/413 branches both come *after* the ownership check, so a
# non-owner sees 403 regardless of which combination is drawn.
_REF_CONTENT_TYPES = (
    "image/png",
    "image/jpeg",
    "application/octet-stream",
    "text/plain",
    "",
)
_REF_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".txt", "")


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _InMemoryStorage(StorageBackend):
    """Minimal in-memory ``StorageBackend`` for the test app.

    None of the routes under test reach storage — ownership-check fires
    before any ``save_file`` / ``load_file`` / ``get_file_url`` call —
    but ``StorageBackend`` is abstract so a concrete implementation is
    required for FastAPI dependency injection to resolve cleanly.
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


class _StubProjectService:
    """Stub ``ProjectService`` returning a single canned project.

    The image-jobs router and the projects router both call
    ``project_service.get_project(project_id)`` via their
    ``_load_owned_project`` helper. Ownership-check (the ``403`` source
    under test) follows immediately, so a stub is sufficient — no need
    to stand up a full :class:`backend.services.project_service.ProjectService`
    per example.
    """

    def __init__(self, project: ProjectState) -> None:
        self._project = project

    async def get_project(self, project_id: str) -> ProjectState:
        if project_id != self._project.id:
            raise ProjectNotFoundError(f"Project {project_id} not found")
        return self._project


def _make_project(owner_id: str) -> ProjectState:
    """Construct a deterministic :class:`ProjectState` owned by ``owner_id``.

    The project is the only state ``_load_owned_project`` consults to
    answer "is the caller the owner?". Every other field is irrelevant
    to the property under test — a minimal, valid ``ProjectState`` is
    enough.
    """
    return ProjectState(
        id=_PROJECT_ID,
        owner_id=owner_id,
        title="Ownership Enforcement Test",
        story_text="A short story for ownership-enforcement testing.",
        pipeline_progress=PipelineProgress(stage="narration", message="Queued"),
        subtitles=[],
        created_at="2025-01-01T00:00:00+00:00",
        updated_at="2025-01-01T00:00:00+00:00",
    )


# ---------------------------------------------------------------------------
# Hypothesis strategy
# ---------------------------------------------------------------------------


# Restrict generated text to a printable ASCII subset so request bodies
# always serialize to JSON / multipart cleanly. The property does not
# depend on unicode handling — body content is only randomized to honor
# the "regardless of body content" clause.
_SAFE_TEXT = st.text(
    alphabet=string.ascii_letters + string.digits + " -_.",
    min_size=0,
    max_size=64,
)
# Single-segment filename for the media route. The
# ``{filename}`` path parameter on the projects router is single-segment
# only, so any embedded ``/`` would either prevent route matching or
# trigger the in-handler 400 *before* the ownership check (the 400
# branch comes first). The pattern below mimics an ``imgjobs/<job>/
# candidate-<cid>.png`` filename flattened to a single segment so the
# route matches and ownership-check is what produces the response.
_MEDIA_FILENAME = st.from_regex(
    r"^imgjobs-[a-zA-Z0-9_]{1,30}-candidate-[a-zA-Z0-9_]{1,30}\.png$",
    fullmatch=True,
)


@st.composite
def _scenario(draw) -> dict:
    """Draw a ``(project_owner, caller, route, body)`` scenario.

    All body fields are drawn unconditionally — even when the chosen
    route doesn't use them — so the strategy stays composable and the
    test code can reach into the dict by key without per-route guards.
    The "ignored" fields exercise that body content cannot influence
    the 403 outcome (Property 6's "regardless of body content" clause).

    Body values are constrained to be *Pydantic-schema-valid* for the
    submit and apply request models so FastAPI never short-circuits
    with 422 before the handler runs (which would mask the ownership
    check and dilute the property).
    """
    project_owner = draw(st.sampled_from(_OWNERS))
    caller = draw(st.sampled_from(_OWNERS))
    # Property 6 is about non-owner callers. Hypothesis's ``assume`` is
    # the right primitive here — it discards the example silently when
    # the constraint isn't met, so the strategy doesn't waste effort
    # generating intricate non-pairs.
    assume(caller != project_owner)

    route = draw(st.sampled_from(_ROUTES))

    # --- Submit body (Pydantic-valid ImageJobSubmitRequest) ----------
    prompt = draw(_SAFE_TEXT)
    # ``image_count`` may be any int — the JobManager's range check
    # never runs, ownership-check fires first. Using a wide range
    # exercises both in-range and out-of-range bodies.
    image_count = draw(st.integers(min_value=-100, max_value=100))
    target_kind = draw(st.sampled_from(["whole_video", "section"]))
    if target_kind == "whole_video":
        target: dict = {"kind": "whole_video"}
    else:
        # GenerationTarget's model_validator requires both indices when
        # kind is "section". Index *values* are unconstrained — the
        # JobManager's index validation never runs (ownership-check
        # fires first), so widely-ranging integers exercise the body
        # randomization without tripping Pydantic.
        start_index = draw(st.integers(min_value=-3, max_value=10))
        end_index = draw(st.integers(min_value=-3, max_value=10))
        target = {
            "kind": "section",
            "start_index": start_index,
            "end_index": end_index,
        }
    submit_body = {
        "prompt": prompt,
        "image_count": image_count,
        "target": target,
    }

    # --- Apply body (Pydantic-valid ApplyCandidateRequest) -----------
    candidate_id = draw(_SAFE_TEXT)
    version = draw(st.integers(min_value=-100, max_value=100))
    apply_body = {"candidate_id": candidate_id, "version": version}

    # --- Reference upload (multipart fields) -------------------------
    ref_content_type = draw(st.sampled_from(_REF_CONTENT_TYPES))
    ref_extension = draw(st.sampled_from(_REF_EXTENSIONS))
    # Cap the body size at 128 bytes — the route's size-check (which
    # bounds at MAX_REFERENCE_IMAGE_SIZE_MB MB) runs *after* the
    # ownership check, so a small body keeps each example fast while
    # still exercising the multipart parsing path.
    ref_size = draw(st.integers(min_value=0, max_value=128))

    # --- Media route filename (single-segment, imgjobs-style) --------
    media_filename = draw(_MEDIA_FILENAME)

    return {
        "project_owner": project_owner,
        "caller": caller,
        "route": route,
        "submit_body": submit_body,
        "apply_body": apply_body,
        "ref_content_type": ref_content_type,
        "ref_extension": ref_extension,
        "ref_size": ref_size,
        "media_filename": media_filename,
    }


# ---------------------------------------------------------------------------
# Property 6: Ownership enforcement on all job-scoped routes
# ---------------------------------------------------------------------------
# Feature: ai-background-generation, Property 6: Ownership enforcement on all job-scoped routes
# Validates: Requirements 6.1, 6.2, 6.3, 9.4, 9.5


@given(scenario=_scenario())
@hyp_settings(max_examples=100, deadline=None)
def test_non_owner_receives_403_on_every_job_scoped_route(
    scenario: dict,
) -> None:
    """Feature: ai-background-generation, Property 6: Ownership enforcement on all job-scoped routes

    Validates: Requirements 6.1, 6.2, 6.3, 9.4, 9.5

    For any authenticated caller ``U`` and any project ``P`` whose owner
    is not ``U``, every call by ``U`` to any of the five job-scoped
    routes — submit, reference, status, apply, and media (for any
    imgjobs-style filename) — returns 403, regardless of body content.
    """
    project_owner: str = scenario["project_owner"]
    caller: str = scenario["caller"]
    route: str = scenario["route"]

    async def _run() -> None:
        # Reset the process-level capability flag so any earlier test
        # (or earlier Hypothesis example here) that observed a simulated
        # ProviderAuthenticationError cannot leave the capability
        # disabled and turn the submit route into a 503 before the
        # ownership check runs.
        capability_state.reset()

        # Per-example app + manager so FastAPI dependency overrides stay
        # scoped to this example and the JobManager registry never
        # carries leftovers across draws.
        app = FastAPI()
        # Both routers must be mounted: the four image-jobs routes live
        # on ``image_jobs_router`` and the media route lives on
        # ``projects_router``. Their prefixes are disjoint
        # (``/projects/{id}/image-jobs`` vs ``/projects``) so route
        # matching is unambiguous.
        app.include_router(image_jobs_router)
        app.include_router(projects_router)

        test_settings = Settings()
        # Bypass auth — the test owns the ``get_owner_id`` override so
        # the auth middleware never runs. ``DISABLE_AUTH`` is also
        # flipped for defense in depth.
        test_settings.DISABLE_AUTH = True
        test_settings.LOCAL_OWNER_ID = caller
        # The reference-upload route reads
        # ``MAX_REFERENCE_IMAGE_SIZE_MB`` to size its byte cap. The
        # value is irrelevant to this property (size-check fires after
        # ownership-check) — the explicit assignment documents the
        # decoupling.
        test_settings.MAX_REFERENCE_IMAGE_SIZE_MB = 50

        project = _make_project(project_owner)
        storage = _InMemoryStorage()
        fake_backend = FakeImageBackend()
        stub_project_service = _StubProjectService(project)
        manager = JobManager(
            storage=storage,
            project_service=stub_project_service,  # type: ignore[arg-type]
            settings=test_settings,
            backend=fake_backend,
        )

        # ``get_owner_id`` is the lever that simulates a non-owner
        # caller. Override it so every request in this example is
        # authenticated as ``caller`` (the non-owner principal drawn by
        # the strategy).
        app.dependency_overrides[get_owner_id] = lambda: caller
        # Override both ``get_settings`` providers (router-side and
        # auth-side) so any settings lookup the routes happen to
        # perform sees the same per-example values.
        app.dependency_overrides[get_settings] = lambda: test_settings
        app.dependency_overrides[get_auth_settings] = lambda: test_settings
        # Wire the FakeImageBackend so the submit route's
        # ``_require_image_generation`` gate passes — without this the
        # gate would 503 before the ownership check on submit.
        app.dependency_overrides[get_image_backend] = lambda: fake_backend
        app.dependency_overrides[get_project_service] = (
            lambda: stub_project_service
        )
        app.dependency_overrides[get_job_manager] = lambda: manager
        app.dependency_overrides[get_storage] = lambda: storage

        try:
            transport = ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                if route == "submit":
                    resp = await client.post(
                        f"/projects/{_PROJECT_ID}/image-jobs",
                        json=scenario["submit_body"],
                    )
                elif route == "reference":
                    ref_filename = f"reference{scenario['ref_extension']}"
                    body = b"\x00" * scenario["ref_size"]
                    resp = await client.post(
                        f"/projects/{_PROJECT_ID}"
                        f"/image-jobs/{_JOB_ID}/reference",
                        files={
                            "file": (
                                ref_filename,
                                body,
                                scenario["ref_content_type"],
                            )
                        },
                    )
                elif route == "status":
                    resp = await client.get(
                        f"/projects/{_PROJECT_ID}"
                        f"/image-jobs/{_JOB_ID}",
                    )
                elif route == "apply":
                    resp = await client.post(
                        f"/projects/{_PROJECT_ID}"
                        f"/image-jobs/{_JOB_ID}/apply",
                        json=scenario["apply_body"],
                    )
                elif route == "media":
                    resp = await client.get(
                        f"/projects/{_PROJECT_ID}"
                        f"/media/{scenario['media_filename']}",
                    )
                else:  # pragma: no cover — defensive; strategy is closed
                    raise AssertionError(f"unknown route: {route!r}")

            assert resp.status_code == 403, (
                f"Expected 403 for non-owner caller={caller!r} hitting "
                f"route={route!r} on project owned by "
                f"{project_owner!r}; got {resp.status_code}: "
                f"{resp.text!r}"
            )
        finally:
            # Cancel any worker task spawned by an accepted submit so
            # it does not outlive the per-example event loop. The
            # ownership-check returns 403 before ``JobManager.submit``
            # is called for non-owner callers, so under the property
            # this list is normally empty — the drain is defense in
            # depth in case a future refactor changes that ordering.
            tasks = list(manager._tasks.values())
            for task in tasks:
                if not task.done():
                    task.cancel()
            for task in tasks:
                try:
                    await task
                except BaseException:  # noqa: BLE001 — best-effort drain
                    pass

    try:
        asyncio.run(_run())
    finally:
        # ``asyncio.run`` closes the event loop it created and leaves
        # the thread without a current event loop. Other tests in the
        # suite still rely on a usable loop being installed on the main
        # thread, so install a fresh one (matching the hygiene used by
        # peer property tests in this directory).
        asyncio.set_event_loop(asyncio.new_event_loop())
