# Requirements Document

## Introduction

This feature builds out the AI background image generation capability that
is currently stubbed in the Story Video Editor. The existing UI exposes a
"Generate AI background" toggle with a static "requires API key" warning,
and the backend defines an `ImageGenerationBackend` abstract interface but
no concrete provider, no API endpoint, and no way to trigger generation.

The API key for the Image_Generation_Provider is provisioned by the
operator of the deployment via backend environment configuration. End
users do not supply, configure, or see the API key. From an Owner's
perspective the feature is either available or unavailable for the
current deployment; configuration is an operator concern, not a user
concern.

This spec extends the existing foundation with three user-facing
capabilities:

1. **Availability signalling** — the frontend can tell whether AI
   image generation is available in the current deployment, so the UI
   can enable or disable the generation controls accordingly, without
   surfacing operator-level configuration details to end users.
2. **Reference image support** — Owners can upload a reference image
   and ask the provider to produce variations or guided generations
   based on that image.
3. **Generation control** — Owners can specify how many candidate
   images to generate and which target (whole-video background, or a
   specific subtitle section) the generated images apply to.

The goals are: (a) make the feature usable by Owners as soon as the
operator has provisioned a key, (b) keep the provider integration
behind the existing `ImageGenerationBackend` abstraction, and (c) treat
the API key as a server-side secret that never crosses the
backend/frontend boundary in either direction.

## Glossary

- **Editor**: The web-based video editor application consisting of a
  frontend UI and a Python/FastAPI backend.
- **Project**: A user's working session containing story text, generated
  audio, subtitle data, styling configuration, and one or more background
  image targets. Each Project has exactly one owner identified by
  `owner_id`.
- **Owner**: The authenticated end user who created a Project and is the
  only principal permitted to read or modify it.
- **Operator**: The party responsible for deploying and running the
  Editor backend. The Operator provisions the Image_Generation_Provider
  API key via backend environment configuration. The Operator is not an
  end user of the application.
- **Image_Generation_Provider**: An external service (for example
  OpenAI Images, Stability AI) that accepts a text prompt, optionally a
  reference image, and returns generated image bytes.
- **Image_Generation_Backend**: The backend-side adapter (an
  implementation of `backend.models.image_gen.ImageGenerationBackend`)
  that translates Editor requests into Image_Generation_Provider calls.
- **Capability_Endpoint**: A backend HTTP endpoint that reports whether
  the Image_Generation_Backend is available to serve requests in the
  current deployment, without exposing any secret values or
  operator-level configuration details.
- **Reference_Image**: An image uploaded by the Owner that is sent to
  the Image_Generation_Provider as visual guidance for generation.
- **Generation_Job**: A single user-initiated request to produce one or
  more candidate background images for a specific Generation_Target,
  identified by a `job_id`.
