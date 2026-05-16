import type { ReactNode } from "react";
import { Link, NavLink, useNavigate } from "react-router-dom";
import { useUnsavedChanges } from "./UnsavedChangesContext";

interface AppShellProps {
  children: ReactNode;
  onSignOut?: () => void;
}

/**
 * Top-level layout: brand header on the left, primary nav in the middle,
 * sign-out (when present) on the right. Children render in the main content area.
 */
export function AppShell({ children, onSignOut }: AppShellProps) {
  const navigate = useNavigate();
  const { confirmDiscard } = useUnsavedChanges();

  const guarded = (path: string) => (e: React.MouseEvent) => {
    e.preventDefault();
    if (confirmDiscard()) navigate(path);
  };

  const guardedSignOut = () => {
    if (confirmDiscard()) onSignOut?.();
  };

  return (
    <div style={shell}>
      <header style={headerStyle}>
        <div style={headerInner}>
          <Link to="/" style={brandStyle} onClick={guarded("/")}>
            <span style={brandMark}>SV</span>
            <span style={brandName}>Story Video Editor</span>
          </Link>

          <nav style={navStyle}>
            <NavLink to="/" end style={navLinkStyle} onClick={guarded("/")}>
              New
            </NavLink>
            <NavLink to="/projects" style={navLinkStyle} onClick={guarded("/projects")}>
              Projects
            </NavLink>
          </nav>

          <div style={actionsStyle}>
            {onSignOut && (
              <button className="btn btn-ghost btn-sm" onClick={guardedSignOut}>
                Sign out
              </button>
            )}
          </div>
        </div>
      </header>

      <main style={mainStyle}>{children}</main>
    </div>
  );
}

/* ---------- styles ---------- */

const shell: React.CSSProperties = {
  minHeight: "100vh",
  display: "flex",
  flexDirection: "column",
};

const headerStyle: React.CSSProperties = {
  position: "sticky",
  top: 0,
  zIndex: 100,
  background: "color-mix(in srgb, var(--bg-elevated) 88%, transparent)",
  backdropFilter: "saturate(140%) blur(10px)",
  WebkitBackdropFilter: "saturate(140%) blur(10px)",
  borderBottom: "1px solid var(--border)",
};

const headerInner: React.CSSProperties = {
  maxWidth: 1200,
  margin: "0 auto",
  padding: "12px 24px",
  display: "flex",
  alignItems: "center",
  gap: 24,
};

const brandStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 10,
  textDecoration: "none",
  color: "var(--text-strong)",
};

const brandMark: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  width: 30,
  height: 30,
  background: "linear-gradient(135deg, var(--accent), var(--accent-hover))",
  color: "#fff",
  fontWeight: 700,
  fontSize: 12,
  borderRadius: 8,
  letterSpacing: "0.02em",
};

const brandName: React.CSSProperties = {
  fontWeight: 600,
  fontSize: 15,
  letterSpacing: "-0.01em",
};

const navStyle: React.CSSProperties = {
  display: "flex",
  gap: 4,
  marginLeft: 8,
};

function navLinkStyle({ isActive }: { isActive: boolean }): React.CSSProperties {
  return {
    padding: "6px 12px",
    fontSize: 14,
    borderRadius: 6,
    color: isActive ? "var(--text-strong)" : "var(--text)",
    background: isActive ? "var(--bg-muted)" : "transparent",
    fontWeight: isActive ? 500 : 400,
    textDecoration: "none",
    transition: "background 0.15s ease, color 0.15s ease",
  };
}

const actionsStyle: React.CSSProperties = {
  marginLeft: "auto",
  display: "flex",
  alignItems: "center",
  gap: 8,
};

const mainStyle: React.CSSProperties = {
  flex: 1,
  width: "100%",
  maxWidth: 1200,
  margin: "0 auto",
  padding: "32px 24px 64px",
  boxSizing: "border-box",
};
