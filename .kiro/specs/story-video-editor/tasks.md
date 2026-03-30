# Implementation Plan: Story Video Editor

## Overview

This plan converts the existing CLI story-to-video tool into a web application with a FastAPI backend and React + TypeScript frontend. Tasks are ordered to build the backend foundation first, then the frontend, then wire everything together. The existing pipeline modules are refactored to work behind the API.

## Tasks

- [x] 1. Set up project structure and backend foundation
  - [x] 1.1 Create backend project structure with FastAPI
    - Create `backend/` directory with `main.py`, `config.py`, `models.py`, `dependencies.py`
    - Set up FastAPI app with CORS middleware, environment variable configuration (`API_SECRET_KEY`, `DEV_OWNER_ID`, `MAX_PROJECTS_PER_USER`, `MAX_CONCURRENT_PIPELINES_PER_USER`, `MAX_UPLOAD_SIZE_MB`)
    - Implement fail-closed auth startup check (refuse to start if no API_SECRET_KEY is set)
    - Add `backend/requirements.txt` with fastapi, uvicorn, pydantic, python-multipart, sse-starlette, httpx, hypothesis, pytest, pytest-asyncio, plus existing deps (edge-tts, openai-whisper, moviepy, Pillow)
    - _Requirements: 11.1, 11.2, 11.4, 11.5_

  - [x] 1.2 Implement Pydantic data models
    - Create all models from design: `Position`, `SubtitleStyle`, `SubtitleSegment`, `PipelineProgress`, `ProjectState`
    - Use `Literal` types for `stage` and `status` fields
    - Add model-level subtitle timing validator: `start_time < end_time` (Pydantic validator on SubtitleSegment)
    - Note: audio_duration bounds checking belongs in ProjectService.update_project, not in the model
    - Create `ImageGenerationBackend` abstract class with `generate_single` and `generate_sectioned` methods (placeholder for future provider integration)
    - _Requirements: 5.2, 5.3, 9.5_

  - [x] 1.3 Write property tests for data models
    - **Property 5: Subtitle timing validation**
    - **Validates: Requirements 5.2, 5.3**

- [x] 2. Implement storage layer and project service
  - [x] 2.1 Implement StorageBackend abstract class and LocalStorageBackend
    - Create `backend/storage.py` with `StorageBackend` ABC (save_file with AsyncIterator, save_file_from_path default impl using sync I/O — acceptable since pipeline runs in background threads, load_file, get_file_url, delete_project)
    - Implement `LocalStorageBackend` storing files under `./data/projects/{project_id}/`
    - _Requirements: 11.3_

  - [x] 2.2 Implement ProjectService
    - Create `backend/services/project_service.py`
    - Implement CRUD: create_project (UUID generation, owner_id), get_project, update_project (optimistic concurrency version check), delete_project, list_projects (summaries only)
    - In update_project: validate subtitle timing bounds against audio_duration when audio_duration is known (0 <= start_time, end_time <= audio_duration)
    - Store project state as JSON via StorageBackend
    - Enforce MAX_PROJECTS_PER_USER limit on creation
    - _Requirements: 10.1, 10.2, 10.3, 1.1, 5.2, 5.3_

  - [x] 2.3 ~~Implement TTL cleanup background task~~ (Removed — all users are authenticated; users manage their own projects via DELETE)

  - [x] 2.4 Write property tests for ProjectService
    - **Property 4: Project state save/load round trip**
    - **Validates: Requirements 3.3, 4.3, 5.1, 10.1, 10.2**
    - **Property 8: Project ID uniqueness**
    - **Validates: Requirements 10.3**

- [x] 3. Implement authentication middleware
  - [x] 3.1 Create auth middleware
    - Create `backend/auth.py` with pluggable auth middleware
    - Implement Bearer token validation against API_SECRET_KEY
    - Extract owner_id from token/request
    - Add ownership check in project endpoints (return 403 if not owner)
    - All users must be authenticated — no anonymous access
    - _Requirements: 11.2_

