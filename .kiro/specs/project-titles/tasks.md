# Implementation Plan: Project Titles

## Overview

Implement user-supplied, validated, owner-scoped unique Project titles. Work proceeds bottom-up: pure validator first (Properties 1–5), then service-layer integration with persistence (Properties 6–11), then HTTP surface, then frontend wiring (Property 12 + examples). Each task references the requirement clauses it covers and, where applicable, the correctness property from `design.md` it implements or validates.

The implementation language is Python for the backend and TypeScript for the frontend, matching the existing codebase.

## Tasks

- [x] 1. Add `Title_Validator` module
  - [x] 1.1 Create `backend/services/title_validator.py` with `MAX_TITLE_LENGTH`, `TitleErrorCode`, `TitleValidationError`, `normalize`, `validate_shape`, `title_key`, and `check_uniqueness`
    - Implement per the signatures in `design.md` § Components and Interfaces / Title_Validator
    - `validate_shape` trims, then checks empty, length > 100 code points, and Unicode category `Cc`; raises `TitleValidationError` with the matching `TitleErrorCode`
    - `title_key` returns `trimmed.casefold()`; `check_uniqueness` skips `self_project_id` and raises `DUPLICATE` on key collision
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 3.1_

  - [x] 1.2 Write property tests for `validate_shape` accept path
    - **Property 1: Valid titles are accepted and trimmed**
    - **Validates: Requirements 1.1, 4.1, 4.5**
    - Hypothesis strategy: `st.text(alphabet=characters(blacklist_categories=("Cc",)), min_size=1, max_size=100)` filtered so `s.strip()` is non-empty and ≤ 100 code points; assert `validate_shape(s) == s.strip()`

  - [x] 1.3 Write property test for empty / whitespace-only rejection
    - **Property 2: Empty or whitespace-only titles are rejected**
    - **Validates: Requirements 1.3, 4.2, 1.2**
    - Strategy: whitespace-only strings (categories `Zs`, `Zl`, `Zp`) plus the empty string; assert raises with `code == title_empty`; separately assert `validate_shape(None)` raises `title_required`

  - [x] 1.4 Write property test for over-length rejection
    - **Property 3: Over-length titles are rejected**
    - **Validates: Requirements 4.3**
    - Strategy: valid alphabet, `min_size=101, max_size=500`; assert raises with `code == title_too_long`

  - [x] 1.5 Write property test for control-character rejection
    - **Property 4: Control characters are rejected**
    - **Validates: Requirements 4.4**
    - Strategy: build a valid base string then insert ≥ 1 char from `characters(whitelist_categories=("Cc",))` at a random index; assert raises with `code == title_control_chars`

  - [x] 1.6 Write property test for comparison key
    - **Property 5: Title comparison key is trim-and-casefold**
    - **Validates: Requirements 3.1**
    - Strategy: pairs of arbitrary strings; assert `title_key(s.strip()) == title_key(t.strip())` iff `s.strip().casefold() == t.strip().casefold()`

