import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { createProject, TitleApiError } from "../api/projects";
import { VoiceSelector } from "../components/VoiceSelector";
import { useToast } from "../components/Toast";
import {
  MAX_TITLE_LENGTH,
  validateTitleShape,
  type TitleErrorCode,
} from "../lib/titleValidation";

const DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural";

/**
 * Map a `TitleErrorCode` returned by the client-side validator (or the
 * backend's shape-check codes) to a user-facing message. The backend
 * `title_duplicate` case uses the server-provided message directly,
 * since uniqueness can only be evaluated server-side.
 */
function titleErrorMessage(code: TitleErrorCode): string {
  switch (code) {
    case "title_required":
    case "title_empty":
      return "Title is required.";
    case "title_too_long":
      return `Title must be at most ${MAX_TITLE_LENGTH} characters.`;
    case "title_control_chars":
      return "Title cannot contain control characters.";
  }
}

/**
 * Project creation page.
 * - Required title input
 * - Large textarea for story text
 * - Upload .txt button (FileReader API, client-side only)
 * - VoiceSelector dropdown
 * - Submit with validation (reject empty/whitespace title and story text)
 */
export function CreateProjectPage() {
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [voice, setVoice] = useState(DEFAULT_VOICE);
  // Track whether the user has interacted with the title field so we
  // don't show a red error before they've had a chance to type.
  const [titleTouched, setTitleTouched] = useState(false);
  // Server-side title error (e.g. `title_duplicate`). Cleared on edit.
  const [titleServerError, setTitleServerError] = useState<string | null>(null);
  const [storyError, setStoryError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();
  const { showToast } = useToast();

  // Live shape validation. `null` means the title is currently valid.
  const titleValidation = validateTitleShape(title);
  const titleShapeError = titleValidation.ok ? null : titleValidation.code;

  // Show the inline title error only after the user has interacted with
  // the field (blurred or attempted to submit) — this avoids a red
  // error on initial render — OR when the server has rejected the
  // title (e.g. duplicate).
  const visibleTitleError: string | null = titleServerError
    ? titleServerError
    : titleTouched && titleShapeError
      ? titleErrorMessage(titleShapeError)
      : null;

  const titleInvalid = titleShapeError !== null;
  const titleEmpty = title.trim().length === 0;
  // Disable submit when the title is empty or fails shape validation.
  // Story text is checked at submit time so the textarea-empty case
  // surfaces an inline error rather than a silently disabled button.
  const submitDisabled = submitting || titleEmpty || titleInvalid;

  function handleFileUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      if (typeof reader.result === "string") {
        setText(reader.result);
        setStoryError(null);
      }
    };
    reader.onerror = () => {
      showToast("Failed to read file");
    };
    reader.readAsText(file);
    // Reset so the same file can be re-selected
    e.target.value = "";
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    // Mark title as touched so any pending shape error becomes visible.
    setTitleTouched(true);
    // Re-check shape on submit — bail out before any network call if
    // the title is empty or otherwise invalid.
    if (!titleValidation.ok) {
      return;
    }
    if (!text.trim()) {
      setStoryError("Story text cannot be empty or whitespace-only.");
      return;
    }
    setStoryError(null);
    setTitleServerError(null);
    setSubmitting(true);
    try {
      const project = await createProject(text, titleValidation.trimmed, voice);
      navigate(`/projects/${project.id}`);
    } catch (err) {
      if (err instanceof TitleApiError && err.field === "title") {
        // Render server-side title errors (notably `title_duplicate`)
        // inline next to the input rather than as a generic toast.
        setTitleServerError(err.message);
      } else {
        const msg =
          err instanceof Error ? err.message : "Failed to create project";
        showToast(msg);
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div style={containerStyle}>
      <div style={headerStyle}>
        <h1>Create a new project</h1>
        <p style={subtitleStyle}>
          Paste a story, pick a voice, and we'll handle narration, subtitles, and video.
        </p>
      </div>

      <form onSubmit={handleSubmit} className="card" style={cardStyle}>
        <div>
          <label htmlFor="project-title">Title</label>
          <input
            id="project-title"
            type="text"
            value={title}
            onChange={(e) => {
              setTitle(e.target.value);
              // Edits clear any prior server error; let live shape
              // validation drive the inline message from here.
              if (titleServerError) setTitleServerError(null);
            }}
            onBlur={() => setTitleTouched(true)}
            placeholder="Give your project a title"
            maxLength={MAX_TITLE_LENGTH * 4}
            aria-invalid={visibleTitleError !== null}
            aria-describedby={
              visibleTitleError ? "project-title-error" : undefined
            }
            aria-required="true"
            required
          />
          {visibleTitleError && (
            <p id="project-title-error" role="alert" style={errorStyle}>
              {visibleTitleError}
            </p>
          )}
        </div>

        <div>
          <label htmlFor="story-text">Story Text</label>
          <textarea
            id="story-text"
            value={text}
            onChange={(e) => {
              setText(e.target.value);
              if (storyError) setStoryError(null);
            }}
            placeholder="Enter or paste your Chinese story text here…"
            rows={10}
          />
          {storyError && (
            <p role="alert" style={errorStyle}>
              {storyError}
            </p>
          )}
        </div>

        <div>
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            onClick={() => fileInputRef.current?.click()}
          >
            Upload .txt file
          </button>
          <input
            ref={fileInputRef}
            type="file"
            accept=".txt,text/plain"
            onChange={handleFileUpload}
            style={{ display: "none" }}
            aria-label="Upload text file"
          />
        </div>

        <div>
          <label htmlFor="voice-select">Narration Voice</label>
          <div id="voice-select">
            <VoiceSelector value={voice} onChange={setVoice} />
          </div>
        </div>

        <div style={footerStyle}>
          <button
            type="submit"
            className="btn btn-primary"
            disabled={submitDisabled}
          >
            {submitting ? "Creating…" : "Create Project"}
          </button>
        </div>
      </form>
    </div>
  );
}

/* ---------- styles ---------- */

const containerStyle: React.CSSProperties = {
  maxWidth: 720,
  margin: "0 auto",
};

const headerStyle: React.CSSProperties = {
  marginBottom: 24,
};

const subtitleStyle: React.CSSProperties = {
  marginTop: 6,
  color: "var(--text)",
  fontSize: 14,
};

const cardStyle: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 20,
  padding: 24,
};

const errorStyle: React.CSSProperties = {
  marginTop: 8,
  color: "var(--danger)",
  fontSize: 13,
};

const footerStyle: React.CSSProperties = {
  display: "flex",
  justifyContent: "flex-end",
  paddingTop: 8,
  borderTop: "1px solid var(--border)",
};