- [x] 4. Refactor pipeline for backend integration
  - [x] 4.1 Refactor existing pipeline modules
    - Modify `narration.py` to accept voice parameter and return audio duration
    - Modify `subtitles.py` to return structured segment data (with UUIDs, default positions/styles)
    - Modify `video.py` `_render_text_frame` to accept per-subtitle position and style (normalized → pixel conversion using target resolution 1792×1024)
    - Modify `video.py` `create_video_with_subtitles` to accept ProjectState subtitle list instead of SRT path
    - Add font fallback chain: Noto Sans CJK SC → PingFang → system default
    - _Requirements: 1.1, 3.3, 4.3, 7.1, 8.2_

  - [x] 4.2 Implement PipelineService
    - Create `backend/services/pipeline_service.py`
    - Implement `run_pipeline`: orchestrate narration → subtitles → preview video, update project state at each stage, write files via StorageBackend
    - Implement `export_video`: re-render video using edited project state (positions, styles, timings, background image)
    - Implement `retry_pipeline`: resume from failed stage (narration: rerun from scratch; subtitles: reuse audio; assembly: reuse audio + subtitle data). Only valid when status is "error" (return 422 otherwise). Reset status to "processing"
    - Track pipeline progress for SSE streaming
    - Enforce MAX_CONCURRENT_PIPELINES_PER_USER limit
    - _Requirements: 1.1, 1.4, 7.1, 7.4, 8.2, 8.3_

  - [x] 4.3 Write property tests for pipeline input validation
    - **Property 1: Whitespace text rejection**
    - **Validates: Requirements 1.3**
    - **Property 6: Project creation for valid text**
    - **Validates: Requirements 1.1**
    - **Property 7: Voice selection propagation**
    - **Validates: Requirements 8.2**

