/**
 * API client module for all backend endpoints.
 *
 * Uses the shared axios instance from ./client.ts which handles
 * camelCase ↔ snake_case conversion automatically.
 */

import apiClient from "./client";
import type { Project, ProjectSummary } from "../types";

export interface Voice {
  id: string;
  name: string;
  language: string;
}

/** POST /projects — create a new project and start the pipeline. */
export async function createProject(
  storyText: string,
  voice?: string,
  title?: string,
): Promise<Project> {
  const body: Record<string, string> = { storyText };
  if (voice) body.voice = voice;
  if (title) body.title = title;
  const { data } = await apiClient.post<Project>("/projects", body);
  return data;
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
  const { data } = await apiClient.post<{
    detail: string;
    backgroundImage: string;
  }>(`/projects/${id}/background`, form, {
    headers: { "Content-Type": "multipart/form-data" },
  });
  return data;
}

/** GET /voices — list available edge-tts voices. */
export async function listVoices(): Promise<Voice[]> {
  const { data } = await apiClient.get<Voice[]>("/voices");
  return data;
}
