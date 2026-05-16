# Requirements Document

## Introduction

This feature implements Auth0-based user authentication as the sole authentication method for the Story Video Editor across all environments. Auth0 handles all user registration, login, password management, and social login (Google, Facebook, etc.) externally. The frontend uses the @auth0/auth0-react SDK to redirect users to Auth0's Universal Login for sign-in and sign-up. The backend validates Auth0-issued JWTs against Auth0's JWKS endpoint. Each user's projects are scoped to their Auth0 user ID (the "sub" claim) via the existing owner_id system. For local development, a separate Auth0 tenant (e.g., "myapp-dev") is configured with localhost callback URLs. For production, a separate Auth0 tenant (e.g., "myapp-prod") is used. The same application code runs in all environments — only the Auth0 configuration values differ.

## Glossary

- **Auth0_Provider**: The Auth0Provider React component from @auth0/auth0-react that wraps the application and manages authentication state, token storage, and token refresh
- **Auth0_SDK**: The @auth0/auth0-react library used on the frontend to initiate login/signup via Auth0 Universal Login, retrieve access tokens, and manage session state
- **Universal_Login**: Auth0's hosted login page that handles the sign-in and sign-up UI, including social login buttons, password fields, and account creation — the application redirects to this page rather than rendering its own login form
- **Auth_Middleware**: The backend middleware (backend/auth/middleware.py) that validates Bearer tokens as Auth0 JWTs against the JWKS_Endpoint — the only supported authentication mechanism
- **Access_Token**: A JWT issued by Auth0 after successful authentication, containing the user's identity as the "sub" claim and the configured audience — stored in memory by the Auth0_SDK
- **JWKS_Endpoint**: Auth0's JSON Web Key Set endpoint (https://{AUTH0_DOMAIN}/.well-known/jwks.json) used by the Auth_Middleware to fetch the public keys for verifying Access_Token signatures
- **Sub_Claim**: The "sub" field in the Access_Token payload that contains the Auth0 user identifier (e.g., "auth0|abc123" or "google-oauth2|123456") — used as the owner_id for project ownership
- **Login_Page**: The frontend page displayed to unauthenticated users, containing a "Sign In" button that redirects to Auth0 Universal_Login
- **Protected_Route**: A frontend route that requires authentication — unauthenticated users accessing a Protected_Route are redirected to the Login_Page
- **Auth0_Tenant**: An isolated Auth0 environment with its own domain, users, and configuration — separate tenants are used for development (e.g., "myapp-dev.us.auth0.com") and production (e.g., "myapp-prod.us.auth0.com")

## Requirements

### Requirement 1: Auth0 Frontend Integration

**User Story:** As a user, I want to sign in using Auth0's Universal Login, so that I can authenticate with my email/password or social accounts without the application managing my credentials.

#### Acceptance Criteria

1. THE Auth0_Provider SHALL wrap the application root component and be configured with the AUTH0_DOMAIN, AUTH0_CLIENT_ID, and AUTH0_AUDIENCE values from environment variables (VITE_AUTH0_DOMAIN, VITE_AUTH0_CLIENT_ID, VITE_AUTH0_AUDIENCE)
2. WHEN a user clicks the "Sign In" button on the Login_Page, THE Auth0_SDK SHALL redirect the user to Auth0 Universal_Login
3. WHEN Auth0 Universal_Login completes authentication successfully, THE Auth0_SDK SHALL redirect the user back to the application with a valid Access_Token
4. THE Auth0_SDK SHALL store the Access_Token in memory and SHALL NOT store the Access_Token in localStorage or sessionStorage
5. THE Auth0_SDK SHALL automatically refresh the Access_Token before expiration using Auth0's silent authentication mechanism
6. WHEN the Auth0_SDK fails to refresh the Access_Token silently, THE Login_Page SHALL be displayed to the user to re-authenticate

### Requirement 2: Frontend Authentication Flow

**User Story:** As a user, I want the application to show me a login page when I am not authenticated and redirect me to the editor after login, so that I have a seamless authentication experience.

#### Acceptance Criteria

1. WHEN an unauthenticated user navigates to any Protected_Route, THE application SHALL display the Login_Page instead of the requested content
2. THE Login_Page SHALL display a "Sign In" button that initiates the Auth0 Universal_Login redirect
3. WHEN authentication completes and the Auth0_SDK reports the user as authenticated, THE application SHALL display the main editor view with the user's projects
4. THE application SHALL display a "Sign Out" button in the navigation area for authenticated users
5. WHEN a user clicks the "Sign Out" button, THE Auth0_SDK SHALL clear the local session and redirect the user to Auth0's logout endpoint to clear the Auth0 session
6. WHEN the Auth0_SDK is loading the authentication state (e.g., during token refresh or initial page load), THE application SHALL display a loading indicator instead of the Login_Page or editor content

### Requirement 3: API Request Authentication

**User Story:** As a frontend developer, I want API requests to automatically include the Auth0 access token, so that the backend can identify and authorize the user.

#### Acceptance Criteria

1. WHEN the Auth0_SDK has a valid Access_Token, THE API client SHALL include the Access_Token in the Authorization header as a Bearer token for all API requests
2. THE API client SHALL retrieve the Access_Token from the Auth0_SDK using the getAccessTokenSilently method before each API request
3. WHEN the API client receives a 401 response from the backend, THE application SHALL redirect the user to the Login_Page to re-authenticate
4. WHEN an SSE connection requires authentication, THE API client SHALL pass the Access_Token as a query parameter (token={Access_Token}) since EventSource cannot set custom headers

### Requirement 4: Backend JWT Validation

**User Story:** As a backend developer, I want the backend to validate Auth0-issued JWTs as the sole authentication mechanism, so that the backend authenticates users exclusively through Auth0.

