/**
 * TypeScript interfaces matching backend Pydantic models.
 *
 * Backend uses snake_case; frontend uses camelCase.
 * The API client handles conversion between the two conventions.
 */

export interface Position {
  x: number; // 0-1 normalized (fraction of video width)
  y: number; // 0-1 normalized (fraction of video height)
}

export interface SubtitleStyle {
  fontSize: number; // normalized (fraction of video height, e.g. 0.047 ≈ 48px at 1024h)
  fontColor: string; // hex color
  outlineColor: string; // hex color
  fontFamily: string; // font name
  maxWidth: number; // normalized line-wrap width (fraction of video width)
  align: "left" | "center" | "right"; // per-line horizontal alignment
}

export interface SubtitleSegment {
  id: string;
  text: string;
  startTime: number; // seconds
  endTime: number; // seconds
  position: Position;
  style: SubtitleStyle;
}

export interface PipelineProgress {
  stage: "narration" | "subtitles" | "assembly" | "complete" | "error";
  message: string;
}

export interface Project {
  id: string;
  title: string;
  storyText: string;
  voice: string;
  status:
    | "pending"
    | "processing"
    | "ready"
    | "exporting"
    | "exported"
    | "error";
  version: number;
  pipelineProgress: PipelineProgress;
  subtitles: SubtitleSegment[];
  backgroundImage: string | null;
  videoUrl: string | null;
  audioUrl: string | null;
  audioDuration: number | null; // seconds
  exportUrl: string | null;
  createdAt: string;
  updatedAt: string;
}

/** Summary returned by GET /projects list endpoint */
export interface ProjectSummary {
  id: string;
  title: string;
  status: Project["status"];
  createdAt: string;
  updatedAt: string;
}
