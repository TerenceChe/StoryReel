import { useCallback, useRef, useState } from "react";
import { triggerExport, downloadExport } from "../api/projects";
import { useSSE } from "../hooks/useSSE";
import { useToast } from "./Toast";
import type { Project } from "../types";

import { getApiToken } from "../api/client";

const API_BASE = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

export interface ExportPanelProps {
  projectId: string;
  projectStatus: string;
  /** Current local draft sent to the backend so the export uses unsaved edits. */
  draft: Project | null;
  onExportComplete: () => void;
}

export function ExportPanel({
  projectId,
  projectStatus,
  draft,
  onExportComplete,
}: ExportPanelProps) {
  const { showToast } = useToast();
  const [exporting, setExporting] = useState(false);
  const [sseUrl, setSseUrl] = useState<string | null>(null);
  const [downloadReady, setDownloadReady] = useState(projectStatus === "exported");
  const [downloading, setDownloading] = useState(false);
  const completedRef = useRef(false);

  const sse = useSSE(sseUrl);

  if (sse.stage === "complete" && !completedRef.current) {
    completedRef.current = true;
    queueMicrotask(() => {
      setDownloadReady(true);
      setExporting(false);
      setSseUrl(null);
      onExportComplete();
    });
  }

  const isError = sse.stage === "error";
  if (isError && exporting) {
    queueMicrotask(() => {
      setExporting(false);
      setSseUrl(null);
    });
  }

  const handleExport = useCallback(async () => {
    setExporting(true);
    setDownloadReady(false);
    completedRef.current = false;
    try {
      await triggerExport(projectId, draft);
      const token = await getApiToken();
      setSseUrl(
        `${API_BASE}/projects/${projectId}/export/status${token ? `?token=${token}` : ""}`,
      );
    } catch {
      showToast("Failed to start export");
      setExporting(false);
    }
  }, [projectId, draft, showToast]);

  const handleDownload = useCallback(async () => {
    setDownloading(true);
    try {
      const blob = await downloadExport(projectId);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `export-${projectId}.mp4`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch {
      showToast("Failed to download export");
    } finally {
      setDownloading(false);
    }
  }, [projectId, showToast]);

  return (
    <div>
      <h3 style={{ margin: "0 0 6px" }}>Export</h3>
      <p style={hintStyle}>
        Renders the current draft as MP4 — including any unsaved edits.
      </p>

      <div style={rowStyle}>
        {!exporting && (
          <button className="btn btn-primary" onClick={handleExport}>
            {downloadReady ? "Re-export" : "Export video"}
          </button>
        )}

        {exporting && !isError && (
          <span style={progressStyle}>
            <span className="spinner" />
            <span>{sse.message || "Exporting…"}</span>
          </span>
        )}

        {downloadReady && !exporting && (
          <button
            className="btn btn-secondary"
            onClick={handleDownload}
            disabled={downloading}
          >
            {downloading ? "Downloading…" : "Download MP4"}
          </button>
        )}
      </div>

      {isError && (
        <div style={errorBoxStyle}>
          <p style={{ color: "var(--danger)", fontSize: 13, margin: "0 0 8px" }}>
            {sse.message || "Export failed"}
          </p>
          <button className="btn btn-secondary btn-sm" onClick={handleExport}>
            Retry export
          </button>
        </div>
      )}
    </div>
  );
}

const hintStyle: React.CSSProperties = {
  margin: "0 0 12px",
  color: "var(--text-muted)",
  fontSize: 13,
};

const rowStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 12,
  flexWrap: "wrap",
};

const progressStyle: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 8,
  color: "var(--text)",
  fontSize: 13,
};

const errorBoxStyle: React.CSSProperties = {
  marginTop: 12,
  padding: 10,
  borderRadius: "var(--radius-sm)",
  background: "rgba(220, 38, 38, 0.08)",
  border: "1px solid rgba(220, 38, 38, 0.3)",
};
