# Requirements Document

## Introduction

This feature evolves the existing Chinese story-to-video CLI tool into a web-based video editor. Users will interact through a browser UI to input story text, generate narrated videos with Chinese subtitles, visually edit subtitle positioning and styling, preview the result, and export the final video. The existing Python pipeline (edge-tts narration, local Whisper subtitle timing, moviepy/Pillow assembly) is preserved and exposed through a backend API.

## Glossary

- **Editor**: The web-based video editor application consisting of a frontend UI and a Python backend API
- **Pipeline**: The existing processing chain that converts story text into a narrated video with subtitles (text → narration → subtitle timing → video assembly)
- **Project**: A user's working session containing story text, generated audio, subtitle data, styling configuration, and background image selection
- **Preview_Player**: The in-browser video preview component that displays the current state of the video without requiring a full export
- **Subtitle_Overlay**: A visual text element rendered on top of the video, representing a single timed subtitle segment
- **Timeline**: A UI component showing the temporal layout of audio and subtitle segments
- **Canvas**: The visual editing area where the video frame is displayed and subtitle overlays can be manipulated
- **Export_Engine**: The backend component that renders the final video file using moviepy/Pillow based on the current project state

## Requirements

### Requirement 1: Story Input and Project Creation

**User Story:** As a user, I want to input or upload Chinese story text through a web interface, so that I can start the video generation process without using the command line.

#### Acceptance Criteria

1. WHEN a user enters story text into the text input area and submits it, THE Editor SHALL create a new Project and initiate the Pipeline processing
2. WHEN a user uploads a .txt file, THE Editor SHALL read the file contents and populate the text input area
3. WHEN a user submits empty or whitespace-only text, THE Editor SHALL reject the submission and display a validation message
4. WHILE the Pipeline is processing, THE Editor SHALL display progress indicators for each stage (narration, subtitle timing, assembly)
5. WHEN the Pipeline completes successfully, THE Editor SHALL load the generated video into the Canvas for editing

### Requirement 2: Video Preview

**User Story:** As a user, I want to preview the generated video in the browser, so that I can see and hear the result before making edits or exporting.

#### Acceptance Criteria

1. WHEN a Project has a generated video, THE Preview_Player SHALL play the video with synchronized audio and subtitle overlays
2. WHEN a user clicks play/pause, THE Preview_Player SHALL toggle playback accordingly
3. WHEN a user seeks to a position on the Timeline, THE Preview_Player SHALL jump to that position and display the corresponding frame and subtitle
4. THE Preview_Player SHALL display the current playback timestamp

### Requirement 3: Subtitle Position Editing

**User Story:** As a user, I want to drag subtitles to reposition them on the video frame, so that I can place text exactly where I want it.

#### Acceptance Criteria

1. WHEN a user drags a Subtitle_Overlay on the Canvas, THE Editor SHALL update the subtitle's x and y position to match the drag destination
2. WHEN a subtitle position is changed, THE Editor SHALL constrain the position so the subtitle remains fully visible within the video frame boundaries
3. WHEN a subtitle position is updated, THE Editor SHALL persist the new position in the Project state and reflect it in subsequent previews and exports

### Requirement 4: Subtitle Style Editing

**User Story:** As a user, I want to change subtitle font size, color, and font family, so that I can customize the look of the text overlays.

#### Acceptance Criteria

1. WHEN a user selects a Subtitle_Overlay, THE Editor SHALL display a style panel with controls for font size, font color, outline color, and font family
2. WHEN a user changes a style property, THE Editor SHALL apply the change to the selected subtitle and update the Canvas in real time
3. WHEN a user applies a style change, THE Editor SHALL persist the updated style in the Project state
4. THE Editor SHALL provide a set of CJK-compatible font options suitable for Chinese text rendering

### Requirement 5: Subtitle Timing Editing

**User Story:** As a user, I want to adjust when subtitles appear and disappear, so that I can fine-tune synchronization with the narration.

#### Acceptance Criteria