- [x] 2. Wire validator into `ProjectService`
  - [x] 2.1 Add `_list_owner_titles(owner_id)` helper to `backend/services/project_service.py`
    - Mirror the directory scan used by `_count_user_projects` / `list_projects`
    - Return `list[tuple[project_id, stored_title]]` for every project owned by `owner_id`
    - _Requirements: 3.5, 6.1_

  - [x] 2.2 Make `title` required on `create_project` and route through the validator
    - Remove the `title or story_text[:50]` fallback; signature becomes `title: str` (no default)
    - Call `validate_shape(title)` then `check_uniqueness(trimmed, self_project_id=None, siblings=await self._list_owner_titles(owner_id))`
    - Persist the trimmed title; existing `MAX_PROJECTS_PER_USER` guard runs first
    - _Requirements: 1.1, 1.3, 1.4, 4.1, 4.2, 4.3, 4.4, 4.5, 3.2, 5.3, 5.5_

  - [x] 2.3 Add `TitleConflictError` and `rename_title(project_id, owner_id, candidate, expected_version)`
    - Load state, verify `owner_id` (raise `ProjectNotFoundError` on mismatch so the router maps to 404), check `version == expected_version` else `VersionConflictError`
    - `validate_shape` then `check_uniqueness` with `self_project_id=project_id`
    - On success: assign trimmed title, increment `version`, set `updated_at = datetime.now(timezone.utc).isoformat()`, persist
    - _Requirements: 2.2, 2.3, 3.2, 3.3, 3.4, 3.5, 6.3, 6.4_

  - [x] 2.4 Validate title in `update_project` (PUT) when it differs from stored
    - When `incoming.title.strip().casefold() != stored.title.strip().casefold()`, run `validate_shape` + `check_uniqueness(self_project_id=project_id, siblings=...)`
    - Always store the trimmed value; do not auto-rewrite stored title when keys match
    - _Requirements: 1.4, 3.2, 3.3, 4.1–4.5, 5.5_

  - [x] 2.5 Property test: duplicates rejected with no side effects
    - **Property 6: Duplicate titles under the same owner are rejected without side effects**
    - **Validates: Requirements 1.4, 2.3, 3.2, 3.3, 5.5**
    - Strategy: generate a list of pre-seeded owner projects with arbitrary stored titles and a candidate that is one of them transformed by random whitespace padding and per-character case flips; capture every state file's bytes before the call, assert the call raises `TitleConflictError`, then assert byte-for-byte equality after

  - [x] 2.6 Property test: self-rename to a key-equivalent variant succeeds
    - **Property 7: Renaming a project to a variant of its own current title succeeds**
    - **Validates: Requirements 3.4**
    - Strategy: stored title `T` (valid by `validate_shape`); generate `s` by adding leading/trailing whitespace and per-character case flips such that `s.strip().casefold() == T.strip().casefold()` and `validate_shape(s)` does not raise; assert `rename_title` succeeds and the post-call `title_key` equals the pre-call key

  - [x] 2.7 Property test: uniqueness scoped per owner
    - **Property 8: Uniqueness is scoped per owner**
    - **Validates: Requirements 3.5**
    - Strategy: two distinct `owner_id`s and a title `T` that passes shape validation; in any order, both owners create or rename to `T` without raising

  - [x] 2.8 Property test: title persistence round-trip
    - **Property 9: Title persistence round-trip**
    - **Validates: Requirements 1.1, 2.2, 6.1, 6.2**
    - Strategy: random sequence of create / rename / list / get operations on a fresh storage backend; after every successful create or rename, every subsequent `get_project` and `list_projects` (until the next successful rename of that project) returns `title == candidate.strip()`

  - [x] 2.9 Property test: rename advances version and timestamp
    - **Property 10: Rename advances version and timestamp**
    - **Validates: Requirements 6.3, 6.4**
    - Strategy: arbitrary valid stored state and arbitrary valid candidate title; assert post-call `version == pre-call version + 1` and `updated_at >= pre-call updated_at` (string compare on ISO-8601 UTC is sufficient)

  - [x] 2.10 Property test: pre-existing stored titles are preserved
    - **Property 11: Pre-existing stored titles are preserved on read-write**
    - **Validates: Requirements 5.1, 5.4**
    - Strategy: write `state.json` directly with arbitrary stored titles (including titles that would now fail `validate_shape` and including duplicate-by-key titles under one owner); call `get_project`, then any non-rename operation that re-persists state (e.g., a no-op `update_project` that keeps `title` identical, a pipeline progress update); assert the stored `title` field is byte-for-byte unchanged

- [x] 3. Checkpoint - validator and service
  - Run `pytest backend/tests` with the new tests; ensure all tests pass, ask the user if questions arise.

- [x] 4. Expose HTTP surface
  - [x] 4.1 Make `title` required on `CreateProjectRequest` in `backend/routers/projects.py`
    - Change `title: str | None = None` to `title: str`
    - Pass through to `project_service.create_project`
    - _Requirements: 1.1, 1.2_

  - [x] 4.2 Add `RenameTitleRequest` schema and `PATCH /projects/{project_id}/title` endpoint
    - Body: `{title: str, version: int}`
    - Resolve project via `_load_owned_project` (404 on missing/not-owned), then call `project_service.rename_title`
    - Return the updated `ProjectState` on success
    - _Requirements: 2.2, 6.3, 6.4_

  - [x] 4.3 Map `TitleValidationError` and `TitleConflictError` to structured responses
    - Register a FastAPI exception handler in `backend/main.py` (or local `HTTPException` raises in the router) producing `{"detail": {"error_code", "field": "title", "message"}}`
    - Status codes per design: `title_duplicate` → 409; all other `TitleErrorCode`s → 422
    - Map Pydantic missing-field errors for `title` on `POST /projects` so they surface as `error_code: title_required` (an exception handler over `RequestValidationError` filtered by `loc`, or a custom validator on `CreateProjectRequest`)
    - _Requirements: 1.2, 1.3, 1.4, 2.3, 3.2, 3.3, 4.2, 4.3, 4.4, 5.5_

  - [x] 4.4 Route `PUT /projects/{id}` title changes through the validator
    - Already covered by 2.4; the router needs to map `TitleValidationError` / `TitleConflictError` raised from `update_project` to the structured responses (same handler as 4.3)
    - _Requirements: 1.4, 3.2, 3.3, 4.1–4.5_

  - [x] 4.5 Example test: missing title on create returns 422
    - **Example E1: Validates Requirement 1.2**
    - `POST /projects` with `{story_text: "故事"}`; assert 422 and response body `detail.error_code == "title_required"`

  - [x] 4.6 Example tests for router error mapping
    - One test per `TitleErrorCode` (`title_empty`, `title_too_long`, `title_control_chars`, `title_duplicate`) on both `POST /projects` and `PATCH /projects/{id}/title`
    - Assert HTTP status and `detail.error_code`
    - _Requirements: 1.3, 1.4, 2.3, 3.2, 3.3, 4.2, 4.3, 4.4_

