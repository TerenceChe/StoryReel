import { useCallback, useRef, useState } from "react";
import type { SubtitleSegment } from "../types";

export interface TimelineProps {
  subtitles: SubtitleSegment[];
  audioDuration: number | null;
  currentTime: number;
  selectedSubtitleId: string | null;
  onSubtitleSelect: (id: string | null) => void;
  onTimingChange: (subtitleId: string, startTime: number, endTime: number) => void;
  onSeek: (time: number) => void;
}

/** Segment colors — cycle through a palette. */
const COLORS = [
  "#4fc3f7",
  "#81c784",
  "#ffb74d",
  "#e57373",
  "#ba68c8",
  "#4dd0e1",
  "#aed581",
  "#ff8a65",
];

/**
 * Assign rows to subtitle segments so overlapping segments stack vertically.
 * Uses a greedy interval-scheduling approach: for each segment, find the
 * lowest row where it doesn't overlap with any already-placed segment.
 */
function assignRows(subtitles: SubtitleSegment[]): Map<string, number> {
  const rows = new Map<string, number>();
  // Track the end time of the last segment placed in each row
  const rowEnds: number[] = [];

  // Sort by start time for greedy placement
  const sorted = [...subtitles].sort((a, b) => a.startTime - b.startTime);

  for (const sub of sorted) {
    let placed = false;
    for (let r = 0; r < rowEnds.length; r++) {
      if (rowEnds[r] <= sub.startTime) {
        rows.set(sub.id, r);
        rowEnds[r] = sub.endTime;
        placed = true;
        break;
      }
    }
    if (!placed) {
      rows.set(sub.id, rowEnds.length);
      rowEnds.push(sub.endTime);
    }
  }

  return rows;
}

const TIMELINE_HEIGHT_PER_ROW = 32;
const TIMELINE_PADDING = 8;
const MIN_SEGMENT_DURATION = 0.05; // minimum 50ms

/**
 * Horizontal timeline showing subtitle segments as colored blocks.
 * Draggable left/right edges adjust start/end times.
 * Overlapping segments are stacked in rows.
 * Click on the timeline background to seek.
 * A vertical line shows the current playback position.
 */
