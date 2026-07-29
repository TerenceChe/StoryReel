"""Property-based test for operator-opaque responses.

Feature: ai-background-generation, Property 1: Capability and error responses are operator-opaque
Validates: Requirements 1.4, 1.7, 5.4

Hypothesis randomizes across a mix of scenarios — capability calls,
valid submits, invalid submits, reference upload errors, and failed-job
status reads — and for every drawn scenario asserts the response body
contains none of the forbidden substrings (case-insensitive substring
search):

* The synthetic API key value injected into the bound
  :class:`FakeImageBackend` (a distinctive marker that stands in for a
  real provider credential).
* Environment variable names this feature reads:
  ``IMAGE_GEN_PROVIDER``, ``OPENAI_API_KEY``, ``MAX_IMAGES_PER_JOB``,
  ``MAX_CONCURRENT_IMAGE_JOBS_PER_USER``, ``MAX_REFERENCE_IMAGE_SIZE_MB``.
* The literal strings ``API key`` and ``README``.
* Python traceback markers ``Traceback`` and ``File "``.

These constants come straight from the design's "Property 1" definition
and from Requirements 1.4, 1.7, and 5.4 — none of these strings may
ever cross the backend/frontend boundary.

Design notes baked into this test:

* Each scenario is exercised through the *real* image-jobs and
  capability routers (no router stubbing). The forbidden-substring
  predicate is applied to the response body verbatim, so any future
  refactor that accidentally leaks an env var name or a provider trace
  is caught here.
* The synthetic API key value is a distinctive, easy-to-grep string —
  ``SyntheticApiKeyValue_PROPERTY1_DO_NOT_LEAK_42x9``. The
  :class:`FakeImageBackend` records the marker on its instance but
  never echoes it into call records, return payloads, exception
  messages, or anywhere else. The test asserts the marker never
  appears in any response body, so even a hypothetical leak path
  through some unexpected code site would be caught.
* The "failed-job" branch drives a job to ``status="failed"`` by flipping
  :attr:`FakeImageBackend.simulate_auth_failure` on **after** the
  capability_state reset. The auth-failure path also flips the
  process-level capability flag off via
  :meth:`CapabilityState.disable_for_session`. This is the most
  dangerous response shape from a leak perspective — provider
  exception messages are most likely to carry sensitive content — so
  the property gives it a fair share of Hypothesis's example budget.
* The ``calls`` list on the fake records call inputs (prompts,
  image_count, etc). A future refactor that surfaces backend internals
  in HTTP responses would tend to dump these via JSON serialization,
  so the prompt fed into valid submits is a benign ASCII string that
  doesn't accidentally collide with any forbidden substring. The fake's
  call records themselves are kept entirely server-side.
* The reference-upload branch drives the route with deliberately
  malformed inputs (wrong content-type, wrong extension, oversized
  body) so the 422 / 413 error paths are exercised. The success path
  doesn't produce a response containing any forbidden string by
  construction (the route's success body is just
  ``{"detail": "Reference image attached"}``), so the property is
  sharper when biased toward error paths.
* All routes share the same per-example FastAPI app + JobManager
  pattern from
  :mod:`backend.tests.test_image_jobs_authorization`, ensuring the
  JobManager registry never carries leftovers across draws and
  ``app.dependency_overrides`` stay scoped to the example.
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

import httpx
from fastapi import FastAPI
from httpx import ASGITransport
from hypothesis import given, settings as hyp_settings, strategies as st

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
from backend.models.image_jobs import GenerationTarget
from backend.models.project import PipelineProgress, ProjectState
from backend.models.subtitle import Position, SubtitleSegment, SubtitleStyle
from backend.persistence.base import StorageBackend
from backend.routers.image_generation import router as capability_router
from backend.routers.image_jobs import router as image_jobs_router
from backend.services.image_capability_state import (
    CapabilityState,
    capability_state,
)
from backend.services.image_job_service import JobManager
from backend.services.project_service import ProjectNotFoundError
from backend.tests._image_fakes import FakeImageBackend


# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------

_OWNER_ID = "owner-response-safety-test"
_PROJECT_ID = "project-response-safety-test"
# Same default as ``MAX_IMAGES_PER_JOB`` in ``backend/config.py``.
# Pinned at the module level so the strategy and the manager's range
# check share the same constant; the strategy uses this to deliberately
# generate values both inside and outside the valid range.
_MAX_IMAGES = 4

# Distinctive synthetic API key value. Hard-to-collide so a search
# inside any response body would only match an actual leak. The
# :class:`FakeImageBackend` stores this on its instance but never
# echoes it — the property under test is that the marker never crosses
# the wire under any of the exercised scenarios.
_SYNTHETIC_API_KEY = "SyntheticApiKeyValue_PROPERTY1_DO_NOT_LEAK_42x9"

# Substrings that MUST NOT appear in any response body, case-insensitive.
# Sourced verbatim from the design's "Property 1" definition and the
# task instructions for 13.2.
_FORBIDDEN_SUBSTRINGS: tuple[str, ...] = (
    _SYNTHETIC_API_KEY,
    "IMAGE_GEN_PROVIDER",
    "OPENAI_API_KEY",
    "MAX_IMAGES_PER_JOB",
    "MAX_CONCURRENT_IMAGE_JOBS_PER_USER",
    "MAX_REFERENCE_IMAGE_SIZE_MB",
    "API key",
    "README",
    "Traceback",
    'File "',
)

_SCENARIOS = (
    "capability",
    "valid_submit",
    "invalid_submit_image_count",
    "invalid_submit_section_indices",
    "invalid_submit_no_subtitles",
    "reference_upload_bad_content_type",
    "reference_upload_bad_extension",
    "reference_upload_oversized",
    "failed_job_status",
)


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _InMemoryStorage(StorageBackend):
    """Minimal in-memory ``StorageBackend`` for the test app.

    The reference-upload route writes bytes via ``save_file`` on the
    success path; the worker writes candidate bytes on the success path
    too. None of the forbidden substrings flow through storage, so the
    success-path mutations are fine. Tests never read these bytes back
    via load/get_file_url through HTTP — the media route isn't part of
    Property 1's scope.
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
    """Stub project-service that returns a single canned project.

    The image-jobs and capability routers don't need a full
    ``ProjectService`` for this property — they only call
    ``get_project(project_id)`` via the shared ``_load_owned_project``
    helper. ``owner_id`` matches the test's caller so ownership-check
    passes uniformly across all scenarios.
    """

    def __init__(self, project: ProjectState) -> None:
        self._project = project

    async def get_project(self, project_id: str) -> ProjectState:
        if project_id != self._project.id:
            raise ProjectNotFoundError(f"Project {project_id} not found")
        return self._project


