import { useCallback, useEffect, useRef, useState } from "react";
import {
  getProject,
  updateProject as apiUpdateProject,
} from "../api/projects";
import type { Project } from "../types";

export type SaveStatus = "idle" | "saving" | "saved" | "error";

export interface UseProjectResult {
  /** The current project (draft if there are unsaved edits, server state otherwise). */
  project: Project | null;
  /** Loading the initial fetch. */
  loading: boolean;
  /** Initial fetch / save error message, or null. */
  error: string | null;
  /** Apply a local-only edit to the draft. Does not hit the server. */
  applyLocalEdit: (patch: Partial<Project>) => void;
  /** Persist the current draft to the server. */
  save: () => Promise<boolean>;
  /** Refetch the latest server state and discard local edits. */
  refreshProject: () => Promise<void>;
  /** True when the local draft differs from the last-known server state. */
  isDirty: boolean;
  saveStatus: SaveStatus;
}

const DRAFT_PREFIX = "story-video-editor:draft:";

function loadDraft(id: string): Project | null {
  try {
    const raw = localStorage.getItem(DRAFT_PREFIX + id);
    return raw ? (JSON.parse(raw) as Project) : null;
  } catch {
    return null;
  }
}

function saveDraft(id: string, project: Project) {
  try {
    localStorage.setItem(DRAFT_PREFIX + id, JSON.stringify(project));
  } catch {
    // localStorage may be full or disabled — ignore
  }
}

function clearDraft(id: string) {
  try {
    localStorage.removeItem(DRAFT_PREFIX + id);
  } catch {
    /* ignore */
  }
}

/**
 * Project hook with local-draft semantics.
 *
 * Edits via ``applyLocalEdit`` mutate an in-memory draft only. ``save``
 * persists the draft to the server. Drafts are mirrored to localStorage
 * keyed by project id so a refresh restores in-progress edits.
 */
export function useProject(id: string | null): UseProjectResult {
  const [project, setProject] = useState<Project | null>(null);
  // The pristine server snapshot used for dirty detection.
  const [serverProject, setServerProject] = useState<Project | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saveStatus, setSaveStatus] = useState<SaveStatus>("idle");

  // Keep refs around so save callbacks always see the latest state.
  const projectRef = useRef<Project | null>(null);
  useEffect(() => {
    projectRef.current = project;
  }, [project]);

  const fetchProject = useCallback(async () => {
    if (!id) return;
    setLoading(true);
    setError(null);
    try {
      const data = await getProject(id);
      setServerProject(data);
      // Restore any in-progress draft from localStorage. We only carry over
      // user-editable fields — system-owned fields (status, version, URLs,
      // audio info) always come from the server so that things like the
      // newly-rendered exportUrl appear after an export completes.
      const cached = loadDraft(id);
      const usable =
        cached && cached.id === data.id && cached.version === data.version;
      const initial: Project = usable && cached
        ? {
            ...data,
            title: cached.title,
            backgroundImage: cached.backgroundImage,
            subtitles: cached.subtitles,
          }
        : data;
      setProject(initial);
      projectRef.current = initial;
      setSaveStatus("idle");
    } catch (err: unknown) {
      const msg =
        err instanceof Error ? err.message : "Failed to load project";
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    fetchProject();
  }, [fetchProject]);

  const applyLocalEdit = useCallback(
    (patch: Partial<Project>) => {
      if (!id) return;
      const current = projectRef.current;
      if (!current) return;
      const next: Project = { ...current, ...patch };
      projectRef.current = next;
      setProject(next);
      saveDraft(id, next);
      // Any change leaves the saved state behind.
      setSaveStatus((prev) => (prev === "saving" ? prev : "idle"));
    },
    [id],
  );

  const save = useCallback(async (): Promise<boolean> => {
    if (!id) return false;
    const draft = projectRef.current;
    if (!draft) return false;
    setSaveStatus("saving");
    try {
      const updated = await apiUpdateProject(id, draft);
      // Server bumped the version. Carry it onto the local copy so a
      // subsequent save doesn't 409.
      const merged: Project = { ...draft, version: updated.version };
      projectRef.current = merged;
      setProject(merged);
      setServerProject(merged);
      clearDraft(id);
      setSaveStatus("saved");
      return true;
    } catch (err: unknown) {
      const msg =
        err instanceof Error ? err.message : "Failed to save project";
      setError(msg);
      setSaveStatus("error");
      return false;
    }
  }, [id]);

  // Compare draft with the pristine server copy to determine dirty state.
  const isDirty = (() => {
    if (!project || !serverProject) return false;
    // Cheap shallow check on top-level then deep on subtitles.
    if (project.title !== serverProject.title) return true;
    if (project.backgroundImage !== serverProject.backgroundImage) return true;
    if (project.subtitles.length !== serverProject.subtitles.length) return true;
    try {
      return JSON.stringify(project.subtitles) !== JSON.stringify(serverProject.subtitles);
    } catch {
      return true;
    }
  })();

  return {
    project,
    loading,
    error,
    applyLocalEdit,
    save,
    refreshProject: fetchProject,
    isDirty,
    saveStatus,
  };
}
