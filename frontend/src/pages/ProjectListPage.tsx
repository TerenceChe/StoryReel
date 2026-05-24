import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import axios from "axios";
import {
  listProjects,
  deleteProject,
  renameProject,
  TitleApiError,
} from "../api/projects";
import { useToast } from "../components/Toast";
import type { ProjectSummary } from "../types";

/**
 * Per-row rename state machine.
 *
 * `idle` — title is shown read-only; the pencil icon is the only
 * affordance.
 * `editing` — the input is open with `draft` and the user can type or
 * cancel.
 * `submitting` — a `renameProject` request is in flight; the Save
 * button is disabled and Enter is a no-op (Requirement 2.4).
 * `error` — the previous submit was rejected by the server with a
 * structured `TitleApiError`. The input stays open with the user's
 * draft so they can correct it without retyping.
 */
type RenameState =
  | { mode: "idle" }
  | { mode: "editing"; draft: string }
  | { mode: "submitting"; draft: string }
  | { mode: "error"; draft: string; message: string };

export function ProjectListPage() {
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [renameStates, setRenameStates] = useState<
    Record<string, RenameState>
  >({});
  const navigate = useNavigate();
  const { showToast } = useToast();

  useEffect(() => {
    fetchProjects();
  }, []);

  async function fetchProjects(): Promise<ProjectSummary[]> {
    setLoading(true);
    try {
      const data = await listProjects();
      setProjects(data);
      return data;
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Failed to load projects";
      showToast(msg);
      return [];
    } finally {
      setLoading(false);
    }
  }

  function getRenameState(id: string): RenameState {
    return renameStates[id] ?? { mode: "idle" };
  }

  function setRenameState(id: string, next: RenameState) {
    setRenameStates((prev) => ({ ...prev, [id]: next }));
  }

  async function handleDelete(e: React.MouseEvent, id: string) {
    e.stopPropagation();
    if (!window.confirm("Delete this project? This cannot be undone.")) return;
    try {
      await deleteProject(id);
      setProjects((prev) => prev.filter((p) => p.id !== id));
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Failed to delete project";
      showToast(msg);
    }
  }

  function handleStartRename(e: React.MouseEvent, p: ProjectSummary) {
    e.stopPropagation();
    setRenameState(p.id, { mode: "editing", draft: p.title });
  }

  function handleCancelRename(e: React.MouseEvent | React.KeyboardEvent, id: string) {
    e.stopPropagation();
    setRenameState(id, { mode: "idle" });
  }

  /**
   * Save handler shared by the Save button click, Enter keypress, and
   * any other code path that submits the rename. Returns silently when
   * the row is not in a submittable state — this preserves the
   * in-flight guard (Requirement 2.4): repeated calls while the row is
   * `submitting` are no-ops.
   */
  async function handleSaveRename(id: string) {
    const current = getRenameState(id);
    // In-flight guard: only `editing` and `error` rows may submit.
    if (current.mode !== "editing" && current.mode !== "error") {
      return;
    }
    const project = projects.find((p) => p.id === id);
    if (!project) return;

    const draft = current.draft;
    setRenameState(id, { mode: "submitting", draft });

    try {
      const updated = await renameProject(id, draft, project.version);
      // On success, refresh the row from the server response so the
      // summary list shows the new title, version, and updated_at.
      setProjects((prev) =>
        prev.map((p) =>
          p.id === id
            ? {
                ...p,
                title: updated.title,
                version: updated.version,
                updatedAt: updated.updatedAt,
              }
            : p,
        ),
      );
      setRenameState(id, { mode: "idle" });
    } catch (err) {
      if (err instanceof TitleApiError && err.field === "title") {
        // Validation / duplicate: keep the input open with the user's
        // draft and surface the server message inline.
        setRenameState(id, { mode: "error", draft, message: err.message });
        return;
      }
      // Version conflict (non-title 409): refresh the summary list so
      // we pick up the latest version, then re-enter editing with the
      // user's draft preserved so they can resubmit.
      if (
        axios.isAxiosError(err) &&
        err.response?.status === 409 &&
        !(err instanceof TitleApiError)
      ) {
        const fresh = await fetchProjects();
        const stillExists = fresh.find((p) => p.id === id);
        if (stillExists) {
          setRenameState(id, { mode: "editing", draft });
        } else {
          setRenameState(id, { mode: "idle" });
        }
        return;
      }
      // Anything else: surface as a toast and drop back to editing so
      // the user can retry.
      const msg = err instanceof Error ? err.message : "Failed to rename project";
      showToast(msg);
      setRenameState(id, { mode: "editing", draft });
    }
  }

  return (
    <div style={containerStyle}>
      <div style={headerRowStyle}>
        <div>
          <h1>Projects</h1>
          <p style={subtitleStyle}>
            {loading
              ? "Loading…"
              : projects.length === 0
                ? "Nothing here yet."
                : `${projects.length} ${projects.length === 1 ? "project" : "projects"}`}
          </p>
        </div>
        <button className="btn btn-primary" onClick={() => navigate("/")}>
          + New project
        </button>
      </div>

      {loading ? (
        <div style={emptyStateStyle}>
          <p style={{ color: "var(--text-muted)" }}>Loading projects…</p>
        </div>
      ) : projects.length === 0 ? (
        <div className="card" style={emptyStateCardStyle}>
          <h2 style={{ marginBottom: 8 }}>No projects yet</h2>
          <p style={{ color: "var(--text)", marginBottom: 20 }}>
            Create your first story video to see it here.
          </p>
          <button className="btn btn-primary" onClick={() => navigate("/")}>
            Create your first project
          </button>
        </div>
      ) : (
        <div style={gridStyle}>
          {projects.map((p) => {
            const renameState = getRenameState(p.id);
            const isEditing = renameState.mode !== "idle";
            return (
              <article
                key={p.id}
                className="card"
                style={projectCardStyle}
                onClick={() => {
                  if (isEditing) return;
                  navigate(`/projects/${p.id}`);
                }}
                role="button"
                tabIndex={0}
                onKeyDown={(e) => {
                  if (isEditing) return;
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    navigate(`/projects/${p.id}`);
                  }
                }}
              >
                <div style={cardBodyStyle}>
                  <div style={cardTopStyle}>
                    <RenameRow
                      project={p}
                      state={renameState}
                      onStart={(e) => handleStartRename(e, p)}
                      onCancel={(e) => handleCancelRename(e, p.id)}
                      onSave={() => handleSaveRename(p.id)}
                      onDraftChange={(draft) => {
                        // Editing while a request is in flight is
                        // blocked at the input level (disabled), but
                        // guard here too for safety. Typing while in
                        // `error` clears the error and returns the
                        // row to plain `editing`, matching the
                        // CreateProjectPage pattern.
                        if (renameState.mode === "submitting") return;
                        setRenameState(p.id, { mode: "editing", draft });
                      }}
                    />
                    <span className={`pill pill-${p.status}`}>{p.status}</span>
                  </div>
                  <p style={metaStyle}>
                    Created {new Date(p.createdAt).toLocaleDateString()}
                  </p>
                </div>
                <div style={cardFooterStyle}>
                  <button
                    className="btn btn-ghost btn-sm"
                    onClick={(e) => {
                      e.stopPropagation();
                      navigate(`/projects/${p.id}`);
                    }}
                  >
                    Open
                  </button>
                  <button
                    className="btn btn-danger-ghost btn-sm"
                    onClick={(e) => handleDelete(e, p.id)}
                    aria-label={`Delete project ${p.title || p.id}`}
                  >
                    Delete
                  </button>
                </div>
              </article>
            );
          })}
        </div>
      )}
    </div>
  );
}

