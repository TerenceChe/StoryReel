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

      // Client-side format validation
      if (!ACCEPTED_TYPES.includes(file.type)) {
        showToast("Invalid file format. Please upload a PNG or JPG image.");
        if (fileInputRef.current) fileInputRef.current.value = "";
        return;
      }

      // Client-side size validation
      if (file.size > MAX_FILE_SIZE_BYTES) {
        showToast(`File too large. Maximum size is ${MAX_FILE_SIZE_MB}MB.`);
        if (fileInputRef.current) fileInputRef.current.value = "";
        return;
      }

      setUploading(true);
      try {
        await uploadBackground(projectId, file);
        onBackgroundChange();
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
    <div
      style={{
        marginTop: 16,
        padding: 12,
        background: "#1e1e1e",
        borderRadius: 8,
        border: "1px solid #333",
      }}
    >
      <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 8 }}>
        Background
      </div>

      {/* File upload */}
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
        <label
          style={{
            padding: "6px 12px",
            background: uploading ? "#555" : "#2563eb",
            color: "#fff",
            borderRadius: 4,
            cursor: uploading ? "not-allowed" : "pointer",
            fontSize: 13,
          }}
        >
          {uploading ? "Uploading…" : "Upload Image"}
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
        <span style={{ fontSize: 12, color: "#888" }}>PNG or JPG, max {MAX_FILE_SIZE_MB}MB</span>
      </div>

      {/* AI generation toggle */}
      <div style={{ borderTop: "1px solid #333", paddingTop: 10 }}>
        <label style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer", fontSize: 13 }}>
          <input
            type="checkbox"
            checked={aiEnabled}
            onChange={(e) => setAiEnabled(e.target.checked)}
            aria-label="Enable AI background generation"
          />
          Generate AI background
        </label>

        {aiEnabled && (
          <div style={{ marginTop: 8, marginLeft: 24 }}>
            <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, marginBottom: 4, cursor: "pointer" }}>
              <input
                type="radio"
                name="ai-bg-mode"
                value="single"
                checked={aiMode === "single"}
                onChange={() => setAiMode("single")}
              />
              Single image for entire video
            </label>
            <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, marginBottom: 8, cursor: "pointer" }}>
              <input
                type="radio"
                name="ai-bg-mode"
                value="multi"
                checked={aiMode === "multi"}
                onChange={() => setAiMode("multi")}
              />
              Multiple images per section
            </label>
            <p
              style={{
                fontSize: 11,
                color: "#f59e0b",
                background: "#2a2000",
                padding: "6px 8px",
                borderRadius: 4,
                margin: 0,
              }}
            >
              This feature requires an external image generation API key. Configure it in your environment settings.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
