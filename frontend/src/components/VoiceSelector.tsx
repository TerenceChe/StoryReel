import { useEffect, useState } from "react";
import { listVoices, type Voice } from "../api/projects";

interface VoiceSelectorProps {
  value: string;
  onChange: (voice: string) => void;
}

/**
 * Dropdown populated from the /voices endpoint.
 * Shows voice name and language, defaults to XiaoxiaoNeural.
 */
export function VoiceSelector({ value, onChange }: VoiceSelectorProps) {
  const [voices, setVoices] = useState<Voice[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    listVoices()
      .then((data) => {
        if (!cancelled) {
          setVoices(data);
          setError(null);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to load voices");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (loading) {
    return (
      <select disabled aria-label="Voice" style={selectStyle}>
        <option>Loading voices…</option>
      </select>
    );
  }

  if (error) {
    return (
      <select disabled aria-label="Voice" style={selectStyle}>
        <option>Failed to load voices</option>
      </select>
    );
  }

  return (
    <select
      aria-label="Voice"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      style={selectStyle}
    >
      {voices.map((v) => (
        <option key={v.id} value={v.id}>
          {v.name} ({v.language})
        </option>
      ))}
    </select>
  );
}

const selectStyle: React.CSSProperties = {
  width: "100%",
  padding: "10px 12px",
  fontSize: 14,
  borderRadius: "var(--radius-sm)",
  border: "1px solid var(--border)",
  background: "var(--bg-elevated)",
  color: "var(--text-strong)",
  boxSizing: "border-box",
};
