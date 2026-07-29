# Implementation Plan: AI Background Generation

## Overview

Convert the AI background generation feature design into a series of prompts
for a code-generation LLM that will implement each step with incremental
progress. Each prompt builds on the previous prompts and ends with wiring
things together. There is no hanging or orphaned code that isn't integrated
into a previous step. Tasks focus only on writing, modifying, or testing
backend code.

The plan honors the layered build order:

1. Disabled backend + error types (no abstract-base changes).
2. Settings additions in `backend/config.py`.
3. `ProjectState.section_backgrounds` model addition (default `[]` for
   migration-safe loads).
4. `JobManager` service with the in-memory registry, lock, per-user cap,
   and asyncio task driver.
5. Capability route at `/image-generation/capability`.
6. `image_jobs` router with the four job endpoints.
7. Dependency wiring in `backend/dependencies.py` and FastAPI inclusion in
   `backend/main.py`.
8. Concrete `OpenAIImageBackend` adapter — the only task that touches a
   real provider SDK.
9. Frontend contract documentation (UI work is a follow-up).

The 10 correctness properties from the design are required tests (not
optional). They live in `backend/tests/test_image_jobs_*.py` (and
`test_image_backend_disabled.py` for Property 8) and use the
`FakeImageBackend` fixture introduced in Task 5.

User-facing strings in any task's implementation MUST NOT mention provider
names, environment variable names, README links, or other operator-facing
configuration details. This constraint is reinforced in the relevant tasks
below.

## Tasks

- [x] 1. Add disabled backend and typed error
  - [x] 1.1 Add `ImageGenerationDisabledError` and `DisabledImageBackend`
    - Add the exception class to `backend/models/image_gen.py` (or a sibling
      module `backend/services/image_backends.py`).
    - Implement `DisabledImageBackend(ImageGenerationBackend)` with
      `generate_single`, `generate_sectioned`, `generate_candidates`, and
      `generate_section_candidates` all raising `ImageGenerationDisabledError`
      with a generic message ("Image generation is not configured").
    - Do NOT modify the abstract base `ImageGenerationBackend`. The candidate
      methods live only on concrete adapters; the JobManager uses duck typing
      to call them.
    - The error message MUST NOT contain provider names or env var names.
    - _Requirements: 8.2, 8.4_

  - [x] 1.2 Write property test for `DisabledImageBackend`
    - File: `backend/tests/test_image_backend_disabled.py`.
    - **Property 8: Disabled backend declines all generation calls**
    - **Validates: Requirements 8.2, 8.4**
    - Use Hypothesis to randomize method invocations across all four methods
      (single, sectioned, candidates, section candidates) with arbitrary
      prompts and parameters. Assert every call raises
      `ImageGenerationDisabledError`.
    - Tag the docstring `Feature: ai-background-generation, Property 8: ...`.

- [x] 2. Extend application settings
  - [x] 2.1 Add image-generation settings to `backend/config.py`
    - Add `IMAGE_GEN_PROVIDER: str` (default `""`).
    - Add `MAX_IMAGES_PER_JOB: int` (default `4`).
    - Add `MAX_CONCURRENT_IMAGE_JOBS_PER_USER: int` (default `2`).
    - Add `MAX_REFERENCE_IMAGE_SIZE_MB: int` (default falls back to
      `MAX_UPLOAD_SIZE_MB`).
    - Do NOT add the provider API key as a typed `Settings` attribute; it is
      read once at adapter construction in `dependencies.py` and never stored
      on `Settings`.
    - _Requirements: 2.2, 4.1, 4.2, 7.1, 9.1_

  - [x] 2.2 Unit tests for settings defaults
    - One test per default value. Verify env-var override path for
      `MAX_IMAGES_PER_JOB`.
    - _Requirements: 4.1, 7.1_

