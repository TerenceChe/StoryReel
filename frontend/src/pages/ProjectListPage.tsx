import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { listProjects, deleteProject } from "../api/projects";
import { useToast } from "../components/Toast";
import type { ProjectSummary } from "../types";

export function ProjectListPage() {
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const navigate = useNavigate();
  const { showToast } = useToast();

  useEffect(() => {
    fetchProjects();
  }, []);

  async function fetchProjects() {
    setLoading(true);
    try {
      const data = await listProjects();
      setProjects(data);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Failed to load projects";
      showToast(msg);
    } finally {
      setLoading(false);
    }
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
          {projects.map((p) => (
            <article
              key={p.id}
              className="card"
              style={projectCardStyle}
              onClick={() => navigate(`/projects/${p.id}`)}
              role="button"
              tabIndex={0}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  navigate(`/projects/${p.id}`);
                }
              }}
            >
              <div style={cardBodyStyle}>
                <div style={cardTopStyle}>
                  <h3 style={titleStyle}>{p.title || "Untitled"}</h3>
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
          ))}
        </div>
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

const titleStyle: React.CSSProperties = {
  margin: 0,
  fontSize: 16,
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
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
