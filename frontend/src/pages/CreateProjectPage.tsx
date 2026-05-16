import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { createProject } from "../api/projects";
import { VoiceSelector } from "../components/VoiceSelector";
import { useToast } from "../components/Toast";

const DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural";

/**
 * Project creation page.
 * - Large textarea for story text
 * - Upload .txt button (FileReader API, client-side only)
 * - VoiceSelector dropdown
 * - Submit with validation (reject empty/whitespace)
 */
export function CreateProjectPage() {
  const [text, setText] = useState("");
  const [voice, setVoice] = useState(DEFAULT_VOICE);
  const [validationError, setValidationError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();
  const { showToast } = useToast();

  function handleFileUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      if (typeof reader.result === "string") {
        setText(reader.result);
        setValidationError(null);
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
    if (!text.trim()) {
      setValidationError("Story text cannot be empty or whitespace-only.");
      return;
    }
    setValidationError(null);
    setSubmitting(true);
    try {
      const project = await createProject(text, voice);
      navigate(`/projects/${project.id}`);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Failed to create project";
      showToast(msg);
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
          <label htmlFor="story-text">Story Text</label>
          <textarea
            id="story-text"
            value={text}
            onChange={(e) => {
              setText(e.target.value);
              if (validationError) setValidationError(null);
            }}
            placeholder="Enter or paste your Chinese story text here…"
            rows={10}
          />
          {validationError && (
            <p role="alert" style={errorStyle}>
              {validationError}
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
            disabled={submitting}
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
