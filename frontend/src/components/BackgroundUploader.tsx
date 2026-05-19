import { useCallback, useRef, useState } from "react";
import { uploadBackground } from "../api/projects";
import { useToast } from "./Toast";

const MAX_FILE_SIZE_MB = 50;
const MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024;
const ACCEPTED_TYPES = ["image/png", "image/jpeg"];

export interface BackgroundUploaderProps {
  projectId: string;
  onBackgroundChange: () => void;
}

export function BackgroundUploader({
  projectId,
  onBackgroundChange,
}: BackgroundUploaderProps) {
  const { showToast } = useToast();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [aiEnabled, setAiEnabled] = useState(false);
  const [aiMode, setAiMode] = useState<"single" | "multi">("single");

  const handleFileChange = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (!file) return;

      if (!ACCEPTED_TYPES.includes(file.type)) {
        showToast("Invalid file format. Please upload a PNG or JPG image.");
        if (fileInputRef.current) fileInputRef.current.value = "";
        return;
      }

      if (file.size > MAX_FILE_SIZE_BYTES) {
        showToast(`File too large. Maximum size is ${MAX_FILE_SIZE_MB}MB.`);
        if (fileInputRef.current) fileInputRef.current.value = "";
        return;
      }

      setUploading(true);
      try {
        await uploadBackground(projectId, file);
        onBackgroundChange();
        showToast("Background uploaded");
      } catch {
        showToast("Failed to upload background image.");
      } finally {
        setUploading(false);
        if (fileInputRef.current) fileInputRef.current.value = "";
      }
    },
    [projectId, onBackgroundChange, showToast],
  );

  return (
    <div>
      <h3 style={{ margin: "0 0 6px" }}>Background</h3>
      <p style={hintStyle}>
        Custom image behind the subtitles. Defaults to solid black.
      </p>

      <div style={uploadRowStyle}>
        <label className={`btn btn-primary btn-sm ${uploading ? "disabled" : ""}`}>
          {uploading ? "Uploading…" : "Upload image"}
          <input
            ref={fileInputRef}
            type="file"
            accept="image/png,image/jpeg"
            onChange={handleFileChange}
            disabled={uploading}
            style={{ display: "none" }}
            aria-label="Upload background image"
          />
        </label>
        <span style={hintStyle}>
          PNG or JPG, max {MAX_FILE_SIZE_MB}MB
        </span>
      </div>

      <div style={aiBlockStyle}>
        <label style={checkboxRowStyle}>
          <input
            type="checkbox"
            style={checkboxStyle}
            checked={aiEnabled}
            onChange={(e) => setAiEnabled(e.target.checked)}
            aria-label="Enable AI background generation"
          />
          <span>Generate AI background</span>
        </label>

        {aiEnabled && (
          <div style={aiOptionsStyle}>
            <label style={radioRowStyle}>
              <input
                type="radio"
                name="ai-bg-mode"
                value="single"
                style={radioStyle}
                checked={aiMode === "single"}
                onChange={() => setAiMode("single")}
              />
              <span>Single image for entire video</span>
            </label>
            <label style={radioRowStyle}>
              <input
                type="radio"
                name="ai-bg-mode"
                value="multi"
                style={radioStyle}
                checked={aiMode === "multi"}
                onChange={() => setAiMode("multi")}
              />
              <span>Multiple images per section</span>
            </label>
            <p style={warnStyle}>
              Requires an external image-generation API key. Configure it in
              your environment settings.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

const hintStyle: React.CSSProperties = {
  margin: 0,
  color: "var(--text-muted)",
  fontSize: 12,
};

const uploadRowStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 12,
  marginTop: 12,
  marginBottom: 12,
  flexWrap: "wrap",
};

const aiBlockStyle: React.CSSProperties = {
  paddingTop: 12,
  borderTop: "1px solid var(--border)",
};

const checkboxRowStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  fontSize: 13,
  color: "var(--text)",
  margin: 0,
  cursor: "pointer",
};

const checkboxStyle: React.CSSProperties = {
  width: "auto",
  margin: 0,
  padding: 0,
};

const aiOptionsStyle: React.CSSProperties = {
  marginTop: 8,
  paddingLeft: 24,
};

const radioRowStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 6,
  fontSize: 13,
  color: "var(--text)",
  marginBottom: 4,
  cursor: "pointer",
};

const radioStyle: React.CSSProperties = {
  width: "auto",
  margin: 0,
  padding: 0,
};

const warnStyle: React.CSSProperties = {
  marginTop: 8,
  marginBottom: 0,
  fontSize: 12,
  color: "var(--warning)",
  background: "rgba(217, 119, 6, 0.1)",
  border: "1px solid rgba(217, 119, 6, 0.3)",
  padding: "6px 10px",
  borderRadius: "var(--radius-sm)",
};
