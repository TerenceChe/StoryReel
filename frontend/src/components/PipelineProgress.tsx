import { useSSE } from "../hooks/useSSE";
import { retryPipeline } from "../api/projects";
import { useToast } from "./Toast";
import { useEffect, useRef, useState } from "react";

import { getApiToken } from "../api/client";

const API_BASE = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

const STAGES = ["narration", "subtitles", "complete"] as const;

const STAGE_LABELS: Record<string, string> = {
  narration: "Generating narration",
  subtitles: "Aligning subtitles",
  complete: "Complete",
};

interface PipelineProgressProps {
  projectId: string;
  onComplete: () => void;
}

export function PipelineProgress({ projectId, onComplete }: PipelineProgressProps) {
  const [sseUrl, setSseUrl] = useState<string | null>(null);

  useEffect(() => {
    getApiToken().then((token) => {
      setSseUrl(`${API_BASE}/projects/${projectId}/status${token ? `?token=${token}` : ""}`);
    });
  }, [projectId]);

  const { stage, message } = useSSE(sseUrl);
  const { showToast } = useToast();
  const [retrying, setRetrying] = useState(false);
  const completedRef = useRef(false);

  const isError = stage === "error";
  const activeIndex = stage ? STAGES.indexOf(stage as (typeof STAGES)[number]) : -1;

  if (stage === "complete" && !completedRef.current) {
    completedRef.current = true;
    queueMicrotask(onComplete);
  }

  async function handleRetry() {
    setRetrying(true);
    try {
      await retryPipeline(projectId);
    } catch {
      showToast("Failed to retry pipeline");
    } finally {
      setRetrying(false);
    }
  }

  return (
    <div style={containerStyle}>
      <div className="card" style={cardStyle}>
        <h2 style={{ marginBottom: 6 }}>Preparing your story…</h2>
        <p style={subtitleStyle}>
          We're generating narration and aligning subtitles. You'll be able
          to preview and edit before exporting the final video.
        </p>

        {!isError && (
          <div style={progressBarTrackStyle} aria-hidden>
            <div style={progressBarFillStyle} />
          </div>
        )}

        <div style={stagesStyle}>
          {STAGES.map((s, i) => {
            if (s === "complete") return null;
            const completed = !isError && activeIndex > i;
            const active = !isError && activeIndex === i;
            return (
              <StageRow
                key={s}
                label={STAGE_LABELS[s]}
                completed={completed}
                active={active}
                index={i}
              />
            );
          })}
        </div>

        {isError && (
          <div style={errorStyle}>
            <p style={{ color: "var(--danger)", marginBottom: 12 }}>
              {message || "Pipeline failed"}
            </p>
            <button
              className="btn btn-primary"
              onClick={handleRetry}
              disabled={retrying}
            >
              {retrying ? "Retrying…" : "Retry"}
            </button>
          </div>
        )}

        {!isError && stage && stage !== "complete" && (
          <p style={messageStyle}>{message || "Working…"}</p>
        )}
      </div>
    </div>
  );
}

function StageRow({
  label,
  completed,
  active,
  index,
}: {
  label: string;
  completed: boolean;
  active: boolean;
  index: number;
}) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
      <StageIcon completed={completed} active={active} index={index} />
      <span
        style={{
          fontSize: 15,
          color: completed
            ? "var(--success)"
            : active
              ? "var(--text-strong)"
              : "var(--text-muted)",
          fontWeight: active ? 500 : 400,
        }}
      >
        {label}
      </span>
    </div>
  );
}

function StageIcon({
  completed,
  active,
  index,
}: {
  completed: boolean;
  active: boolean;
  index: number;
}) {
  const size = 26;
  const baseStyle: React.CSSProperties = {
    width: size,
    height: size,
    borderRadius: "50%",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    flexShrink: 0,
    fontSize: 13,
  };
  if (completed) {
    return (
      <div
        style={{ ...baseStyle, background: "var(--success)", color: "#fff", fontSize: 14 }}
        aria-label={`Step ${index + 1} complete`}
      >
        ✓
      </div>
    );
  }
  if (active) {
    return (
      <div
        style={{ ...baseStyle, position: "relative" }}
        aria-label={`Step ${index + 1} in progress`}
      >
        <span
          style={{
            position: "absolute",
            inset: 0,
            borderRadius: "50%",
            border: "2px solid var(--accent)",
            borderRightColor: "transparent",
            animation: "sv-spin 0.9s linear infinite",
          }}
        />
        <span style={{ color: "var(--accent)", fontWeight: 600, fontSize: 12 }}>
          {index + 1}
        </span>
      </div>
    );
  }
  return (
    <div
      style={{
        ...baseStyle,
        border: "2px solid var(--border-strong)",
        color: "var(--text-muted)",
      }}
      aria-label={`Step ${index + 1} pending`}
    >
      {index + 1}
    </div>
  );
}

const containerStyle: React.CSSProperties = {
  maxWidth: 520,
  margin: "0 auto",
};

const cardStyle: React.CSSProperties = {
  padding: "32px 28px",
};

const subtitleStyle: React.CSSProperties = {
  color: "var(--text)",
  fontSize: 14,
  marginBottom: 24,
};

const stagesStyle: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 16,
};

const progressBarTrackStyle: React.CSSProperties = {
  position: "relative",
  height: 4,
  borderRadius: 2,
  background: "var(--bg-muted)",
  overflow: "hidden",
  marginBottom: 24,
};

const progressBarFillStyle: React.CSSProperties = {
  position: "absolute",
  top: 0,
  bottom: 0,
  width: "40%",
  background:
    "linear-gradient(90deg, transparent, var(--accent), transparent)",
  animation: "sv-indeterminate 1.4s ease-in-out infinite",
};

const errorStyle: React.CSSProperties = {
  marginTop: 24,
  paddingTop: 16,
  borderTop: "1px solid var(--border)",
};

const messageStyle: React.CSSProperties = {
  marginTop: 16,
  paddingTop: 16,
  borderTop: "1px solid var(--border)",
  color: "var(--text-muted)",
  fontSize: 13,
};