def _make_subtitle(i: int) -> SubtitleSegment:
    """Build a placeholder subtitle so the project has a non-empty list.

    Each segment occupies a half-second window starting at ``i`` so
    ``SubtitleSegment.validate_timing`` (``start_time < end_time``)
    holds for every index ``i >= 0``.
    """
    return SubtitleSegment(
        id=f"seg-{i}",
        text=f"Line {i}",
        start_time=float(i),
        end_time=float(i) + 0.5,
        position=Position(x=0.5, y=0.85),
        style=SubtitleStyle(),
    )


def _make_project(*, with_subtitles: bool) -> ProjectState:
    """Construct a deterministic :class:`ProjectState` for the stub service.

    ``owner_id`` matches ``_OWNER_ID`` so :func:`verify_project_ownership`
    always passes — Property 1 is about response bodies, not ownership
    (Property 6 covers ownership separately). The subtitles list shape is
    a constructor flag because invalid-section-indices vs no-subtitles
    branches need different starting states.
    """
    return ProjectState(
        id=_PROJECT_ID,
        owner_id=_OWNER_ID,
        title="Response Safety Test",
        story_text="A short story for response-safety-property testing.",
        pipeline_progress=PipelineProgress(stage="narration", message="Queued"),
        subtitles=[_make_subtitle(i) for i in range(3)] if with_subtitles else [],
        created_at="2025-01-01T00:00:00+00:00",
        updated_at="2025-01-01T00:00:00+00:00",
    )