/* ---------- inline rename row ---------- */

interface RenameRowProps {
  project: ProjectSummary;
  state: RenameState;
  onStart: (e: React.MouseEvent) => void;
  onCancel: (e: React.MouseEvent | React.KeyboardEvent) => void;
  onSave: () => void;
  onDraftChange: (draft: string) => void;
}

function RenameRow({
  project,
  state,
  onStart,
  onCancel,
  onSave,
  onDraftChange,
}: RenameRowProps) {
  const inputRef = useRef<HTMLInputElement>(null);

  // Auto-focus the input when the row enters editing mode for the
  // first time — this is what Example E2 (Requirement 2.1) asserts.
  useEffect(() => {
    if (state.mode === "editing" || state.mode === "error") {
      inputRef.current?.focus();
    }
  }, [state.mode]);

  if (state.mode === "idle") {
    return (
      <div style={titleRowStyle}>
        <h3 style={titleStyle}>{project.title || "Untitled"}</h3>
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          aria-label="Rename project"
          onClick={onStart}
          style={pencilButtonStyle}
        >
          ✎
        </button>
      </div>
    );
  }

  const submitting = state.mode === "submitting";
  const errorMessage = state.mode === "error" ? state.message : null;

  return (
    <div style={editingWrapStyle} onClick={(e) => e.stopPropagation()}>
      <input
        ref={inputRef}
        type="text"
        value={state.draft}
        onChange={(e) => onDraftChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            // In-flight guard: Enter is a no-op while submitting.
            if (submitting) return;
            onSave();
          } else if (e.key === "Escape") {
            e.preventDefault();
            onCancel(e);
          }
        }}
        aria-label="Project title"
        aria-invalid={errorMessage !== null}
        aria-describedby={errorMessage ? `rename-error-${project.id}` : undefined}
        disabled={submitting}
        style={inputStyle}
      />
      <div style={editingButtonsStyle}>
        <button
          type="button"
          className="btn btn-primary btn-sm"
          onClick={(e) => {
            e.stopPropagation();
            onSave();
          }}
          disabled={submitting}
        >
          {submitting ? "Saving…" : "Save"}
        </button>
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          onClick={onCancel}
          disabled={submitting}
        >
          Cancel
        </button>
      </div>
      {errorMessage && (
        <p
          id={`rename-error-${project.id}`}
          role="alert"
          style={errorMessageStyle}
        >
          {errorMessage}
        </p>
      )}
    </div>
  );
}

