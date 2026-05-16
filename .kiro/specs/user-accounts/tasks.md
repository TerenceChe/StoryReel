# Tasks

## Task 1: Backend Configuration — Replace Settings with Auth0-Only Config

- [x] 1.1 Update `backend/config.py`: Remove `API_SECRET_KEY`, `DEV_OWNER_ID`, `JWT_ISSUER`, `JWT_AUDIENCE`, `JWT_JWKS_URI` fields. Add `AUTH0_DOMAIN` and `AUTH0_AUDIENCE` fields from env vars. Add `JWT_ISSUER` and `JWT_JWKS_URI` as computed `@property` methods derived from `AUTH0_DOMAIN`.
- [x] 1.2 Update `backend/main.py`: Replace `_check_auth_config()` to check `settings.AUTH0_DOMAIN` instead of `JWT_ISSUER`/`API_SECRET_KEY`. Print error and `sys.exit(1)` if `AUTH0_DOMAIN` is not set.
- [x] 1.3 Update `backend/tests/conftest.py`: Replace `os.environ.setdefault("API_SECRET_KEY", "test-secret-key")` with `os.environ.setdefault("AUTH0_DOMAIN", "test-auth0.example.com")` and `os.environ.setdefault("AUTH0_AUDIENCE", "test-audience")`.

## Task 2: Backend Auth Middleware — Simplify to JWT-Only

- [x] 2.1 Update `backend/auth/middleware.py` `get_owner_id` function: Remove the simple-token fallback branch (the `secrets.compare_digest` block and `X-Owner-Id` header reading). Keep the `?token=` query param fallback for SSE. Always validate via `decode_jwt` and extract `sub` claim.
- [x] 2.2 Remove `import secrets` from `backend/auth/middleware.py` since it is no longer used.
- [x] 2.3 Verify `decode_jwt`, `_get_signing_key`, JWKS caching, and `verify_project_ownership` remain unchanged.

## Task 3: Backend Auth Tests — Update to Auth0-Only

- [x] 3.1 Update `backend/tests/test_auth.py`: Remove `TestTokenValidation` class (simple token mode tests).
- [x] 3.2 Update `TestJWTValidation` setup: Use `AUTH0_DOMAIN` and `AUTH0_AUDIENCE` on Settings instead of `JWT_ISSUER`, `JWT_AUDIENCE`, `JWT_JWKS_URI`. Let the derived properties compute the issuer and JWKS URI.
- [x] 3.3 Add a unit test verifying the app startup check fails when `AUTH0_DOMAIN` is not set (test `_check_auth_config` behavior).
- [x] 3.4 Run backend tests to verify all pass: `cd backend && python -m pytest tests/ -x -q`

## Task 4: Frontend — Add @auth0/auth0-react and Auth0Provider

- [x] 4.1 Install `@auth0/auth0-react`: Add `"@auth0/auth0-react": "^2.2.4"` to `frontend/package.json` dependencies and run `npm install` in the frontend directory.
- [x] 4.2 Rewrite `frontend/src/App.tsx`: Remove imports of `isAuthenticated`, `clearToken` from `./auth`. Import `Auth0Provider` and `useAuth0` from `@auth0/auth0-react`. Wrap the app in `Auth0Provider` configured with `VITE_AUTH0_DOMAIN`, `VITE_AUTH0_CLIENT_ID`, `VITE_AUTH0_AUDIENCE` from `import.meta.env`. Show error message if any env var is missing. Create `AuthGate` component that uses `useAuth0()` to show loading indicator, LoginPage, or authenticated routes.
- [x] 4.3 Delete `frontend/src/auth.ts` (localStorage-based token management, replaced by Auth0 SDK).

## Task 5: Frontend — Replace LoginPage with Auth0 Redirect

