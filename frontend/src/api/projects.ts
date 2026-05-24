/**
 * API client module for all backend endpoints.
 *
 * Uses the shared axios instance from ./client.ts which handles
 * camelCase ↔ snake_case conversion automatically.
 */

import axios from "axios";
import apiClient from "./client";
import type { Project, ProjectSummary } from "../types";
import type { TitleErrorCode } from "../lib/titleValidation";

export interface Voice {
  id: string;
  name: string;
  language: string;
}

/**
 * Backend `TitleErrorCode` values. The shape-validation codes match the
 * frontend `TitleErrorCode`; the backend additionally returns
 * `title_duplicate` for owner-scoped uniqueness violations, which the
 * client-side validator cannot check on its own.
 */
export type TitleApiErrorCode = TitleErrorCode | "title_duplicate";

/**
 * Error thrown by `createProject` / `renameProject` when the server
 * rejects the request with a structured title-validation body of the
 * form `{ detail: { errorCode, field: "title", message } }`.
 *
 * Components render `message` inline next to the title input and may
 * branch on `code` (e.g. to refresh the list on `title_duplicate`).
 */
export class TitleApiError extends Error {
  readonly code: TitleApiErrorCode;
  readonly field: "title";

  constructor(code: TitleApiErrorCode, message: string) {
    super(message);
    this.name = "TitleApiError";
    this.code = code;
    this.field = "title";
  }
}

/**
 * Inspect an error from the API client and, if it is a structured
 * title-validation response, throw a `TitleApiError`. Otherwise rethrow
 * the original error so existing toast / generic handlers still apply.
 *
 * The backend response shape is documented in
 * `.kiro/specs/project-titles/design.md` § Backend / Error response shape:
 *   { "detail": { "error_code": "title_*", "field": "title", "message": "..." } }
 *
 * Response keys are converted to camelCase by the axios response
 * interceptor (`./client.ts`), so we read `errorCode` here.
 */
function rethrowAsTitleError(err: unknown): never {
  if (axios.isAxiosError(err)) {
    const detail = err.response?.data?.detail as
      | { errorCode?: string; field?: string; message?: string }
      | undefined;
    if (
      detail &&
      detail.field === "title" &&
      typeof detail.errorCode === "string" &&
      typeof detail.message === "string"
    ) {
      throw new TitleApiError(
        detail.errorCode as TitleApiErrorCode,
        detail.message,
      );
    }
  }
  throw err;
}

/** POST /projects — create a new project and start the pipeline.
 *
 *  `title` is required; `voice` is optional and defaults to the backend
 *  default. On a structured title-validation failure this throws a
 *  `TitleApiError`; other failures rethrow the original axios error.
 */
export async function createProject(
  storyText: string,
  title: string,
  voice?: string,
): Promise<Project> {
  const body: Record<string, string> = { storyText, title };
  if (voice) body.voice = voice;
  try {
    const { data } = await apiClient.post<Project>("/projects", body);
    return data;
  } catch (err) {
    rethrowAsTitleError(err);
  }
}

/** GET /projects — list the current user's projects (summaries only). */
export async function listProjects(): Promise<ProjectSummary[]> {
  const { data } = await apiClient.get<ProjectSummary[]>("/projects");
  return data;
}

/** GET /projects/:id — get full project state. */
export async function getProject(id: string): Promise<Project> {
  const { data } = await apiClient.get<Project>(`/projects/${id}`);
  return data;
}

/** PUT /projects/:id — update project state (includes version for optimistic concurrency). */
export async function updateProject(
  id: string,
  state: Partial<Project>,
): Promise<Project> {
  const { data } = await apiClient.put<Project>(`/projects/${id}`, state);
  return data;
}

/** PATCH /projects/:id/title — rename a project.
 *
 *  Sends `{title, version}` for optimistic concurrency. On a structured
 *  title-validation failure (invalid shape or duplicate under the same
 *  owner) this throws a `TitleApiError` carrying the backend `code` and
 *  user-facing `message`.
 */
export async function renameProject(
  id: string,
  title: string,
  version: number,
): Promise<Project> {
  try {
    const { data } = await apiClient.patch<Project>(
      `/projects/${id}/title`,
      { title, version },
    );
    return data;
  } catch (err) {
    rethrowAsTitleError(err);
  }
}

/** DELETE /projects/:id — delete project and all associated files. */
export async function deleteProject(id: string): Promise<void> {
  await apiClient.delete(`/projects/${id}`);
}

/** POST /projects/:id/export — trigger async video export. Returns 202.
 *  Optionally accepts the current draft state so the backend can render
 *  the user's unsaved edits.
 */
export async function triggerExport(
  id: string,
  draft?: Project | null,
): Promise<{ detail: string; projectId: string }> {
  const { data } = await apiClient.post<{ detail: string; projectId: string }>(
    `/projects/${id}/export`,
    draft ?? undefined,
  );
  return data;
}

/** GET /projects/:id/export/download — download the exported MP4 as a Blob. */
export async function downloadExport(id: string): Promise<Blob> {
  const { data } = await apiClient.get<Blob>(
    `/projects/${id}/export/download`,
    { responseType: "blob" },
  );
  return data;
}

/** POST /projects/:id/retry — retry pipeline from the failed stage. */
export async function retryPipeline(
  id: string,
): Promise<{ detail: string; projectId: string }> {
  const { data } = await apiClient.post<{ detail: string; projectId: string }>(
    `/projects/${id}/retry`,
  );
  return data;
}

/** POST /projects/:id/background — upload a custom background image. */
export async function uploadBackground(
  id: string,
  file: File,
): Promise<{ detail: string; backgroundImage: string }> {
  const form = new FormData();
  form.append("file", file);
  // Don't set Content-Type — axios/the browser will auto-set it including
  // the multipart boundary. Forcing "multipart/form-data" without a
  // boundary breaks parsing on the server.
  const { data } = await apiClient.post<{
    detail: string;
    backgroundImage: string;
  }>(`/projects/${id}/background`, form, {
    headers: { "Content-Type": undefined as unknown as string },
  });
  return data;
}

/** GET /voices — list available edge-tts voices. */
export async function listVoices(): Promise<Voice[]> {
  const { data } = await apiClient.get<Voice[]>("/voices");
  return data;
}
