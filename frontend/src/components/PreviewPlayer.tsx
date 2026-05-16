import type { UsePlaybackResult } from "../hooks/usePlayback";

export interface PreviewPlayerProps {
  audioUrl: string | null;
  playback: UsePlaybackResult;
  /** Fallback duration (seconds) used when the audio element reports
   *  Infinity, which happens when the server streams audio without a
   *  Content-Length header. */
  fallbackDuration?: number | null;
}

/** Format seconds as mm:ss. Returns "--:--" for non-finite values. */
function formatTime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "--:--";
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

/**
 * Audio preview player with custom play/pause, seek slider, and timestamp.
 * The <audio> element is hidden — controlled via the shared playback hook.
 */
export function PreviewPlayer({ audioUrl, playback, fallbackDuration }: PreviewPlayerProps) {
  const { currentTime, isPlaying, duration, play, pause, seek, setDuration, audioRef } = playback;

  function handleLoadedMetadata() {
    const audio = audioRef.current;
    if (!audio) return;
    if (Number.isFinite(audio.duration) && audio.duration > 0) {
      setDuration(audio.duration);
    } else if (fallbackDuration && Number.isFinite(fallbackDuration)) {
      setDuration(fallbackDuration);
    }
  }

  // If the audio element never gives us a finite duration but the project
  // has one persisted from the pipeline, use that.
  const effectiveDuration =
    Number.isFinite(duration) && duration > 0
      ? duration
      : fallbackDuration && Number.isFinite(fallbackDuration)
        ? fallbackDuration
        : 0;

  function handleSeek(e: React.ChangeEvent<HTMLInputElement>) {
    seek(Number(e.target.value));
  }

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "8px 0" }}>
      {/* Hidden audio element */}
      <audio
        ref={audioRef}
        src={audioUrl ?? undefined}
        preload="metadata"
        onLoadedMetadata={handleLoadedMetadata}
        style={{ display: "none" }}
      />

      {/* Play / Pause button */}
      <button
        onClick={isPlaying ? pause : play}
        disabled={!audioUrl}
        aria-label={isPlaying ? "Pause" : "Play"}
        style={{
          width: 36,
          height: 36,
          borderRadius: "50%",
          border: "none",
          background: "var(--accent)",
          color: "#fff",
          fontSize: 16,
          cursor: audioUrl ? "pointer" : "not-allowed",
          opacity: audioUrl ? 1 : 0.4,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          flexShrink: 0,
        }}
      >
        {isPlaying ? "⏸" : "▶"}
      </button>

      {/* Timestamp */}
      <span
        style={{
          fontSize: 13,
          fontVariantNumeric: "tabular-nums",
          minWidth: 80,
          color: "var(--text-muted)",
        }}
      >
        {formatTime(currentTime)} / {formatTime(effectiveDuration)}
      </span>

      {/* Seek slider */}
      <input
        type="range"
        min={0}
        max={effectiveDuration || 0}
        step={0.1}
        value={currentTime}
        onChange={handleSeek}
        disabled={!audioUrl || !effectiveDuration}
        aria-label="Seek"
        style={{ flex: 1, padding: 0 }}
      />
    </div>
  );
}