#### Acceptance Criteria

1. THE Auth_Middleware SHALL validate all incoming Bearer tokens as Auth0-issued JWTs using the JWKS_Endpoint derived from AUTH0_DOMAIN
2. THE Auth_Middleware SHALL fetch the signing keys from the JWKS_Endpoint and cache the keys for reuse
3. THE Auth_Middleware SHALL verify the token signature, expiration, issuer, and audience claims
4. WHEN a valid Access_Token is presented, THE Auth_Middleware SHALL extract the Sub_Claim and use the Sub_Claim value as the owner_id for the request
5. WHEN an expired Access_Token is presented, THE Auth_Middleware SHALL reject the request with a 401 response indicating the token has expired
6. WHEN a malformed or tampered Access_Token is presented, THE Auth_Middleware SHALL reject the request with a 401 response
7. WHEN a token is signed by a key not present in the JWKS_Endpoint, THE Auth_Middleware SHALL reject the request with a 401 response

### Requirement 5: Backend Configuration

**User Story:** As a system operator, I want to configure Auth0 integration through environment variables, so that I can deploy the application with Auth0 authentication in any environment without code changes.

#### Acceptance Criteria

1. THE application SHALL accept AUTH0_DOMAIN and AUTH0_AUDIENCE as required environment variables for Auth0 configuration
2. THE application SHALL derive the JWT issuer URL as https://{AUTH0_DOMAIN}/ from the AUTH0_DOMAIN environment variable
3. THE application SHALL derive the JWKS URI as https://{AUTH0_DOMAIN}/.well-known/jwks.json from the AUTH0_DOMAIN environment variable
4. WHEN AUTH0_DOMAIN is not set, THE application SHALL refuse to start and SHALL print an error message indicating that AUTH0_DOMAIN is required (fail-closed behavior)
5. THE application SHALL use AUTH0_AUDIENCE to validate the audience claim in incoming JWTs

### Requirement 6: Project Ownership Mapping

**User Story:** As a user, I want my projects to be associated with my Auth0 account, so that only I can access and edit my projects.

#### Acceptance Criteria

1. WHEN a user authenticated via Auth0 creates a project, THE application SHALL set the project's owner_id to the Sub_Claim value from the Access_Token
2. THE Auth_Middleware SHALL extract the Sub_Claim from the Access_Token and pass the Sub_Claim value as the owner_id for all project operations
3. WHEN a token contains a Sub_Claim with a valid Auth0 user identifier format (e.g., "auth0|abc123" or "google-oauth2|123456"), THE Auth_Middleware SHALL accept the Sub_Claim value as the owner_id
4. THE project ownership verification logic SHALL remain unchanged — the owner_id is always sourced from the Auth0 Sub_Claim

### Requirement 7: Frontend Environment Configuration

**User Story:** As a frontend developer, I want Auth0 configuration provided through environment variables, so that I can configure the Auth0 integration for different environments without code changes.

#### Acceptance Criteria

1. THE frontend build SHALL read VITE_AUTH0_DOMAIN, VITE_AUTH0_CLIENT_ID, and VITE_AUTH0_AUDIENCE from environment variables
2. THE Auth0_Provider SHALL be configured with the values from VITE_AUTH0_DOMAIN, VITE_AUTH0_CLIENT_ID, and VITE_AUTH0_AUDIENCE
3. THE frontend SHALL read the Auth0 callback URL from the current window origin (window.location.origin) for the Auth0_Provider redirect_uri configuration
4. WHEN any of VITE_AUTH0_DOMAIN, VITE_AUTH0_CLIENT_ID, or VITE_AUTH0_AUDIENCE is not set, THE application SHALL display an error message indicating the missing Auth0 configuration

### Requirement 8: Environment Separation

**User Story:** As a system operator, I want to use separate Auth0 tenants for development and production, so that development activity does not affect production users or data.

#### Acceptance Criteria

1. THE application SHALL support different Auth0_Tenant configurations per environment through environment variables alone, with no code changes required
2. WHEN deployed for local development, THE application SHALL be configured with a development Auth0_Tenant (e.g., "myapp-dev.us.auth0.com") that has localhost callback URLs registered
3. WHEN deployed for production, THE application SHALL be configured with a production Auth0_Tenant (e.g., "myapp-prod.us.auth0.com") that has the production domain callback URLs registered
4. THE application code SHALL be identical across all environments — only the AUTH0_DOMAIN, AUTH0_AUDIENCE, VITE_AUTH0_DOMAIN, VITE_AUTH0_CLIENT_ID, and VITE_AUTH0_AUDIENCE environment variable values differ between environments

### Requirement 9: Testing Strategy

**User Story:** As a developer, I want a clear testing approach for authentication, so that I can write unit tests without depending on a live Auth0 tenant.

#### Acceptance Criteria

1. WHEN writing unit tests that require an authenticated user, THE test SHALL override the get_owner_id FastAPI dependency to return a fixed test owner_id directly, bypassing JWT validation
2. WHEN testing the JWT validation logic in the Auth_Middleware, THE test SHALL use test RSA keypairs to sign test JWTs and mock the JWKS_Endpoint to return the corresponding test public key
3. THE test suite SHALL verify that the Auth_Middleware rejects tokens with invalid signatures by signing test JWTs with a different RSA key than the one served by the mocked JWKS_Endpoint
4. THE test suite SHALL verify that the Auth_Middleware rejects expired tokens by creating test JWTs with past expiration timestamps
5. THE test suite SHALL verify that the Auth_Middleware rejects tokens with incorrect issuer or audience claims
6. THE test suite SHALL verify that the application refuses to start when AUTH0_DOMAIN is not set