/* ---------- styles ---------- */

const containerStyle: React.CSSProperties = {
  maxWidth: 1100,
  margin: "0 auto",
};

const headerRowStyle: React.CSSProperties = {
  display: "flex",
  justifyContent: "space-between",
  alignItems: "flex-end",
  gap: 16,
  marginBottom: 24,
  flexWrap: "wrap",
};

const subtitleStyle: React.CSSProperties = {
  marginTop: 4,
  color: "var(--text)",
  fontSize: 14,
};

const gridStyle: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))",
  gap: 16,
};

const projectCardStyle: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  cursor: "pointer",
  transition: "border-color 0.15s ease, box-shadow 0.15s ease, transform 0.05s ease",
  outline: "none",
};

const cardBodyStyle: React.CSSProperties = {
  padding: "18px 18px 14px",
  flex: 1,
};

const cardTopStyle: React.CSSProperties = {
  display: "flex",
  justifyContent: "space-between",
  alignItems: "flex-start",
  gap: 12,
  marginBottom: 8,
};

const titleRowStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 6,
  minWidth: 0,
  flex: 1,
};

const titleStyle: React.CSSProperties = {
  margin: 0,
  fontSize: 16,
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

const pencilButtonStyle: React.CSSProperties = {
  padding: "2px 6px",
  fontSize: 14,
  lineHeight: 1,
};

const editingWrapStyle: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 6,
  flex: 1,
  minWidth: 0,
};

const inputStyle: React.CSSProperties = {
  width: "100%",
  padding: "6px 8px",
  fontSize: 14,
  border: "1px solid var(--border)",
  borderRadius: 4,
};

const editingButtonsStyle: React.CSSProperties = {
  display: "flex",
  gap: 6,
};

const errorMessageStyle: React.CSSProperties = {
  margin: 0,
  color: "var(--danger)",
  fontSize: 12,
};

const metaStyle: React.CSSProperties = {
  margin: 0,
  fontSize: 13,
  color: "var(--text-muted)",
};

const cardFooterStyle: React.CSSProperties = {
  display: "flex",
  justifyContent: "flex-end",
  gap: 6,
  padding: "10px 12px",
  borderTop: "1px solid var(--border)",
};

const emptyStateStyle: React.CSSProperties = {
  padding: "48px 0",
  textAlign: "center",
};

const emptyStateCardStyle: React.CSSProperties = {
  padding: "48px 24px",
  textAlign: "center",
};