def _assert_operator_opaque(scenario: str, status_code: int, body_text: str) -> None:
    """Assert the response body contains none of the forbidden substrings.

    The check is case-insensitive (lower-casing both sides) so any
    casing of an env var name or a credential would still surface as a
    leak. The first forbidden hit short-circuits with a verbose
    assertion that names the scenario, status, and matched substring so
    a Hypothesis shrink lands on a small repro that pinpoints the leak.
    """
    haystack = body_text.lower()
    for needle in _FORBIDDEN_SUBSTRINGS:
        assert needle.lower() not in haystack, (
            f"Operator-opaque property violated by scenario={scenario!r} "
            f"(status={status_code}): forbidden substring {needle!r} "
            f"found in response body {body_text!r}"
        )


# ---------------------------------------------------------------------------
# Hypothesis strategy
# ---------------------------------------------------------------------------


@st.composite
def _scenario(draw) -> dict:
    """Draw a scenario descriptor.

    Every scenario tag in :data:`_SCENARIOS` is drawn uniformly; the
    test body dispatches on the tag and uses any extra fields drawn here
    as inputs to that scenario. Hypothesis's shrinker can therefore pin
    a leak to a specific scenario kind plus the smallest reproducing
    payload.

    For ``invalid_submit_image_count`` the strategy draws an
    out-of-range integer ``image_count`` so the JobManager's range check
    raises :class:`ImageJobValidationError` and the router maps it to
    422. For ``invalid_submit_section_indices`` the strategy draws an
    indices triple guaranteed to violate
    ``0 <= start <= end < subtitles_len``. For ``failed_job_status`` the
    strategy doesn't need extra inputs — the auth-failure flow is
    deterministic given the fake's ``simulate_auth_failure`` toggle.
    """
    tag = draw(st.sampled_from(_SCENARIOS))

    # Out-of-range image_count for the invalid-submit-image_count branch.
    # The strategy avoids the [1, MAX_IMAGES_PER_JOB] interval so the
    # router's mapping to 422 is exercised reliably.
    bad_image_count = draw(
        st.one_of(
            st.integers(min_value=-100, max_value=0),
            st.integers(min_value=_MAX_IMAGES + 1, max_value=200),
        )
    )

    # Bad section-indices triple. The project under test has 3
    # subtitles, so the strategy draws indices outside ``[0, 2]`` or
    # with ``start > end`` so the JobManager's section-validation check
    # raises.
    bad_start = draw(st.integers(min_value=-3, max_value=10))
    bad_end = draw(st.integers(min_value=-3, max_value=10))

    # Reference upload payload. The body size is small (≤ 64 bytes) for
    # the bad-content-type / bad-extension cases, since those hit the
    # 422 short-circuit before the size check. The oversized case
    # picks a body just over the configured cap (1 MB → 1 MiB + 1).
    ref_extension_bad = draw(
        st.sampled_from([".gif", ".txt", ".bmp", ""])
    )
    ref_content_type_bad = draw(
        st.sampled_from(
            ["application/octet-stream", "text/plain", "image/gif", ""]
        )
    )

    # Valid submit body parameters for the valid-submit and
    # failed-job-status scenarios.
    valid_image_count = draw(st.integers(min_value=1, max_value=_MAX_IMAGES))
    valid_kind = draw(st.sampled_from(["whole_video", "section"]))

    return {
        "tag": tag,
        "bad_image_count": bad_image_count,
        "bad_start": bad_start,
        "bad_end": bad_end,
        "ref_extension_bad": ref_extension_bad,
        "ref_content_type_bad": ref_content_type_bad,
        "valid_image_count": valid_image_count,
        "valid_kind": valid_kind,
    }


# ---------------------------------------------------------------------------
# Property 1: Capability and error responses are operator-opaque
# ---------------------------------------------------------------------------
# Feature: ai-background-generation, Property 1: Capability and error responses are operator-opaque
# Validates: Requirements 1.4, 1.7, 5.4


