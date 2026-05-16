import { useAuth0 } from "@auth0/auth0-react";

export function LoginPage() {
  const { loginWithRedirect } = useAuth0();

  return (
    <div style={pageStyle}>
      <div className="card" style={cardStyle}>
        <div style={brandStyle}>
          <span style={brandMark}>SV</span>
        </div>
        <h1 style={titleStyle}>Story Video Editor</h1>
        <p style={subtitleStyle}>
          Turn your stories into narrated videos with subtitles in minutes.
        </p>
        <button
          className="btn btn-primary"
          style={{ width: "100%", padding: "12px 16px", fontSize: 15 }}
          onClick={() => loginWithRedirect()}
        >
          Sign in to continue
        </button>
      </div>
    </div>
  );
}

const pageStyle: React.CSSProperties = {
  minHeight: "100vh",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  padding: 24,
};

const cardStyle: React.CSSProperties = {
  width: "100%",
  maxWidth: 380,
  padding: "32px 28px",
  textAlign: "center",
};

const brandStyle: React.CSSProperties = {
  display: "flex",
  justifyContent: "center",
  marginBottom: 20,
};

const brandMark: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  width: 48,
  height: 48,
  background: "linear-gradient(135deg, var(--accent), var(--accent-hover))",
  color: "#fff",
  fontWeight: 700,
  fontSize: 16,
  borderRadius: 12,
  letterSpacing: "0.02em",
  boxShadow: "var(--shadow)",
};

const titleStyle: React.CSSProperties = {
  marginBottom: 8,
};

const subtitleStyle: React.CSSProperties = {
  color: "var(--text)",
  fontSize: 14,
  marginBottom: 24,
};