- [x] 3. Extend ProjectState with section backgrounds
  - [x] 3.1 Add `SectionBackground` model and `section_backgrounds` field
    - In `backend/models/project.py`, add a `SectionBackground` Pydantic
      model with `start_index: int`, `end_index: int`, `image_url: str`.
    - Add `section_backgrounds: list[SectionBackground] = []` to
      `ProjectState`. The default `[]` makes existing on-disk `state.json`
      files load unchanged (Pydantic supplies the default for missing
      fields).
    - Do NOT touch `background_image`; the whole-video field remains as-is.
    - _Requirements: 3.6_

  - [x] 3.2 Unit test for backwards-compatible state load
    - Construct a `ProjectState` JSON missing `section_backgrounds` and
      verify it parses with `section_backgrounds == []`.
    - _Requirements: 3.6_

- [x] 4. Define image-job data models
  - In a new module `backend/models/image_jobs.py`, define:
    - `JobStatus = Literal["pending", "running", "succeeded", "failed"]`
    - `GenerationTargetKind = Literal["whole_video", "section"]`
    - `GenerationTarget(BaseModel)` with `kind`, optional `start_index`,
      optional `end_index`, and a `model_validator` enforcing index
      presence/absence based on `kind`.
    - `CandidateImage(BaseModel)` with `id`, `url`, `filename`.
    - `GenerationJob(BaseModel)` with the fields listed in the design's
      Data Models section.
  - In a new module `backend/services/image_job_errors.py` (or alongside the
    JobManager in Task 6), define typed exceptions:
    `ImageJobNotFoundError`, `ImageJobConcurrencyError`,
    `ImageJobInvalidStateError`, `ImageJobValidationError`,
    `ImageJobCandidateNotFoundError`, `ProviderAuthenticationError`.
  - These are pure data and exception definitions — no service logic yet.
  - _Requirements: 5.1, 5.2, 5.3_

- [x] 5. Add `FakeImageBackend` test fixture
  - Place `FakeImageBackend` in `backend/tests/_image_fakes.py` (importable
    by every `test_image_jobs_*.py` test module).
  - Implement `ImageGenerationBackend` plus the candidate methods used by
    the worker. Return deterministic byte payloads
    (e.g. `b"fake-image-{i}"`).
  - Expose toggles: `simulate_auth_failure: bool`, recorded `calls` list,
    recorded `last_reference_image: bytes | None`.
  - When `simulate_auth_failure` is set, raise `ProviderAuthenticationError`
    from candidate methods.
  - Add a pytest fixture `fake_image_backend` to `backend/tests/conftest.py`
    that instantiates one per test and overrides the `get_image_backend`
    FastAPI dependency that Task 10 wires up.
  - _Requirements: 8.1, 8.3_