- **Generation_Target**: The destination a generated image is bound to.
  One of: `whole_video` (the Project's single `background_image`) or
  `section` (a contiguous range of subtitle segments identified by start
  and end segment indices).
- **Candidate_Image**: One image produced by a Generation_Job. A job
  produces between 1 and `MAX_IMAGES_PER_JOB` candidates. The Owner picks
  which candidate (if any) to apply to the Generation_Target.
- **MAX_IMAGES_PER_JOB**: A backend-configured upper bound on the number
  of candidates a single Generation_Job may produce. Default value: 4.
- **MAX_REFERENCE_IMAGE_SIZE_MB**: A backend-configured upper bound on
  the size of an uploaded Reference_Image. Default value: matches
  `MAX_UPLOAD_SIZE_MB` (50 MB).

## Requirements

### Requirement 1: Surface Provider Availability State

**User Story:** As an Owner, I want the Editor to tell me whether AI
image generation is available in this deployment, so that the UI can
enable or disable the generation controls and I know not to attempt a
feature that cannot run.

#### Acceptance Criteria

1. THE Capability_Endpoint SHALL return a JSON response containing a
   boolean field named `image_generation_enabled`.
2. WHEN the Image_Generation_Backend is configured and ready to serve
   requests, THE Capability_Endpoint SHALL set
   `image_generation_enabled` to `true`.
3. WHEN the Image_Generation_Backend is not configured or is otherwise
   not ready to serve requests, THE Capability_Endpoint SHALL set
   `image_generation_enabled` to `false`.
4. THE Capability_Endpoint SHALL NOT include the API key, secret values,
   environment variable names, provider configuration instructions, or
   any other operator-facing setup details in its response.
5. WHEN the Editor loads a Project page, THE Editor SHALL call the
   Capability_Endpoint and cache the result for the lifetime of the
   page session.
6. WHEN `image_generation_enabled` is `false`, THE Editor SHALL disable
   the generation controls in the UI and SHALL display a generic
   message stating that AI background generation is currently
   unavailable.
7. THE Editor SHALL NOT display environment variable names, provider
   configuration instructions, README references, or other
   operator-facing setup details in any user-facing surface.

### Requirement 2: Reference Image Upload

**User Story:** As an Owner, I want to upload a reference image to
guide the AI, so that the generated backgrounds match a visual style I
have in mind.

#### Acceptance Criteria

1. THE Editor SHALL allow the Owner to upload one Reference_Image per
   Generation_Job, in PNG or JPEG format.
2. IF an uploaded Reference_Image exceeds MAX_REFERENCE_IMAGE_SIZE_MB,
   THEN THE Editor SHALL reject the upload with a 413 response and a
   message stating the maximum allowed size.
3. IF an uploaded file is not PNG or JPEG, THEN THE Editor SHALL reject
   the upload with a 422 response and a message stating the supported
   formats.
4. WHERE a Reference_Image is provided for a Generation_Job, THE
   Image_Generation_Backend SHALL pass the Reference_Image to the
   Image_Generation_Provider as input alongside the text prompt.
5. THE Editor SHALL allow a Generation_Job to be submitted without a
   Reference_Image, in which case generation proceeds from the text
   prompt only.
6. WHEN a Generation_Job completes, THE Editor SHALL retain the
   Reference_Image bytes only as long as the job's Candidate_Images are
   retained, and SHALL delete the Reference_Image when the job's
   candidates are deleted.

### Requirement 3: Generation Target Selection

**User Story:** As an Owner, I want to choose where a generated image
will be applied, so that I can produce a single whole-video background
or different visuals for specific subtitle sections.

#### Acceptance Criteria

1. THE Editor SHALL allow the Owner to choose a Generation_Target of
   either `whole_video` or `section` for each Generation_Job.
2. WHEN the Owner chooses `section`, THE Editor SHALL require a start
   segment index and an end segment index, both referring to existing
   entries in the Project's `subtitles` list.
3. IF the Owner chooses `section` but the Project has no subtitles
   yet, THEN THE Editor SHALL reject the Generation_Job with a 422
   response and a message stating that subtitles must be generated
   first.
4. IF the Owner submits a `section` Generation_Target where
   `start_index > end_index`, or where either index is out of range
   for the current `subtitles` list, THEN THE Editor SHALL reject the
   Generation_Job with a 422 response.
5. WHEN the Owner applies a Candidate_Image to a `whole_video`
   Generation_Target, THE Editor SHALL set the Project's
   `background_image` field to the Candidate_Image's URL, replacing any
   prior background.
6. WHEN the Owner applies a Candidate_Image to a `section`
   Generation_Target, THE Editor SHALL associate the Candidate_Image
   with the indicated subtitle section and SHALL preserve any existing
   `whole_video` background as a fallback for sections without an
   assigned image.

### Requirement 4: Generation Count Control

**User Story:** As an Owner, I want to choose how many candidate
images to generate per request, so that I can trade off cost and choice.

#### Acceptance Criteria

1. THE Editor SHALL allow the Owner to specify an integer
   `image_count` for each Generation_Job, with a minimum of 1 and a
   maximum of MAX_IMAGES_PER_JOB.
2. WHEN the Owner does not specify `image_count`, THE Editor SHALL
   default `image_count` to 1.
3. IF the Owner submits a Generation_Job with `image_count` outside
   the range [1, MAX_IMAGES_PER_JOB], THEN THE Editor SHALL reject the
   request with a 422 response stating the allowed range.
4. WHEN a Generation_Job completes successfully, THE Image_Generation_Backend
   SHALL return exactly `image_count` Candidate_Images, each persisted
   under the Project's storage with a stable URL.

### Requirement 5: Generation Job Lifecycle and Progress

**User Story:** As an Owner, I want to see the progress of a
Generation_Job and a clear error if it fails, so that I know whether
to wait, retry, or abandon the request.

#### Acceptance Criteria

1. WHEN the Owner submits a Generation_Job, THE Editor SHALL return a
   202 response containing a `job_id` identifying the job.
2. THE Editor SHALL expose a per-job status endpoint that returns one
   of: `pending`, `running`, `succeeded`, or `failed`.
3. WHEN a Generation_Job reaches `succeeded`, THE Editor SHALL include
   the list of Candidate_Image URLs in the status response.
4. IF a Generation_Job reaches `failed`, THEN THE Editor SHALL include
   a human-readable error message that does not contain credential
   values, environment variable names, provider configuration
   instructions, or raw provider stack traces.
5. IF the Image_Generation_Provider returns an authentication error,
   THEN THE Editor SHALL surface a generic message stating that AI
   background generation is currently unavailable and SHALL set
   `image_generation_enabled` to `false` for the remainder of the page
   session.

### Requirement 6: Authorization and Ownership

**User Story:** As an Owner, I want only my own account to be able to
trigger generation against my Project, so that other users cannot
consume my quota or alter my Project.

#### Acceptance Criteria

1. WHEN the Editor receives a Generation_Job request for a Project,
   THE Editor SHALL verify that the authenticated caller is the
   Project's Owner before accepting the request.
2. IF the authenticated caller is not the Project's Owner, THEN THE
   Editor SHALL reject the request with a 403 response.
3. WHEN the Editor receives a status request for a Generation_Job,
   THE Editor SHALL verify that the authenticated caller is the Owner
   of the Project the job belongs to before returning status.

### Requirement 7: Concurrency and Rate Limits

**User Story:** As an Operator, I want a per-user limit on concurrent
generation jobs, so that one user cannot exhaust provider quota or
backend resources.

#### Acceptance Criteria

1. THE Editor SHALL enforce a per-user limit on concurrent
   Generation_Jobs configured by the backend setting
   `MAX_CONCURRENT_IMAGE_JOBS_PER_USER` (default value: 2).
2. IF an Owner submits a Generation_Job while already at the
   concurrency limit, THEN THE Editor SHALL reject the request with a
   429 response stating the configured limit.
3. WHILE a Generation_Job is in `running` state, THE Editor SHALL
   count the job against its Owner's concurrency budget, and SHALL
   release the slot when the job reaches `succeeded` or `failed`.

### Requirement 8: Provider Abstraction

**User Story:** As a developer, I want the integration to live behind
the existing `ImageGenerationBackend` interface, so that swapping
providers does not require changes to routing or UI code.

#### Acceptance Criteria

1. THE Image_Generation_Backend SHALL implement the existing
   `backend.models.image_gen.ImageGenerationBackend` abstract class.
2. THE Image_Generation_Backend SHALL be selected at backend startup
   based on environment configuration, with a "disabled" backend used
   when no provider is configured.
3. THE Editor's HTTP routing layer SHALL depend only on the
   `ImageGenerationBackend` interface and SHALL NOT reference any
   specific provider's SDK or types.
4. WHEN no provider is configured, THE disabled backend SHALL reject
   any `generate_single` or `generate_sectioned` call with a clear
   error indicating that image generation is not configured.

### Requirement 9: Credential and Reference Image Safety

**User Story:** As an Operator, I want API keys and uploaded
reference images handled safely, so that secrets do not leak and
malicious uploads are rejected.

#### Acceptance Criteria

1. THE Image_Generation_Backend SHALL read its API key only from
   backend environment configuration and SHALL NOT accept it from any
   HTTP request.
2. THE Editor SHALL NOT log the raw API key value at any log level.
3. WHEN the Editor accepts a Reference_Image upload, THE Editor SHALL
   validate the file's declared content type against the file's
   extension and reject the upload if neither indicates PNG or JPEG.
4. THE Editor SHALL store Reference_Image bytes only under the
   Project's owned storage location and SHALL apply the same ownership
   checks used for project media when serving them.
5. THE Editor SHALL NOT include the Reference_Image bytes or URL in
   responses to any user other than the Project's Owner.