- [x] 5. Implement API endpoints
  - [x] 5.1 Implement project CRUD endpoints
    - POST /projects — create project, validate text (reject whitespace-only), start pipeline as background task
    - GET /projects — list user's projects (summaries: id, title, status, createdAt, updatedAt)
    - GET /projects/{id} — get full project state
    - PUT /projects/{id} — update project state with optimistic concurrency check
    - DELETE /projects/{id} — delete project and files
    - _Requirements: 1.1, 1.3, 10.1, 10.2, 10.3_

  - [x] 5.2 Implement SSE and export endpoints
    - GET /projects/{id}/status — SSE stream for pipeline progress (send current state on connect, comment-only keepalive every 15s, close on complete/error)
    - POST /projects/{id}/export — trigger async export, return 202
    - GET /projects/{id}/export/status — SSE stream for export progress
    - GET /projects/{id}/export/download — stream exported MP4 file
    - POST /projects/{id}/retry — retry from failed stage (422 if not in error state)
    - _Requirements: 1.4, 7.1, 7.2, 7.3_

  - [x] 5.3 Implement media and upload endpoints
    - GET /projects/{id}/media/{filename} — serve media files with path traversal validation (reject `..`, `/`, `\` in filename, return 400)
    - POST /projects/{id}/background — upload background image (PNG/JPG validation, MAX_UPLOAD_SIZE_MB limit, return 413 if exceeded)
    - GET /voices — return list of available edge-tts voices
    - _Requirements: 6.1, 6.2, 8.1_

  - [x] 5.4 Write unit tests for API endpoints
    - Test validation errors (empty text, invalid timing, path traversal)
    - Test auth (401 unauthorized, 403 forbidden)
    - Test optimistic concurrency (409 conflict)
    - Test resource limits (429 too many projects, 413 file too large)
    - _Requirements: 1.3, 5.2, 5.3_

- [-] 6. Checkpoint — Backend complete
  - Ensure all backend tests pass, ask the user if questions arise.

- [ ] 7. Set up frontend project
  - [~] 7.1 Initialize React + TypeScript frontend
    - Create `frontend/` with Vite + React + TypeScript
    - Install dependencies: react-konva, konva, axios (or fetch wrapper)
    - Set up API client with configurable base URL from environment variable (VITE_API_URL)
    - Define TypeScript interfaces matching backend models (Project, SubtitleSegment, Position, SubtitleStyle, PipelineProgress)
    - _Requirements: 11.1, 11.2_

  - [~] 7.2 Implement API client, SSE hooks, and error handling infrastructure
    - Create API client module with functions for all backend endpoints
    - Create `useSSE` custom hook for consuming SSE streams (pipeline status, export status). Filter out comment-only keepalive pings. Handle reconnection and current-state-on-connect
    - Create `useProject` hook for project state management (fetch, update with version tracking, handle 409 conflicts by reloading)
    - Set up toast notification system for API errors
    - Add error boundary component for unhandled errors
    - Add connection status indicator for network disconnection
    - _Requirements: 1.4, 10.1, 10.2_

- [ ] 8. Implement project creation flow
  - [~] 8.1 Build project creation page
    - Text input area for story text with submit button
    - Client-side file read (.txt) using FileReader API to populate text area (no backend upload)
    - VoiceSelector dropdown (populated from /voices endpoint, defaults to XiaoxiaoNeural)
    - Client-side validation (reject empty/whitespace text)
    - On submit: call POST /projects, navigate to editor page
    - _Requirements: 1.1, 1.2, 1.3, 8.1, 8.2, 8.3_

  - [~] 8.2 Build pipeline progress view
    - Display progress indicators for each pipeline stage using SSE hook
    - Show stage transitions: narration → subtitles → assembly → complete
    - Handle error state with retry button (calls POST /projects/{id}/retry)
    - On completion: transition to editor view
    - _Requirements: 1.4, 1.5_

- [ ] 9. Implement video editor canvas and preview
  - [~] 9.1 Build VideoCanvas and PreviewPlayer with shared playback state
    - Create a shared `usePlayback` hook that manages current time, play/pause state, and seek position — used by both Canvas and PreviewPlayer
    - VideoCanvas (react-konva): render background image (or black default) as base layer, render subtitle overlays as draggable Konva Text nodes, convert between normalized (0-1) and pixel coordinates, implement drag-to-reposition with boundary clamping, highlight selected subtitle, show/hide subtitles based on current time using half-open interval (start_time <= T < end_time)
    - PreviewPlayer: HTML5 audio element with custom play/pause controls, synchronize playback position via shared hook, display current timestamp, seek support
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 3.1, 3.2, 6.2, 6.3_

  - [~] 9.2 Write property tests for position clamping
    - **Property 3: Position clamping within bounds**
    - **Validates: Requirements 3.2**

  - [~] 9.3 Write property tests for subtitle visibility
    - **Property 2: Visible subtitles at time T**
    - **Validates: Requirements 2.3**

- [ ] 10. Implement subtitle editing panels
  - [~] 10.1 Build SubtitleStylePanel
    - Font size slider (normalized value, display as approximate pixel equivalent)
    - Font color and outline color pickers
    - Font family dropdown with CJK-compatible options (Noto Sans CJK SC, PingFang, etc.)
    - Apply changes in real time to Canvas and persist to project state via PUT
    - _Requirements: 4.1, 4.2, 4.3, 4.4_

  - [~] 10.2 Build Timeline component
    - Horizontal timeline with subtitle segments as blocks
    - Draggable block edges for adjusting start/end times
    - Display overlapping segments as stacked rows
    - Client-side validation (start < end, within audio duration)
    - Persist timing changes via PUT with version tracking
    - _Requirements: 5.1, 5.2, 5.3_

  - [~] 10.3 Build BackgroundUploader and AI image generation placeholder
    - File upload for PNG/JPG with client-side format and size validation
    - Call POST /projects/{id}/background on upload
    - Update Canvas to display uploaded image
    - Add UI toggle for "Generate AI background" with single/multi-section options
    - Display placeholder message indicating API key required when toggle is enabled
    - When not enabled, default to black background (existing behavior)
    - _Requirements: 6.1, 6.2, 9.1, 9.2, 9.3, 9.4_

- [ ] 11. Checkpoint — Editor integration
  - Ensure the editor works end-to-end: create project → pipeline → edit subtitles → preview. Ask the user if questions arise.

- [ ] 12. Implement export and project management
  - [~] 12.1 Build export flow
    - Export button triggers POST /projects/{id}/export
    - Display export progress via SSE stream
    - On completion: show download link (GET /projects/{id}/export/download)
    - _Requirements: 7.1, 7.2, 7.3, 7.4_

  - [~] 12.2 Build project list page
    - Fetch and display user's projects from GET /projects
    - Show project title, status, creation date
    - Click to open project in editor
    - Delete project option
    - _Requirements: 10.3_

- [ ] 13. Final checkpoint — Full integration
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 14. Upgrade to JWT-based authentication
  - [~] 14.1 Replace shared-secret auth with JWT/Cognito
    - Replace Bearer token comparison in `backend/auth.py` with JWT validation (e.g., Amazon Cognito or any OIDC provider)
    - Validate tokens against the provider's JWKS endpoint
    - Extract `owner_id` from the JWT `sub` claim instead of the `X-Owner-Id` header
    - Add environment variables for JWT issuer URL, audience, and JWKS URI
    - Keep Bearer token auth mode for local development (set API_SECRET_KEY in .env)
    - Update tests to cover JWT validation, expired tokens, and invalid signatures
    - _Requirements: 11.2_