- [x] 6. Implement JobManager service
  - [x] 6.1 Build the JobManager core
    - File: `backend/services/image_job_service.py`.
    - In-memory state guarded by `asyncio.Lock`: `_jobs: dict[str,
      GenerationJob]`, `_running_per_owner: dict[str, set[str]]`,
      `_tasks: dict[str, asyncio.Task]`.
    - Public API: `submit`, `get`, `attach_reference`, `apply_candidate`,
      `delete_job`.
    - `submit` validates `image_count` against `MAX_IMAGES_PER_JOB`,
      validates `target` (whole_video vs section, indices), enforces the
      per-owner concurrency cap under the lock (raises
      `ImageJobConcurrencyError` when exceeded), creates the
      `GenerationJob`, and starts a background `asyncio.Task` that drives
      the configured `ImageGenerationBackend`.
    - The worker:
      - Sets `status=running`.
      - Loads any attached reference image bytes via `StorageBackend`.
      - Calls `generate_candidates` or `generate_section_candidates` on the
        backend (duck-typed; not on the abstract base).
      - Persists each candidate under
        `imgjobs/{job_id}/candidate-{candidate_id}.png` via
        `StorageBackend.save_file`.
      - Resolves URLs via `StorageBackend.get_file_url`.
      - On success: fills `candidates`, sets `status=succeeded`.
      - On failure: sets `status=failed` with a generic
        `error_message` ("AI background generation is currently
        unavailable"). Provider error details are NOT placed in
        `error_message`. They are logged at WARN with a sanitized category
        only.
      - Always releases the per-owner slot in `finally`.
    - `attach_reference` only succeeds while the job is `pending`;
      otherwise raises `ImageJobInvalidStateError` (router maps to 409).
    - `apply_candidate` resolves the candidate URL, then for `whole_video`
      sets `project.background_image = url`; for `section` upserts a
      `SectionBackground` matching the job's `(start_index, end_index)`.
      Persistence flows through `ProjectService.update_project` so
      optimistic concurrency, ownership, and timing validation are
      centralized; the caller passes the project's current `version`.
    - _Requirements: 5.1, 5.2, 5.3, 6.1, 7.1, 7.3_

  - [x] 6.2 Add capability state singleton and auth-failure flip
    - In a new module `backend/services/image_capability_state.py`, expose
      a process-level `CapabilityState` singleton with
      `disable_for_session()` and `is_enabled` accessors.
    - In the JobManager worker, catch `ProviderAuthenticationError`
      specifically: mark the job `failed` with the generic message AND
      call `capability_state.disable_for_session()`.
    - Once disabled, subsequent submissions also receive 503 (because the
      capability gate evaluates to `False`); see Task 9.2 for the gate
      check.
    - _Requirements: 5.5_

  - [x] 6.3 Write property test for per-user concurrency cap
    - File: `backend/tests/test_image_jobs_concurrency.py`.
    - **Property 7: Per-user concurrency cap is honored**
    - **Validates: Requirements 7.2, 7.3**
    - Use Hypothesis to randomize sequences of submit/terminate operations
      for a single owner. Assert that at every observed moment
      `len(running_for_owner) <= MAX_CONCURRENT_IMAGE_JOBS_PER_USER`, that
      submissions exceeding the cap raise `ImageJobConcurrencyError`, and
      that completing a job frees the slot.
    - Use `FakeImageBackend` and a `JobManager` with a small
      `MAX_CONCURRENT_IMAGE_JOBS_PER_USER` (e.g. 2) for tractability.

- [x] 7. Checkpoint — services layer
  - Ensure all tests pass, ask the user if questions arise.

- [x] 8. Capability endpoint
  - [x] 8.1 Implement `GET /image-generation/capability`
    - New module `backend/routers/image_generation.py` with its own
      `APIRouter(prefix="/image-generation", tags=["image-generation"])`.
    - The route depends on `get_owner_id` (auth required, any logged-in
      Owner) and the `ImageGenerationBackend` dependency.
    - Compute `image_generation_enabled` as
      `not isinstance(backend, DisabledImageBackend) and
      capability_state.is_enabled`.
    - Response shape: exactly `{"image_generation_enabled": bool}`. No
      other fields. Reinforce: never include provider name, env var name,
      or any setup hint.
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 8.2_

  - [x] 8.2 Write property test for capability auth-failure flip
    - File: `backend/tests/test_image_jobs_capability.py`.
    - **Property 10: Provider auth failure flips capability for the session**
    - **Validates: Requirement 5.5**
    - Scripted scenario (this property is intentionally not deeply
      randomized per the design's testing strategy): with the bound backend
      enabled, GET capability returns `true`; submit a job whose
      `FakeImageBackend` is configured to raise
      `ProviderAuthenticationError`; wait for the job to reach `failed`;
      assert the next GET capability returns `false`.

- [x] 9. Image-jobs router — submit + validation
  - [x] 9.1 Create router skeleton and shared helpers
    - File: `backend/routers/image_jobs.py`.
    - `APIRouter(prefix="/projects/{project_id}/image-jobs",
      tags=["image-jobs"])`.
    - Add `_load_owned_project` (mirroring the helper in
      `backend/routers/projects.py`) and a `_require_image_generation`
      gate that raises 503 with a generic message when the bound backend
      is disabled OR `capability_state.is_enabled` is `False`.
    - Add an exception → HTTP-status mapping helper that converts
      `ImageJobConcurrencyError → 429`, `ImageJobInvalidStateError → 409`,
      `ImageJobValidationError → 422`, `ImageJobCandidateNotFoundError →
      422`, `ImageGenerationDisabledError → 503`,
      `ProjectNotFoundError → 404`, `VersionConflictError → 409`. Every
      `detail` string is a generic, operator-opaque message.
    - The router MUST NOT import any concrete provider SDK module.
    - _Requirements: 5.1, 5.4, 6.1, 6.2, 8.3, 8.4_

  - [x] 9.2 Implement `POST /projects/{project_id}/image-jobs`
    - Request schema: `{prompt: str, image_count: int = 1, target:
      GenerationTarget}`.
    - Verify ownership; pass control to `JobManager.submit`.
    - Returns 202 with `{job_id, status}`.
    - Maps validation errors via the shared exception mapper. Do not
      reuse the project-title structured error shape.
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 4.1, 4.2, 4.3, 5.1, 6.1, 6.2,
      7.2, 8.4_

  - [x] 9.3 Write property test for submit shape
    - File: `backend/tests/test_image_jobs_submit_shape.py`.
    - **Property 9: Job submission shape**
    - **Validates: Requirement 5.1**
    - Use Hypothesis to generate valid request bodies with the
      `FakeImageBackend`. Assert every response is 202, body has a
      non-empty `job_id`, and the same `job_id` resolves via `GET .../{job_id}`
      with matching `owner_id` and `project_id`.

  - [x] 9.4 Write property test for section index validation
    - File: `backend/tests/test_image_jobs_section_validation.py`.
    - **Property 3: Section target index validation**
    - **Validates: Requirements 3.2, 3.3, 3.4**
    - Randomize `(subtitles_len, start_index, end_index)` triples; assert
      acceptance iff `subtitles_len > 0 and 0 <= start <= end <
      subtitles_len`, else 422.

  - [x] 9.5 Write property test for image_count range
    - File: `backend/tests/test_image_jobs_image_count.py`.
    - **Property 4: image_count range validation**
    - **Validates: Requirements 4.1, 4.3 (and 4.2 omitted-default case)**
    - Randomize integers across a wide range plus the omitted case; assert
      acceptance iff `1 <= image_count <= MAX_IMAGES_PER_JOB`, else 422.

- [x] 10. Image-jobs router — reference upload
  - [x] 10.1 Implement `POST /projects/{project_id}/image-jobs/{job_id}/reference`
    - Multipart `file` field.
    - Validate `Content-Type ∈ {image/png, image/jpeg}` AND filename
      extension `∈ {.png, .jpg, .jpeg}`. Both must pass.
    - Enforce `MAX_REFERENCE_IMAGE_SIZE_MB` byte cap.
    - Save bytes via `StorageBackend.save_file` under
      `imgjobs/{job_id}/reference.{ext}`.
    - Update the in-memory job to record the reference filename through
      `JobManager.attach_reference`.
    - Reject with 409 if the job is no longer `pending`.
    - Error messages must be operator-opaque; reuse the same generic
      copy as the existing `/projects/{id}/background` upload path
      (no env var names).
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 9.3, 9.4, 9.5_

  - [x] 10.2 Write property test for reference upload validation
    - File: `backend/tests/test_image_jobs_reference_upload.py`.
    - **Property 2: Reference image upload validation**
    - **Validates: Requirements 2.2, 2.3, 9.3**
    - Randomize `(content_type, filename_extension, byte_size)` tuples;
      assert 200 iff all three predicates hold; assert 422 on bad
      type/extension; assert 413 on size-only failure.

- [x] 11. Image-jobs router — status read
  - [x] 11.1 Implement `GET /projects/{project_id}/image-jobs/{job_id}`
    - Owner-only.
    - Returns the `GenerationJob` Pydantic instance via Pydantic
      serialization. For `failed` jobs, `error_message` is the generic
      string set by the JobManager (not a provider trace).
    - 404 when the job is unknown OR when the caller is not the project's
      Owner (route through the existing 403/404 helpers; ownership is
      enforced before job lookup).
    - _Requirements: 5.2, 5.3, 5.4, 6.3_

- [x] 12. Image-jobs router — apply candidate
  - [x] 12.1 Implement `POST /projects/{project_id}/image-jobs/{job_id}/apply`
    - Request schema: `{candidate_id: str, version: int}`.
    - Job must be `succeeded`; otherwise 422.
    - `candidate_id` must be present in `job.candidates`; otherwise 422.
    - For `whole_video` target: set `project.background_image = url` and
      leave `section_backgrounds` unchanged.
    - For `section` target: upsert `SectionBackground{start_index,
      end_index, image_url=url}` into `project.section_backgrounds` keyed
      on `(start_index, end_index)`. Leave `background_image` unchanged.
    - Persist via `ProjectService.update_project` so the optimistic
      concurrency check on `version` runs in the existing path; map
      `VersionConflictError → 409`.
    - Returns the updated `ProjectState`.
    - _Requirements: 3.5, 3.6, 4.4, 5.3, 6.1_

  - [x] 12.2 Write property test for apply semantics
    - File: `backend/tests/test_image_jobs_apply.py`.
    - **Property 5: Successful job candidates and apply semantics**
    - **Validates: Requirements 3.5, 3.6, 4.4, 5.3**
    - Randomize `(image_count, target_kind, candidate_index, indices)`
      tuples. Drive a job to `succeeded` via the `FakeImageBackend`.
      Assert: `len(candidates) == image_count`; whole-video apply sets
      `background_image` and preserves `section_backgrounds`; section
      apply upserts a `SectionBackground` and preserves `background_image`.

- [x] 13. Image-jobs router — cross-cutting property tests
  - [x] 13.1 Write property test for ownership enforcement
    - File: `backend/tests/test_image_jobs_authorization.py`.
    - **Property 6: Ownership enforcement on all job-scoped routes**
    - **Validates: Requirements 6.1, 6.2, 6.3, 9.4, 9.5**
    - Randomize `(project_owner, caller, route)` triples where the caller
      is NOT the owner. Assert every call returns 403, regardless of body
      content. Cover all five routes: submit, reference, status, apply,
      and `GET /projects/{P}/media/{any imgjobs/... filename}`.

  - [x] 13.2 Write property test for operator-opaque responses
    - File: `backend/tests/test_image_jobs_response_safety.py`.
    - **Property 1: Capability and error responses are operator-opaque**
    - **Validates: Requirements 1.4, 1.7, 5.4**
    - Randomize across a mix of capability calls, valid submits, invalid
      submits, reference upload errors, and failed-job status reads. For
      each, assert the JSON payload (case-insensitive substring search)
      contains none of: a synthetic API key value injected into
      `FakeImageBackend`, the strings `IMAGE_GEN_PROVIDER`,
      `OPENAI_API_KEY`, `MAX_IMAGES_PER_JOB`,
      `MAX_CONCURRENT_IMAGE_JOBS_PER_USER`, `MAX_REFERENCE_IMAGE_SIZE_MB`,
      `API key`, `README`, `Traceback`, or `File "`.

- [x] 14. Checkpoint — router complete
  - Ensure all tests pass, ask the user if questions arise.

- [x] 15. Wire dependencies and FastAPI app
  - [x] 15.1 Add `_build_image_backend` factory in `backend/dependencies.py`
    - Read `IMAGE_GEN_PROVIDER` (lowercased, trimmed). For `"openai"`, read
      the API key from the matching env var (e.g. `OPENAI_API_KEY`); if
      empty, fall back to `DisabledImageBackend`. Otherwise (unset or
      `"none"` or unknown), use `DisabledImageBackend`.
    - Construct a process-singleton `JobManager` with `storage`,
      `project_service`, `settings`, and the bound backend.
    - Expose `get_image_backend()` and `get_job_manager()` dependency
      providers.
    - Log only `image_generation enabled=<bool>` at startup. NEVER log the
      key, NEVER log the env var name when disabled.
    - _Requirements: 8.1, 8.2, 8.3, 9.1, 9.2_

  - [x] 15.2 Wire routers into `backend/main.py`
    - `app.include_router(image_jobs_router)`.
    - `app.include_router(image_generation_router)` (capability route).
    - No new exception handlers needed; the routers map exceptions inline.
    - _Requirements: 1.1, 5.1_

  - [x] 15.3 Static import-check test for router isolation
    - File: `backend/tests/test_image_jobs_imports.py`.
    - Parse `backend/routers/image_jobs.py` (e.g. via the `ast` module) and
      assert no `import` statement names a known concrete provider SDK
      (e.g. `openai`, `stability_sdk`).
    - _Requirements: 8.3_

  - [x] 15.4 Log-capture safety test
    - File: `backend/tests/test_image_jobs_logging.py`.
    - Drive a single failing job via `FakeImageBackend`. Capture logs via
      `caplog`. Assert no captured record contains the synthetic API key
      value, the strings `OPENAI_API_KEY`, `IMAGE_GEN_PROVIDER`, or
      `API key`.
    - _Requirements: 9.2_

- [x] 16. Implement concrete provider adapter (integration)
  - [x] 16.1 `OpenAIImageBackend`
    - File: `backend/services/image_backends/openai.py`.
    - Class `OpenAIImageBackend(ImageGenerationBackend)`.
    - Constructor takes `api_key: str` (received from the factory in Task
      15.1, which reads the env var). The key MUST NOT be re-read inside
      methods, MUST NOT be logged, and MUST NOT appear in any exception
      message that bubbles to the JobManager.
    - Implement `generate_single`, `generate_sectioned`, plus
      `generate_candidates(prompt, *, image_count, reference_image_bytes)`
      and `generate_section_candidates(prompts, *, image_count,
      reference_image_bytes)`.
    - Map provider 401/403 responses to `ProviderAuthenticationError`.
      Map other provider errors to a generic `RuntimeError("image
      generation failed")` (the JobManager already logs a sanitized
      category and surfaces the generic user-facing string).
    - This is the only task that touches a real provider SDK. If a real
      SDK is not available in the development environment, leave this
      task as the documented integration point — the property tests in
      Tasks 6/9/10/12/13 already pass against `FakeImageBackend`, and the
      end-to-end behavior of the router is exercised through that fake.
    - _Requirements: 2.4, 4.4, 5.5, 8.1, 9.1, 9.2_

- [ ] 17. Document the frontend contract
  - [~] 17.1 Add a frontend-contract reference document
    - File: `backend/routers/image_jobs.py` module docstring (or a
      sibling `docs/image-jobs-contract.md` if the team prefers a doc
      file).
    - Describe the exact HTTP shapes the frontend must honor: capability
      response shape, submit form fields, reference upload (multipart
      `file`), status polling cadence (2s while `pending`/`running`),
      apply-candidate body, and the rule that the frontend flips its
      in-memory `image_generation_enabled` to `false` on a failed job
      with the generic "unavailable" message.
    - Reinforce: the UI MUST NOT display environment variable names,
      provider names, or operator setup hints anywhere — even in error
      banners or developer-facing tooltips.
    - The actual frontend UI work is a follow-up spec and is out of scope
      here.
    - _Requirements: 1.5, 1.6, 1.7_

- [~] 18. Final checkpoint
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for a faster MVP.
  The 10 property tests (Tasks 1.2, 6.3, 8.2, 9.3, 9.4, 9.5, 10.2, 12.2,
  13.1, 13.2) are REQUIRED and are not marked optional.
- Each task references specific requirement IDs for traceability. Property
  test sub-tasks additionally reference the property number from the
  design's Correctness Properties section.
- Checkpoints (Tasks 7, 14, 18) ensure incremental validation at the
  natural service-layer / router-layer / final boundaries.
- Property tests live in `backend/tests/test_image_jobs_*.py` (and
  `test_image_backend_disabled.py` for Property 8) and use the
  `FakeImageBackend` fixture. The Hypothesis profile uses
  `max_examples=100`. Each test docstring carries the
  `Feature: ai-background-generation, Property N: ...` tag for grep-able
  property → test traceability.
- The concrete provider adapter (Task 16) is the only task that imports a
  real provider SDK. The router (Task 9.1 onwards) and JobManager (Task
  6.1) depend only on the abstract `ImageGenerationBackend` plus
  duck-typed candidate methods.
- No task surfaces environment variable names, provider names, or operator
  setup details to end users. This is enforced both by review and by
  Property 1 (Task 13.2).