@given(scenario=_scenario())
@hyp_settings(max_examples=100, deadline=None)
def test_responses_never_leak_operator_facing_strings(
    scenario: dict,
) -> None:
    """Feature: ai-background-generation, Property 1: Capability and error responses are operator-opaque

    Validates: Requirements 1.4, 1.7, 5.4

    For any request to the capability endpoint, and for any response
    produced by the image-jobs router (job submission, job status
    including failed jobs, reference upload errors), the JSON payload
    SHALL NOT contain any of the forbidden substrings (case-insensitive):
    a synthetic API key value injected into the bound
    :class:`FakeImageBackend`, the strings ``IMAGE_GEN_PROVIDER``,
    ``OPENAI_API_KEY``, ``MAX_IMAGES_PER_JOB``,
    ``MAX_CONCURRENT_IMAGE_JOBS_PER_USER``,
    ``MAX_REFERENCE_IMAGE_SIZE_MB``, ``API key``, ``README``,
    ``Traceback``, or ``File "``.
    """
    tag: str = scenario["tag"]

    async def _run() -> None:
        # Use a per-example isolated CapabilityState so the
        # ``failed_job_status`` scenario's flip never bleeds into other
        # examples in this test (or other tests in the process). The
        # JobManager and capability router both consult this instance.
        # We pass the same ``CapabilityState`` to both via dependency
        # overrides indirectly: the capability router imports the
        # module-level singleton, so we reset that singleton here for
        # consistency, and we *also* inject our isolated instance into
        # the JobManager. To make the capability route observe the same
        # flip, the test uses the module-level singleton (resetting it
        # at the start) and lets the JobManager default to it as well.
        capability_state.reset()

        # Per-example app + manager so FastAPI dependency overrides stay
        # scoped to this example and the JobManager registry never
        # carries leftovers across draws.
        app = FastAPI()
        # Both routers must be mounted: the capability route lives on
        # ``capability_router`` and the four image-jobs routes live on
        # ``image_jobs_router``. Their prefixes are disjoint so route
        # matching is unambiguous.
        app.include_router(image_jobs_router)
        app.include_router(capability_router)

        test_settings = Settings()
        # Bypass auth — the test owns the ``get_owner_id`` override so
        # the auth middleware never runs. ``DISABLE_AUTH`` is also
        # flipped for defense in depth.
        test_settings.DISABLE_AUTH = True
        test_settings.LOCAL_OWNER_ID = _OWNER_ID
        test_settings.MAX_IMAGES_PER_JOB = _MAX_IMAGES
        # Generous cap so back-to-back submits across 100 examples don't
        # trip Property 7's per-user concurrency limit.
        test_settings.MAX_CONCURRENT_IMAGE_JOBS_PER_USER = 1000
        # Pin the reference image cap to a small integer so the
        # oversized scenario can produce a body just over the cap
        # without massive per-example allocations.
        test_settings.MAX_REFERENCE_IMAGE_SIZE_MB = 1

        # Different scenarios need different project shapes. The
        # no-subtitles scenario must start with an empty subtitles list
        # so the JobManager's "subtitles required for section" branch
        # fires. Every other scenario can use a 3-subtitle project.
        project = _make_project(
            with_subtitles=(tag != "invalid_submit_no_subtitles"),
        )
        storage = _InMemoryStorage()

        # Inject the synthetic API key marker into the fake. The fake
        # records it on the instance but never echoes it — Property 1
        # asserts the marker never appears in any response body even
        # when scenarios deliberately push the routers into error
        # responses.
        fake_backend = FakeImageBackend(api_key_marker=_SYNTHETIC_API_KEY)

        # The ``failed_job_status`` scenario configures the fake to
        # raise ``ProviderAuthenticationError`` so a submitted job
        # transitions to ``status="failed"``. The auth-failure path also
        # flips the capability flag off, so this scenario must be the
        # only one in this example to enable the flag — and the
        # capability scenario reads its result before any other code
        # touches the flag (the capability scenario runs first in the
        # dispatch and exits without further routing).
        if tag == "failed_job_status":
            fake_backend.simulate_auth_failure = True

        stub_project_service = _StubProjectService(project)
        manager = JobManager(
            storage=storage,
            project_service=stub_project_service,  # type: ignore[arg-type]
            settings=test_settings,
            backend=fake_backend,
            # Use an isolated CapabilityState so the capability route's
            # singleton view stays clean — the route reads the
            # module-level singleton, so flipping a private instance
            # off doesn't pollute it. This keeps the per-example state
            # truly isolated.
            capability_state=CapabilityState(),
        )

        app.dependency_overrides[get_owner_id] = lambda: _OWNER_ID
        app.dependency_overrides[get_settings] = lambda: test_settings
        app.dependency_overrides[get_auth_settings] = lambda: test_settings
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
                if tag == "capability":
                    resp = await client.get("/image-generation/capability")
                    _assert_operator_opaque(tag, resp.status_code, resp.text)

                elif tag == "valid_submit":
                    if scenario["valid_kind"] == "whole_video":
                        target = {"kind": "whole_video"}
                    else:
                        target = {
                            "kind": "section",
                            "start_index": 0,
                            "end_index": 0,
                        }
                    body = {
                        "prompt": "a moonlit forest with fog",
                        "image_count": scenario["valid_image_count"],
                        "target": target,
                    }
                    resp = await client.post(
                        f"/projects/{_PROJECT_ID}/image-jobs",
                        json=body,
                    )
                    _assert_operator_opaque(tag, resp.status_code, resp.text)

                elif tag == "invalid_submit_image_count":
                    body = {
                        "prompt": "out-of-range image_count test prompt",
                        "image_count": scenario["bad_image_count"],
                        "target": {"kind": "whole_video"},
                    }
                    resp = await client.post(
                        f"/projects/{_PROJECT_ID}/image-jobs",
                        json=body,
                    )
                    _assert_operator_opaque(tag, resp.status_code, resp.text)

                elif tag == "invalid_submit_section_indices":
                    # Force an indices triple that the JobManager's
                    # section-validation check rejects (the project
                    # has 3 subtitles). If the strategy happened to
                    # draw a valid triple, we explicitly mutate it
                    # to be invalid so this scenario reliably hits
                    # the 422 path. ``end_index = 99`` is always
                    # out of range for a 3-subtitle project.
                    start = scenario["bad_start"]
                    end = scenario["bad_end"]
                    if 0 <= start <= end < 3:
                        end = 99
                    body = {
                        "prompt": "bad-section-indices test prompt",
                        "image_count": 1,
                        "target": {
                            "kind": "section",
                            "start_index": start,
                            "end_index": end,
                        },
                    }
                    resp = await client.post(
                        f"/projects/{_PROJECT_ID}/image-jobs",
                        json=body,
                    )
                    _assert_operator_opaque(tag, resp.status_code, resp.text)

                elif tag == "invalid_submit_no_subtitles":
                    # Project was constructed with no subtitles, so any
                    # ``section`` target is rejected with 422 by the
                    # JobManager's "subtitles required" check
                    # (Requirement 3.3).
                    body = {
                        "prompt": "no-subtitles test prompt",
                        "image_count": 1,
                        "target": {
                            "kind": "section",
                            "start_index": 0,
                            "end_index": 0,
                        },
                    }
                    resp = await client.post(
                        f"/projects/{_PROJECT_ID}/image-jobs",
                        json=body,
                    )
                    _assert_operator_opaque(tag, resp.status_code, resp.text)

                elif tag == "reference_upload_bad_content_type":
                    # A reference upload with a wrong content-type AND
                    # any extension fails the type check first. We
                    # pair the bad content-type with a valid extension
                    # so the failure is *only* about content-type, but
                    # the route's conjunction check (Requirement 9.3)
                    # rejects with 422 in either case.
                    job_id = await _insert_pending_job(manager, project)
                    files = {
                        "file": (
                            "reference.png",
                            b"\x00" * 16,
                            scenario["ref_content_type_bad"],
                        )
                    }
                    resp = await client.post(
                        f"/projects/{_PROJECT_ID}"
                        f"/image-jobs/{job_id}/reference",
                        files=files,
                    )
                    _assert_operator_opaque(tag, resp.status_code, resp.text)

                elif tag == "reference_upload_bad_extension":
                    job_id = await _insert_pending_job(manager, project)
                    files = {
                        "file": (
                            f"reference{scenario['ref_extension_bad']}",
                            b"\x00" * 16,
                            "image/png",
                        )
                    }
                    resp = await client.post(
                        f"/projects/{_PROJECT_ID}"
                        f"/image-jobs/{job_id}/reference",
                        files=files,
                    )
                    _assert_operator_opaque(tag, resp.status_code, resp.text)

                elif tag == "reference_upload_oversized":
                    # Type and extension are both valid; the only
                    # failing predicate is the byte-size check, so the
                    # route returns 413. The body is ``cap + 1`` bytes
                    # (1 MiB + 1) — just over the threshold so the
                    # rejection path is exercised without massive
                    # allocations.
                    job_id = await _insert_pending_job(manager, project)
                    cap_bytes = test_settings.MAX_REFERENCE_IMAGE_SIZE_MB * 1024 * 1024
                    body_bytes = b"\x00" * (cap_bytes + 1)
                    files = {
                        "file": (
                            "reference.png",
                            body_bytes,
                            "image/png",
                        )
                    }
                    resp = await client.post(
                        f"/projects/{_PROJECT_ID}"
                        f"/image-jobs/{job_id}/reference",
                        files=files,
                    )
                    _assert_operator_opaque(tag, resp.status_code, resp.text)

                elif tag == "failed_job_status":
                    # Submit a job, await the worker so the auth-failure
                    # path runs to completion, then read the status. The
                    # ``error_message`` field is the most likely
                    # response carrier for a leak — the JobManager
                    # categorizes provider exceptions but a future
                    # refactor could accidentally surface raw text. The
                    # response body is checked verbatim against every
                    # forbidden substring.
                    body = {
                        "prompt": "failed-job auth-failure test prompt",
                        "image_count": 1,
                        "target": {"kind": "whole_video"},
                    }
                    submit_resp = await client.post(
                        f"/projects/{_PROJECT_ID}/image-jobs",
                        json=body,
                    )
                    # Submit itself must also be operator-opaque even
                    # when followed by a failed job. The 202 body
                    # carries only ``{job_id, status}`` so this
                    # assertion is cheap insurance.
                    _assert_operator_opaque(
                        tag + ":submit",
                        submit_resp.status_code,
                        submit_resp.text,
                    )
                    job_id = submit_resp.json()["job_id"]
                    # Await the worker task so the auth-failure path
                    # finishes before the status read. With the fake's
                    # ``simulate_auth_failure`` set, the worker raises
                    # ``ProviderAuthenticationError`` from
                    # ``generate_candidates``, the JobManager catches
                    # it and marks the job ``failed``.
                    task = manager._tasks.get(job_id)
                    if task is not None:
                        try:
                            await asyncio.wait_for(task, timeout=5.0)
                        except asyncio.TimeoutError:
                            raise AssertionError(
                                f"Worker task for job {job_id} did not "
                                f"complete within 5s"
                            )
                    status_resp = await client.get(
                        f"/projects/{_PROJECT_ID}/image-jobs/{job_id}",
                    )
                    _assert_operator_opaque(
                        tag,
                        status_resp.status_code,
                        status_resp.text,
                    )

                else:  # pragma: no cover — defensive; strategy is closed
                    raise AssertionError(f"unknown scenario tag: {tag!r}")
        finally:
            # Cancel any worker task spawned by an accepted submit so
            # it does not outlive the per-example event loop. The
            # ``failed_job_status`` scenario already awaits its own
            # task to completion; this drain catches any other tag's
            # leftover (e.g. an accepted ``valid_submit``).
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _insert_pending_job(manager: JobManager, project: ProjectState) -> str:
    """Insert a synthetic ``pending`` job directly into the manager's registry.

    The reference-upload route requires the target job to be in
    ``pending`` state. Going through :meth:`JobManager.submit` would
    spawn a worker task that races to set ``status="running"``. We
    sidestep that race by inserting a synthetic pending job — same
    pattern used by ``test_image_jobs_reference_upload`` — so the
    upload-validation paths fire on a stable state.
    """
    from backend.models.image_jobs import GenerationJob

    job = GenerationJob(
        id="pending-job-for-response-safety",
        project_id=project.id,
        owner_id=_OWNER_ID,
        prompt="reference upload response-safety test",
        image_count=1,
        target=GenerationTarget(kind="whole_video"),
        status="pending",
        created_at="2025-01-01T00:00:00+00:00",
        updated_at="2025-01-01T00:00:00+00:00",
    )
    manager._jobs[job.id] = job
    manager._running_per_owner.setdefault(_OWNER_ID, set()).add(job.id)
    return job.id