1. WHEN a user adjusts the start or end time of a Subtitle_Overlay on the Timeline, THE Editor SHALL update the subtitle timing in the Project state
2. WHEN a subtitle's timing is modified, THE Editor SHALL validate that the start time is less than the end time
3. IF a user sets a subtitle's start time equal to or greater than its end time, THEN THE Editor SHALL reject the change and display a validation message

### Requirement 6: Background Image Support

**User Story:** As a user, I want to upload a custom background image instead of the default black background, so that my videos have more visual appeal.

#### Acceptance Criteria

1. WHEN a user uploads an image file (PNG or JPG), THE Editor SHALL set it as the video background image in the Project
2. WHEN a background image is set, THE Editor SHALL scale the image to fit the video dimensions (1792×1024) and display it on the Canvas
3. WHEN no custom image is provided, THE Editor SHALL use a solid black background as the default

### Requirement 7: Video Export

**User Story:** As a user, I want to export the edited video as an MP4 file, so that I can download and share the final result.

#### Acceptance Criteria

1. WHEN a user triggers an export, THE Export_Engine SHALL render the video using the current Project state including all subtitle positions, styles, timings, and background image
2. WHILE the Export_Engine is rendering, THE Editor SHALL display a progress indicator
3. WHEN the export completes, THE Editor SHALL provide a download link for the rendered MP4 file
4. THE Export_Engine SHALL produce an MP4 file encoded with H.264 video and AAC audio

### Requirement 8: Narration Voice Selection

**User Story:** As a user, I want to choose from available Chinese voice options for the narration, so that I can pick a voice that fits my story.

#### Acceptance Criteria

1. THE Editor SHALL present a list of available Chinese edge-tts voices (including at minimum XiaoxiaoNeural, YunxiNeural, and YunjianNeural)
2. WHEN a user selects a voice before generating narration, THE Pipeline SHALL use the selected voice for narration generation
3. WHEN no voice is explicitly selected, THE Pipeline SHALL default to zh-CN-XiaoxiaoNeural

### Requirement 9: AI Background Image Generation (Opt-In)

**User Story:** As a user, I want to optionally generate AI background images based on my story content, so that my videos have contextually relevant visuals instead of a plain black background.

#### Acceptance Criteria

1. THE Editor SHALL provide an option to generate a single AI background image for the entire video based on the story text
2. THE Editor SHALL provide an option to generate multiple AI background images for different sections of the video, matched to subtitle segment groupings
3. WHEN AI image generation is not enabled or not available, THE Editor SHALL use the default black background
4. WHEN a user selects the AI image generation option, THE Editor SHALL display a placeholder indicating the feature requires an external image generation API key
5. THE Editor SHALL design the image generation interface so that a provider (such as DALL-E) can be integrated in the future without modifying the editing workflow

### Requirement 10: Project State Persistence

**User Story:** As a user, I want my project state to be preserved during my editing session, so that I do not lose work if I navigate away briefly or refresh the page.

#### Acceptance Criteria

1. WHEN a user makes any edit (subtitle position, style, timing, or background image), THE Editor SHALL save the Project state to the backend
2. WHEN a user reloads the page for an active Project, THE Editor SHALL restore the most recent Project state including all edits
3. THE Editor SHALL assign each Project a unique identifier that can be used to reload it

### Requirement 11: Cloud Deployment Readiness

**User Story:** As a developer, I want the application architecture to support straightforward deployment to AWS, so that I can host the editor for remote users without major refactoring.

#### Acceptance Criteria

1. THE Editor SHALL separate frontend static assets from the backend API so that each can be deployed independently
2. THE Editor SHALL use environment variables for all configurable values including API URLs, file storage paths, and service endpoints
3. THE Editor SHALL store generated files (audio, images, video) through a file storage abstraction that can be backed by local disk or cloud object storage (such as S3)
4. THE Editor SHALL expose the backend as a stateless HTTP API so that it can run behind a load balancer or in a container service
5. THE Editor SHALL support configurable CORS settings to allow the frontend to be served from a different origin than the backend API
