import { useEffect } from "react";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import { Auth0Provider, useAuth0 } from "@auth0/auth0-react";
import { ToastProvider } from "./components/Toast";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { ConnectionStatus } from "./components/ConnectionStatus";
import { AppShell } from "./components/AppShell";
import { UnsavedChangesProvider } from "./components/UnsavedChangesContext";
import { CreateProjectPage } from "./pages/CreateProjectPage";
import { EditorPage } from "./pages/EditorPage";
import { ProjectListPage } from "./pages/ProjectListPage";
import { LoginPage } from "./pages/LoginPage";
import { configureAuth } from "./api/client";

const AUTH_DISABLED =
  String(import.meta.env.VITE_DISABLE_AUTH ?? "").toLowerCase() === "true";

function AuthedRoutes({ onSignOut }: { onSignOut: () => void }) {
  return (
    <BrowserRouter>
      <AppShell onSignOut={onSignOut}>
        <Routes>
          <Route path="/" element={<CreateProjectPage />} />
          <Route path="/projects" element={<ProjectListPage />} />
          <Route path="/projects/:id" element={<EditorPage />} />
        </Routes>
      </AppShell>
    </BrowserRouter>
  );
}

function AuthGate() {
  const { isAuthenticated, isLoading, logout, getAccessTokenSilently } = useAuth0();

  useEffect(() => {
    configureAuth(getAccessTokenSilently);
  }, [getAccessTokenSilently]);

  if (isLoading) {
    return (
      <div style={{ display: "flex", justifyContent: "center", alignItems: "center", height: "100vh" }}>
        <p style={{ color: "var(--text-muted)", fontSize: 15 }}>Loading…</p>
      </div>
    );
  }

  if (!isAuthenticated) return <LoginPage />;

  return (
    <>
      <ConnectionStatus />
      <AuthedRoutes
        onSignOut={() => logout({ logoutParams: { returnTo: window.location.origin } })}
      />
    </>
  );
}

function LocalShell() {
  return (
    <>
      <ConnectionStatus />
      <BrowserRouter>
        <AppShell>
          <Routes>
            <Route path="/" element={<CreateProjectPage />} />
            <Route path="/projects" element={<ProjectListPage />} />
            <Route path="/projects/:id" element={<EditorPage />} />
          </Routes>
        </AppShell>
      </BrowserRouter>
    </>
  );
}

function App() {
  if (AUTH_DISABLED) {
    return (
      <ErrorBoundary>
        <ToastProvider>
          <UnsavedChangesProvider>
            <LocalShell />
          </UnsavedChangesProvider>
        </ToastProvider>
      </ErrorBoundary>
    );
  }

  const domain = import.meta.env.VITE_AUTH0_DOMAIN;
  const clientId = import.meta.env.VITE_AUTH0_CLIENT_ID;
  const audience = import.meta.env.VITE_AUTH0_AUDIENCE;

  if (!domain || !clientId) {
    return (
      <div style={{ maxWidth: 480, margin: "0 auto", padding: "80px 20px", textAlign: "center" }}>
        <h1 style={{ marginBottom: 8 }}>Configuration Error</h1>
        <p style={{ color: "var(--danger)", fontSize: 14 }}>
          Missing Auth0 configuration. Set VITE_AUTH0_DOMAIN, VITE_AUTH0_CLIENT_ID, and VITE_AUTH0_AUDIENCE environment variables, or set VITE_DISABLE_AUTH=true to run locally without authentication.
        </p>
      </div>
    );
  }

  return (
    <Auth0Provider
      domain={domain}
      clientId={clientId}
      authorizationParams={{
        redirect_uri: window.location.origin,
        ...(audience ? { audience } : {}),
      }}
    >
      <ErrorBoundary>
        <ToastProvider>
          <UnsavedChangesProvider>
            <AuthGate />
          </UnsavedChangesProvider>
        </ToastProvider>
      </ErrorBoundary>
    </Auth0Provider>
  );
}

export default App;
