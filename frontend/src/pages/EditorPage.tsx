import { useParams } from "react-router-dom";
import { useProject } from "../hooks/useProject";
import { usePlayback } from "../hooks/usePlayback";
import { PipelineProgress } from "../components/PipelineProgress";
import { VideoCanvas } from "../components/VideoCanvas";
import { PreviewPlayer } from "../components/PreviewPlayer";
import { Timeline } from "../components/Timeline";
import { SubtitleStylePanel } from "../components/SubtitleStylePanel";
import { BackgroundUploader } from "../components/BackgroundUploader";
import { ExportPanel } from "../components/ExportPanel";
import { useToast } from "../components/Toast";
import { useUnsavedChanges } from "../components/UnsavedChangesContext";
import { useCallback, useEffect, useMemo, useState } from "react";
import type { Project, SubtitleStyle } from "../types";
import type { StyleScope } from "../components/SubtitleStylePanel";

const STYLE_SCOPE_KEY = "story-video-editor:style-scope";

const API_BASE = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

/** Resolve a media path to a full URL.
 *
 * Backend stores paths like "/projects/{id}/media/file.mp3". We need to
 * prepend the API base only — not the full media path again.
 */
function mediaUrl(project: Project, filename: string | null): string | null {
  if (!filename) return null;
  // Already absolute
  if (/^https?:\/\//i.test(filename)) return filename;
  // Already an API-relative path — just prepend the host
  if (filename.startsWith("/")) return `${API_BASE}${filename}`;
  // Bare filename — assume it lives in this project's media dir
  return `${API_BASE}/projects/${project.id}/media/${filename}`;
}

/**
 * Editor page — shows pipeline progress while processing,
 * then transitions to the editor view when ready.
 */
export function EditorPage() {
  const { id } = useParams<{ id: string }>();
  const {
    project,
    loading,
    error,
    refreshProject,
    applyLocalEdit,
    save,
    isDirty,
    saveStatus,
  } = useProject(id ?? null);
  const playback = usePlayback();
  const { showToast } = useToast();
  const { setDirty } = useUnsavedChanges();
  // Manual override; null means "follow the playhead automatically".
  const [pinnedSubtitleId, setPinnedSubtitleId] = useState<string | null>(null);

  // Style edit scope — "one" affects only the selected subtitle, "all" applies
  // to every subtitle in the project. Persisted across sessions.
  const [styleScope, setStyleScope] = useState<StyleScope>(() => {
    try {
      const v = sessionStorage.getItem(STYLE_SCOPE_KEY);
      return v === "all" ? "all" : "one";
    } catch {
      return "one";
    }
  });
  useEffect(() => {
    try {
      sessionStorage.setItem(STYLE_SCOPE_KEY, styleScope);
    } catch {
      /* ignore */
    }
  }, [styleScope]);

  // Publish dirty state up so the AppShell's nav links can confirm before
  // navigating away.
  useEffect(() => {
    const key = `editor:${id ?? "unknown"}`;
    setDirty(key, isDirty);
    return () => setDirty(key, false);
  }, [id, isDirty, setDirty]);

  // Warn before unloading the page when there are unsaved edits.
  useEffect(() => {
    if (!isDirty) return;
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      // Modern browsers ignore the message and show their own prompt.
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [isDirty]);

  const handleSave = useCallback(async () => {
    const ok = await save();
    if (ok) showToast("Saved");
    else showToast("Failed to save");
  }, [save, showToast]);

  const handlePipelineComplete = useCallback(() => {
    refreshProject();
  }, [refreshProject]);

  // Subtitle currently visible at the playhead (if any).
  const activeAtPlayhead = useMemo(() => {
    if (!project) return null;
    const t = playback.currentTime;
    return project.subtitles.find((s) => s.startTime <= t && t < s.endTime) ?? null;
  }, [project, playback.currentTime]);

  // Whichever subtitle the user has pinned takes priority; otherwise we
  // follow the active subtitle at the current playhead.
  const selectedSubtitle = useMemo(() => {
    if (!project) return null;
    if (pinnedSubtitleId) {
      return project.subtitles.find((s) => s.id === pinnedSubtitleId) ?? null;
    }
    return activeAtPlayhead;
  }, [project, pinnedSubtitleId, activeAtPlayhead]);
  const selectedSubtitleId = selectedSubtitle?.id ?? null;

  const handleSubtitleSelect = useCallback((id: string) => {
    setPinnedSubtitleId(id);
  }, []);

  const handleSubtitleMove = useCallback(
    (subtitleId: string, position: { x: number; y: number }) => {
      if (!project) return;
      const updatedSubs = project.subtitles.map((s) => {
        if (styleScope === "all") return { ...s, position };
        return s.id === subtitleId ? { ...s, position } : s;
      });
      applyLocalEdit({ subtitles: updatedSubs });
    },
    [project, styleScope, applyLocalEdit],
  );

  const handleSubtitleResize = useCallback(
    (subtitleId: string, maxWidth: number) => {
      if (!project) return;
      const updatedSubs = project.subtitles.map((s) => {
        if (styleScope === "all") return { ...s, style: { ...s.style, maxWidth } };
        return s.id === subtitleId
          ? { ...s, style: { ...s.style, maxWidth } }
          : s;
      });
      applyLocalEdit({ subtitles: updatedSubs });
    },
    [project, styleScope, applyLocalEdit],
  );

  const handleStyleChange = useCallback(
    (style: SubtitleStyle) => {
      if (!project || !selectedSubtitleId) return;
      const updatedSubs = project.subtitles.map((s) => {
        // "all" applies the new style to every subtitle so they stay
        // visually in sync; "one" only touches the active subtitle.
        if (styleScope === "all") return { ...s, style };
        return s.id === selectedSubtitleId ? { ...s, style } : s;
      });
      applyLocalEdit({ subtitles: updatedSubs });
    },
    [project, selectedSubtitleId, styleScope, applyLocalEdit],
  );

  /** One-shot: copy the active subtitle's style + position onto every other subtitle. */
  const handleApplyStyleToAll = useCallback(() => {
    if (!project || !selectedSubtitle) return;
    const sourceStyle = selectedSubtitle.style;
    const sourcePosition = selectedSubtitle.position;
    const updatedSubs = project.subtitles.map((s) => ({
      ...s,
      style: sourceStyle,
      position: sourcePosition,
    }));
    applyLocalEdit({ subtitles: updatedSubs });
    showToast(`Applied to all ${updatedSubs.length} subtitles`);
  }, [project, selectedSubtitle, applyLocalEdit, showToast]);

  const handleTimingChange = useCallback(
    (subtitleId: string, startTime: number, endTime: number) => {
      if (!project) return;
      const updatedSubs = project.subtitles.map((s) =>
        s.id === subtitleId ? { ...s, startTime, endTime } : s,
      );
      applyLocalEdit({ subtitles: updatedSubs });
    },
    [project, applyLocalEdit],
  );

  if (!id) {
    return <p style={editorMessageStyle}>No project ID provided.</p>;
  }

  if (loading && !project) {
    return <p style={editorMessageStyle}>Loading project…</p>;
  }

  if (error && !project) {
    return (
      <p style={{ ...editorMessageStyle, color: "var(--danger)" }}>
        Error: {error}
      </p>
    );
  }

  if (!project) {
    return <p style={editorMessageStyle}>Project not found.</p>;
  }

  const status = project.status;

  // Show pipeline progress for pending, processing, or error states
  if (status === "pending" || status === "processing" || status === "error") {
    return <PipelineProgress projectId={id} onComplete={handlePipelineComplete} />;
  }

  // Resolve URLs for audio, background, and the rendered preview.
  // Prefer the latest export over the (now-deprecated) initial preview.mp4.
  const audioUrl = mediaUrl(project, project.audioUrl);
  const bgUrl = mediaUrl(project, project.backgroundImage);
  const baseRendered = mediaUrl(project, project.exportUrl ?? project.videoUrl);
  // Append updatedAt as a cache-buster so re-exports actually reload.
  const renderedUrl = baseRendered
    ? `${baseRendered}${baseRendered.includes("?") ? "&" : "?"}v=${encodeURIComponent(project.updatedAt)}`
    : null;

  // Editor view for ready / exporting / exported states
  return (
    <div style={editorStyles.container}>
      <header style={editorStyles.header}>
        <div style={editorStyles.headerTopRow}>
          <div style={{ display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
            <h1 style={{ margin: 0 }}>{project.title || "Untitled"}</h1>
            <span className={`pill pill-${status}`}>{status}</span>
          </div>

          <div style={editorStyles.saveCluster}>
            <span style={editorStyles.saveStatus}>
              {saveStatus === "saving"
                ? "Saving…"
                : saveStatus === "error"
                  ? "Save failed"
                  : isDirty
                    ? "Unsaved changes"
                    : "All changes saved"}
            </span>
            <button
              className="btn btn-primary btn-sm"
              onClick={handleSave}
              disabled={!isDirty || saveStatus === "saving"}
            >
              {saveStatus === "saving" ? "Saving…" : "Save"}
            </button>
          </div>
        </div>
        <p style={editorStyles.metaLine}>
          {project.subtitles.length} subtitle
          {project.subtitles.length !== 1 ? "s" : ""}
          {project.audioDuration != null && (
            <> · {project.audioDuration.toFixed(1)}s narration</>
          )}
        </p>
      </header>

      <div style={editorStyles.previewGrid}>
        {/* Editable canvas */}
        <section className="card" style={editorStyles.previewCard}>
          <div style={editorStyles.cardHeader}>
            <h3 style={{ margin: 0 }}>Editor preview</h3>
            <span style={editorStyles.cardHint}>
              {styleScope === "all"
                ? "Drag or resize — applies to all subtitles."
                : "Drag or resize the selected subtitle."}
            </span>
          </div>
          <div style={editorStyles.canvasWrap}>
            <VideoCanvas
              subtitles={project.subtitles}
              currentTime={playback.currentTime}
              backgroundImage={bgUrl}
              selectedSubtitleId={selectedSubtitleId}
              onSubtitleSelect={handleSubtitleSelect}
              onSubtitleMove={handleSubtitleMove}
              onSubtitleResize={handleSubtitleResize}
            />
          </div>
          <PreviewPlayer
            audioUrl={audioUrl}
            playback={playback}
            fallbackDuration={project.audioDuration}
          />
        </section>

        {/* Rendered preview video */}
        <section className="card" style={editorStyles.previewCard}>
          <div style={editorStyles.cardHeader}>
            <h3 style={{ margin: 0 }}>Rendered video</h3>
            <span style={editorStyles.cardHint}>
              Updates each time you export.
            </span>
          </div>
          {renderedUrl ? (
            <video
              key={renderedUrl}
              src={renderedUrl}
              controls
              preload="metadata"
              style={editorStyles.video}
            >
              Your browser doesn't support embedded video.
            </video>
          ) : (
            <div style={editorStyles.placeholder}>
              No exported video yet. Click <strong>Export</strong> below to render one.
            </div>
          )}
        </section>
      </div>

      {/* Timeline */}
      <section className="card" style={editorStyles.section}>
        <Timeline
          subtitles={project.subtitles}
          audioDuration={project.audioDuration}
          currentTime={playback.currentTime}
          selectedSubtitleId={selectedSubtitleId}
          onSubtitleSelect={handleSubtitleSelect}
          onTimingChange={handleTimingChange}
          onSeek={playback.seek}
        />
      </section>

      {/* Subtitle style panel — shown when a subtitle is selected */}
      {selectedSubtitle && (
        <section className="card" style={editorStyles.section}>
          <SubtitleStylePanel
            subtitle={selectedSubtitle}
            totalSubtitles={project.subtitles.length}
            scope={styleScope}
            onScopeChange={setStyleScope}
            onStyleChange={handleStyleChange}
            onApplyToAll={handleApplyStyleToAll}
          />
        </section>
      )}

      <div style={editorStyles.sideBySide}>
        <section className="card" style={editorStyles.section}>
          <BackgroundUploader
            projectId={id}
            onBackgroundChange={refreshProject}
          />
        </section>

        <section className="card" style={editorStyles.section}>
          <ExportPanel
            projectId={id}
            projectStatus={status}
            draft={project}
            onExportComplete={refreshProject}
          />
        </section>
      </div>
    </div>
  );
}

const editorMessageStyle: React.CSSProperties = {
  padding: 40,
  textAlign: "center",
  color: "var(--text)",
};

const editorStyles = {
  container: {
    maxWidth: 1100,
    margin: "0 auto",
    display: "flex",
    flexDirection: "column" as const,
    gap: 20,
  },
  header: {
    display: "flex",
    flexDirection: "column" as const,
    gap: 6,
  },
  headerTopRow: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "flex-end",
    gap: 16,
    flexWrap: "wrap" as const,
  },
  saveCluster: {
    display: "flex",
    alignItems: "center",
    gap: 12,
  },
  saveStatus: {
    fontSize: 13,
    color: "var(--text-muted)",
  },
  metaLine: {
    margin: 0,
    color: "var(--text)",
    fontSize: 14,
  },
  previewGrid: {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fit, minmax(420px, 1fr))",
    gap: 16,
  },
  previewCard: {
    padding: 16,
    display: "flex",
    flexDirection: "column" as const,
    gap: 12,
  },
  cardHeader: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "baseline",
    gap: 12,
    flexWrap: "wrap" as const,
  },
  cardHint: {
    color: "var(--text-muted)",
    fontSize: 12,
  },
  canvasWrap: {
    background: "#000",
    borderRadius: "var(--radius-sm)",
    overflow: "hidden",
  },
  video: {
    width: "100%",
    aspectRatio: "16 / 9",
    background: "#000",
    borderRadius: "var(--radius-sm)",
    display: "block",
  } as React.CSSProperties,
  placeholder: {
    aspectRatio: "16 / 9",
    background: "var(--bg-muted)",
    borderRadius: "var(--radius-sm)",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    color: "var(--text-muted)",
    fontSize: 14,
  },
  section: {
    padding: 16,
  },
  sideBySide: {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))",
    gap: 16,
  },
} satisfies Record<string, React.CSSProperties>;