- [x] 5. Checkpoint - backend HTTP
  - Run `pytest backend/tests`; ensure all tests pass, ask the user if questions arise.

- [x] 6. Frontend API client
  - [x] 6.1 Update `frontend/src/api/projects.ts`
    - Change `createProject` signature to `(storyText: string, title: string, voice?: string)`; `title` is required at the type level
    - Add `renameProject(id: string, title: string, version: number): Promise<Project>` that issues `PATCH /projects/${id}/title`
    - Add a `TitleApiError` class extending `Error` with `code`, `field`, `message`; have the axios error interceptor (or call site) construct it from the structured `detail` body when `field === "title"`
    - _Requirements: 1.1, 2.2_

  - [x] 6.2 Add shared client-side validation in `frontend/src/lib/titleValidation.ts`
    - Export `MAX_TITLE_LENGTH = 100`, a `validateTitleShape(candidate: string)` returning either the trimmed value or an error code matching the backend `TitleErrorCode`
    - Mirrors the backend rules (trim, 1..100 code points, no `Cc`)
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5_

- [x] 7. Frontend `CreateProjectPage`
  - [x] 7.1 Add required title input above the story textarea in `frontend/src/pages/CreateProjectPage.tsx`
    - Live-validate via `validateTitleShape`; display the matching error inline; disable submit while title is invalid
    - On submit, call `createProject(storyText, title, voice)`; on `TitleApiError` with `field === "title"` render the message inline next to the input
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 4.1–4.5_

  - [x] 7.2 Component test: title input and error rendering
    - Render the page; submit with empty title — assert inline error and no network call
    - Submit with valid title — assert `createProject` called with `(storyText, title, voice)`
    - Mock a `TitleApiError({code: "title_duplicate"})` response — assert inline error rendered
    - _Requirements: 1.1, 1.2, 1.3, 1.4_

- [x] 8. Frontend `ProjectListPage` rename control
  - [x] 8.1 Add inline rename control to each project card
    - Add a pencil icon button next to the title; activating it switches the row from `idle` to `editing` with `draft = currentTitle` and focuses the input
    - Save and Cancel buttons; Enter submits, Escape cancels
    - On save: set `mode = "submitting"`, call `renameProject(id, draft, version)`; on success update the row with the returned `title` / `version` / `updatedAt` and return to `idle`; on `TitleApiError` set `mode = "error"` with the message and keep the input open with the user's draft
    - On `409 version_conflict`: refresh the list via `listProjects()` and re-enter `editing` with the latest version
    - _Requirements: 2.1, 2.2, 2.3, 6.1, 6.3, 6.4_

  - [x] 8.2 Implement the in-flight guard
    - While `mode === "submitting"`, the Save button is `disabled` and the Enter handler is a no-op; rows are independent so other rows remain editable
    - _Requirements: 2.4_

  - [x] 8.3 Property test: rename in-flight guard
    - **Property 12: Rename in-flight guard**
    - **Validates: Requirements 2.4**
    - fast-check strategy: render `ProjectListPage` with one row and a controllable mock `renameProject` (a deferred promise); enter rename mode; for any sequence of arbitrary draft strings dispatched while `mode === "submitting"`, assert exactly one network request was made and the row's rename state is unchanged

  - [x] 8.4 Example test: rename input pre-fill and focus
    - **Example E2: Validates Requirement 2.1**
    - Render the page with a known title; click the rename control; assert the input value equals the current title and `document.activeElement` is the input

  - [x] 8.5 Component tests for rename success and error paths
    - Successful rename updates the row; duplicate title shows inline error and keeps the input open; version conflict triggers a list refresh and re-enters editing
    - _Requirements: 2.2, 2.3, 3.2, 3.3, 6.3, 6.4_

- [x] 9. Final checkpoint
  - Run `pytest backend/tests` and `npm test --prefix frontend -- --run`; ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for a faster MVP. Core implementation tasks (validator, service, HTTP, frontend) are not optional.
- Each property-based test is its own sub-task (per design § Testing Strategy) and is annotated with the property number and the requirement clauses it validates, placed close to the implementation that satisfies it.
- Properties 1–5 attach to the validator (Task 1); Properties 6–11 attach to the service (Task 2); Property 12 and Examples E1/E2 attach to the routers and frontend.
- No backwards-compatibility data migration is needed: pre-existing titles stay as-is per Requirement 5 and Property 11.

## Workflow Complete

This planning workflow is complete. Open `.kiro/specs/project-titles/tasks.md` and click "Start task" next to any task to begin implementation.