- [x] 5.1 Rewrite `frontend/src/pages/LoginPage.tsx`: Remove the token input form. Replace with a simple component that uses `useAuth0().loginWithRedirect` on button click. Remove the `onLogin` prop (no longer needed — Auth0Provider manages auth state). Display "Story Video Editor" heading, "Sign in to continue" text, and a "Sign In" button.

## Task 6: Frontend — Update API Client to Use Auth0 Token

- [x] 6.1 Update `frontend/src/api/client.ts`: Remove `localStorage.getItem("api_token")` from the request interceptor. Add a `configureAuth(getToken: () => Promise<string>)` function that stores the token provider. Update the request interceptor to call the stored async token provider. Make `getApiToken()` async, returning the token from the provider.
- [x] 6.2 In `frontend/src/App.tsx` `AuthGate` component: Call `configureAuth(getAccessTokenSilently)` on mount using `useEffect`, where `getAccessTokenSilently` comes from `useAuth0()`.

## Task 7: Frontend — Update SSE Components for Async Token

- [x] 7.1 Update `frontend/src/components/PipelineProgress.tsx`: Replace synchronous `getApiToken()` call with async token retrieval. Use `useState` + `useEffect` to fetch the token and construct the SSE URL asynchronously. Remove the import of `getApiToken` if it's only used for SSE URL construction (or keep it if the async version is used).
- [x] 7.2 Update `frontend/src/components/ExportPanel.tsx`: Same pattern — replace synchronous `getApiToken()` in `handleExport` with `await getApiToken()` to get a fresh token before constructing the SSE URL.

## Task 8: Frontend Environment Variables

- [x] 8.1 Update `frontend/.env` to add placeholder Auth0 variables: `VITE_AUTH0_DOMAIN`, `VITE_AUTH0_CLIENT_ID`, `VITE_AUTH0_AUDIENCE` with example/placeholder values and comments.

## Task 9: Property-Based Tests

- [x] 9.1 Add property test for Property 4 (AUTH0_DOMAIN derivation) in `backend/tests/test_auth.py`: Use `hypothesis` to generate random domain strings, verify `Settings.JWT_ISSUER` equals `https://{domain}/` and `Settings.JWT_JWKS_URI` equals `https://{domain}/.well-known/jwks.json`. Tag: `Feature: user-accounts, Property 4: AUTH0_DOMAIN derivation`.
- [x] 9.2 Add property test for Property 3 (Sub claim extraction) in `backend/tests/test_auth.py`: Use `hypothesis` to generate random sub claim strings, create valid JWTs with test RSA keypairs, verify the middleware returns the exact sub value. Tag: `Feature: user-accounts, Property 3: Sub claim extraction as owner_id`.
- [x] 9.3 Add property test for Property 2 (Invalid JWT rejection) in `backend/tests/test_auth.py`: Use `hypothesis` to generate JWTs with randomized invalid claims (wrong key, expired, wrong issuer, wrong audience), verify all rejected with 401. Tag: `Feature: user-accounts, Property 2: Invalid JWT claims are rejected`.
- [x] 9.4 Add property test for Property 5 (Auth0 sub format acceptance) in `backend/tests/test_auth.py`: Use `hypothesis` to generate sub claims in Auth0 provider formats (`auth0|{id}`, `google-oauth2|{id}`, etc.), create valid JWTs, verify middleware returns the full sub string. Tag: `Feature: user-accounts, Property 5: Auth0 sub format acceptance`.
- [x] 9.5 Run all backend tests to verify property tests pass: `cd backend && python -m pytest tests/test_auth.py -x -q`

## Task 10: Cleanup and Verification

- [x] 10.1 Search codebase for remaining references to `API_SECRET_KEY`, `DEV_OWNER_ID`, `AUTH_DISABLED`, and remove or update them (check routers, services, README, comments, .env files).
- [x] 10.2 Run full backend test suite: `cd backend && python -m pytest tests/ -x -q`
- [x] 10.3 Run frontend build to verify no TypeScript errors: `cd frontend && npm run build`