export function Timeline({
  subtitles,
  audioDuration,
  currentTime,
  selectedSubtitleId,
  onSubtitleSelect,
  onTimingChange,
  onSeek,
}: TimelineProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [dragState, setDragState] = useState<{
    subtitleId: string;
    edge: "start" | "end";
    initialMouseX: number;
    initialTime: number;
  } | null>(null);

  const duration = audioDuration ?? 0;
  if (duration <= 0) {
    return (
      <div style={{ padding: "12px 16px", color: "#888", fontSize: 13 }}>
        Timeline unavailable — audio duration unknown.
      </div>
    );
  }

  const rowAssignments = assignRows(subtitles);
  const maxRow = subtitles.length > 0 ? Math.max(...Array.from(rowAssignments.values())) : 0;
  const totalRows = subtitles.length > 0 ? maxRow + 1 : 1;
  const timelineHeight = totalRows * TIMELINE_HEIGHT_PER_ROW + TIMELINE_PADDING * 2;

  /** Convert a pixel X offset to a time value. */
  const pxToTime = useCallback(
    (px: number): number => {
      const el = containerRef.current;
      if (!el) return 0;
      const rect = el.getBoundingClientRect();
      const fraction = Math.max(0, Math.min(1, px / rect.width));
      return fraction * duration;
    },
    [duration],
  );

  /** Convert a time value to a fraction (0-1) of the timeline width. */
  const timeFraction = (t: number) => Math.max(0, Math.min(1, t / duration));

  /** Handle click on the timeline background to seek. */
  const handleBackgroundClick = (e: React.MouseEvent<HTMLDivElement>) => {
    // Don't seek if we were dragging
    if (dragState) return;
    const rect = containerRef.current?.getBoundingClientRect();
    if (!rect) return;
    const x = e.clientX - rect.left;
    const time = pxToTime(x);
    onSeek(time);
  };

  /** Start dragging an edge. */
  const handleEdgeMouseDown = (
    e: React.MouseEvent,
    subtitleId: string,
    edge: "start" | "end",
  ) => {
    e.stopPropagation();
    e.preventDefault();
    const sub = subtitles.find((s) => s.id === subtitleId);
    if (!sub) return;

    const initialTime = edge === "start" ? sub.startTime : sub.endTime;

    setDragState({
      subtitleId,
      edge,
      initialMouseX: e.clientX,
      initialTime,
    });

    const handleMouseMove = (ev: MouseEvent) => {
      const el = containerRef.current;
      if (!el) return;
      const rect = el.getBoundingClientRect();
      const pxPerSecond = rect.width / duration;
      const dx = ev.clientX - e.clientX;
      const dt = dx / pxPerSecond;
      let newTime = initialTime + dt;

      // Clamp within [0, duration]
      newTime = Math.max(0, Math.min(duration, newTime));

      // Validate start < end
      if (edge === "start") {
        const maxStart = sub.endTime - MIN_SEGMENT_DURATION;
        newTime = Math.min(newTime, maxStart);
        onTimingChange(subtitleId, newTime, sub.endTime);
      } else {
        const minEnd = sub.startTime + MIN_SEGMENT_DURATION;
        newTime = Math.max(newTime, minEnd);
        onTimingChange(subtitleId, sub.startTime, newTime);
      }
    };

    const handleMouseUp = () => {
      setDragState(null);
      window.removeEventListener("mousemove", handleMouseMove);
      window.removeEventListener("mouseup", handleMouseUp);
    };

    window.addEventListener("mousemove", handleMouseMove);
    window.addEventListener("mouseup", handleMouseUp);
  };

  const playheadLeft = `${timeFraction(currentTime) * 100}%`;

  return (
    <div style={{ marginTop: 12 }}>
      {/* Time labels */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          fontSize: 11,
          color: "#888",
          marginBottom: 4,
          fontVariantNumeric: "tabular-nums",
        }}
      >
        <span>0:00</span>
        <span>{formatTime(duration)}</span>
      </div>

      {/* Timeline container */}
      <div
        ref={containerRef}
        onClick={handleBackgroundClick}
        role="slider"
        aria-label="Timeline"
        aria-valuemin={0}
        aria-valuemax={duration}
        aria-valuenow={currentTime}
        tabIndex={0}
        style={{
          position: "relative",
          height: timelineHeight,
          background: "#1a1a1a",
          borderRadius: 6,
          border: "1px solid #333",
          cursor: "pointer",
          overflow: "hidden",
          userSelect: "none",
        }}
      >
        {/* Subtitle blocks */}
        {subtitles.map((sub, idx) => {
          const row = rowAssignments.get(sub.id) ?? 0;
          const left = timeFraction(sub.startTime) * 100;
          const width = (timeFraction(sub.endTime) - timeFraction(sub.startTime)) * 100;
          const isSelected = sub.id === selectedSubtitleId;
          const color = COLORS[idx % COLORS.length];

          return (
            <div
              key={sub.id}
              onClick={(e) => {
                e.stopPropagation();
                onSubtitleSelect(sub.id);
              }}
              title={`${sub.text} (${formatTime(sub.startTime)} – ${formatTime(sub.endTime)})`}
              style={{
                position: "absolute",
                left: `${left}%`,
                width: `${width}%`,
                top: TIMELINE_PADDING + row * TIMELINE_HEIGHT_PER_ROW,
                height: TIMELINE_HEIGHT_PER_ROW - 4,
                background: isSelected ? color : `${color}99`,
                borderRadius: 4,
                border: isSelected ? "2px solid #fff" : "1px solid transparent",
                boxSizing: "border-box",
                display: "flex",
                alignItems: "center",
                overflow: "hidden",
                fontSize: 11,
                color: "#000",
                fontWeight: isSelected ? 600 : 400,
                paddingLeft: 6,
                paddingRight: 6,
                whiteSpace: "nowrap",
              }}
            >
              {/* Left drag handle */}
              <div
                onMouseDown={(e) => handleEdgeMouseDown(e, sub.id, "start")}
                style={edgeHandleStyle("left")}
              />

              {/* Label */}
              <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", pointerEvents: "none" }}>
                {sub.text}
              </span>

              {/* Right drag handle */}
              <div
                onMouseDown={(e) => handleEdgeMouseDown(e, sub.id, "end")}
                style={edgeHandleStyle("right")}
              />
            </div>
          );
        })}

        {/* Playhead */}
        <div
          style={{
            position: "absolute",
            left: playheadLeft,
            top: 0,
            bottom: 0,
            width: 2,
            background: "#ff5252",
            pointerEvents: "none",
            zIndex: 10,
          }}
        />
      </div>
    </div>
  );
}

/** Style for the draggable edge handles. */
function edgeHandleStyle(side: "left" | "right"): React.CSSProperties {
  return {
    position: "absolute",
    [side]: 0,
    top: 0,
    bottom: 0,
    width: 6,
    cursor: "col-resize",
    background: "rgba(255,255,255,0.15)",
    zIndex: 2,
  };
}

/** Format seconds as m:ss. */
function formatTime(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}
