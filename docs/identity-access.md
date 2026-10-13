# Identity, authentication, and access administration

This guide documents the identity and access behavior currently implemented in AsterForge. It is
intended for administrators, operators, API clients, and contributors. AsterForge is pre-release;
use this foundation for development and evaluation rather than a production deployment.

## Current implementation status

Authentication, secure browser-session restoration, RBAC, environment-driven initial-administrator
bootstrap, and the forced password change lifecycle are implemented. Bootstrap and
administrator-reset passwords are temporary: normal protected API access remains blocked until the
authenticated user changes the password.

## Authentication and sessions

AsterForge uses opaque bearer tokens:

1. `POST /api/v1/auth/login` accepts a username and password.
2. A successful response returns an `access_token`, the `bearer` token type, `expires_at`, and
   `must_change_password`.
3. Clients send the token in `Authorization: Bearer <access-token>`.
4. Login also sets a separate opaque continuation credential as the host-only, `HttpOnly`,
   `SameSite=Strict` `asterforge_session_continuation` cookie, scoped to `/api/v1/auth`.
5. After a full-page reload, the frontend calls `POST /api/v1/auth/restore` with browser credentials.
   A valid continuation creates a new memory-only access bearer for the same server-side session.
6. `POST /api/v1/auth/logout` revokes the parent session and clears the continuation cookie.

Only a SHA-256 digest of each high-entropy token is stored in PostgreSQL. Passwords are stored as
Argon2id hashes and are never returned by user APIs. On every authenticated request, the backend
checks session expiry/revocation, whether the user is enabled, and permissions contributed by
currently enabled profiles.

The default session lifetime is 480 minutes. Restoration never extends that absolute lifetime.
There is no rolling refresh-token flow: after expiry, the user must authenticate again. A restored
bearer and the original login bearer belong to the same parent session, so logout invalidates every
access bearer issued from that continuation. Disabling a user or resetting that user's password
revokes all of the user's active parent sessions.

Only SHA-256 digests of access and continuation credentials are persisted. The raw continuation is
never returned in JSON or exposed to JavaScript. An absent continuation returns HTTP 204 during
bootstrap; an unknown, expired, revoked, reset or disabled-user continuation returns HTTP 401,
clears the cookie, and returns the UI to login without retrying in a loop. Current RBAC and
`must_change_password` state are read from the database during restoration and subsequent requests.

When `must_change_password` is true, the backend permits only `GET /api/v1/auth/me`,
`POST /api/v1/auth/change-password`, and `POST /api/v1/auth/logout` for that session.
Permission-protected routes return HTTP 403 with `password_change_required`, even if the user's profiles would
normally authorize them. This restriction is enforced in the backend.

Clients should keep access tokens in memory, clear them on logout, and avoid persistent
script-readable browser storage. Never place a token in a URL, command history, issue report,
screenshot, or log.

Authentication failures use the same `invalid_credentials` response for an unknown username, a
wrong password, and a disabled user. This avoids disclosing which usernames exist. Failed attempts
are tracked using a one-way username fingerprint rather than the submitted username.

## Login throttling

Login throttling is persisted in PostgreSQL and therefore survives backend restarts. With the
defaults, five failures for a normalized username within 15 minutes establish a 15-minute block;
subsequent attempts receive HTTP 429 with `login_throttled`. Successful authentication clears the
failure record.

Configure the policy with:

| Variable | Default | Accepted range |
| --- | ---: | ---: |
| `ASTERFORGE_LOGIN_ATTEMPT_LIMIT` | `5` | `3`–`20` |
| `ASTERFORGE_LOGIN_ATTEMPT_WINDOW_MINUTES` | `15` | `1`–`60` |
| `ASTERFORGE_LOGIN_BLOCK_MINUTES` | `15` | `1`–`1440` |
| `ASTERFORGE_SESSION_TTL_MINUTES` | `480` | `5`–`10080` |

`ASTERFORGE_SESSION_COOKIE_SECURE` defaults to `false` only for plain-HTTP local development.
Production settings validation requires `true`; production must terminate TLS before the browser
and API. The frontend and API must remain same-site for `SameSite=Strict`, and
`ASTERFORGE_CORS_ORIGINS` must list exact trusted frontend origins. Credentialed CORS is enabled
only for that explicit allowlist. The login and restore endpoints also reject untrusted `Origin` values and
`Sec-Fetch-Site: cross-site`; no other endpoint accepts the cookie as authentication.

These settings are passed to both backend and migration containers by `compose.yaml`; only the
backend uses the authentication settings. Invalid or out-of-range values prevent settings
validation instead of silently weakening the policy.

## Bootstrap the initial administrator

Apply migrations before creating the first account:

```console
podman compose up -d database
podman compose run --rm migrate
```

Set both bootstrap values in the untracked `.env` before starting the backend:

```dotenv
ASTERFORGE_BOOTSTRAP_ADMIN_USERNAME=<administrator-username>
ASTERFORGE_BOOTSTRAP_ADMIN_PASSWORD=<unique-temporary-password>
```

Never put deployable values in `.env.example`. The username must be 3–100 characters using letters,
numbers, underscore, period, or hyphen. The temporary password must contain 12–256 characters.
Both variables must be set together; partial or invalid configuration prevents backend startup.

After migrations complete, start the backend:

```console
podman compose up -d backend
```

During startup, the backend creates an enabled `AsterForge Administrator` user with the immutable,
enabled `Super Administrator` system profile and the complete versioned permission catalog. The
temporary password is hashed immediately, is not stored in plaintext, and is never placed in an API
response or audit payload. The new account starts with `must_change_password=true`.

Bootstrap is idempotent by normalized username. If that username already exists, startup leaves the
user, password, enabled state, and assignments unchanged. Changing the environment password or
restarting containers therefore cannot reset an existing administrator. After the first password
change, remove both bootstrap variables from `.env` or the deployment secret configuration and
restart the backend; the existing account remains available.

For recovery-oriented development workflows, the existing interactive
`python -m app.cli create-admin` command uses the same one-time bootstrap service. It also refuses
to overwrite an existing username and marks a newly created account for password change. The
environment-driven path is the canonical initial deployment workflow.

### First access

Start the API after migrations and administrator creation:

```console
podman compose up -d backend
curl --fail http://localhost:8000/api/v1/health/ready
```

Open the frontend at <http://localhost:4173> and sign in with the temporary bootstrap credential.
English is the default interface language. The language selector on this screen can switch the UI
to Spanish before authentication; the same selector remains available during required password
change and in the authenticated top bar. The non-sensitive locale preference is saved locally and
falls back to English if its stored value is missing or invalid. This does not alter the memory-only
bearer-token model.

The frontend detects `must_change_password`, withholds all normal navigation, and presents the
dedicated password-change form. After the backend accepts the current temporary password and a
different replacement of at least 12 characters, the same session continues into the normal
permission-filtered application shell. The user may sign out from the required-change screen
without changing the password.

API clients and troubleshooting workflows may perform the equivalent sequence directly through
the REST API or FastAPI's generated documentation at <http://localhost:8000/docs>.

For example, request a session with placeholder credentials:

```console
curl --request POST http://localhost:8000/api/v1/auth/login \
  --header "Content-Type: application/json" \
  --data '{"username":"<administrator-username>","password":"<administrator-password>"}'
```

The response contains a live access token. Treat the entire response as sensitive. Use that token
only in the `Authorization` header for later requests.

```console
curl http://localhost:8000/api/v1/auth/me \
  --header "Authorization: Bearer <access-token>"
```

`GET /api/v1/auth/me` returns the current user ID, normalized username, and sorted effective
permission codes, plus `must_change_password`. It does not return profiles, password data, or the
stored token digest.

The initial login returns `must_change_password: true`. Before calling any normal protected route,
change the temporary password with the same bearer token:

```console
curl --request POST http://localhost:8000/api/v1/auth/change-password \
  --header "Authorization: Bearer <access-token>" \
  --header "Content-Type: application/json" \
  --data '{"current_password":"<temporary-password>","new_password":"<new-unique-password>"}'
```

The new password must differ from the current password and contain 12–256 characters. A successful
change returns HTTP 204, clears the forced-change state, keeps the current session usable, revokes
the user's other active sessions, and records `user.password_changed` without credential data.

## Users

User administration requires `user.manage`.

| Method and path | Current behavior |
| --- | --- |
| `GET /api/v1/users` | Lists user IDs, usernames, display names, enabled state, and assigned profile IDs |
| `POST /api/v1/users` | Creates an enabled user with an initial password and zero or more profiles |
| `PATCH /api/v1/users/{user_id}` | Changes display name, enabled state, or complete profile assignment |
| `POST /api/v1/users/{user_id}/password` | Replaces the password and revokes every active session for the user |

Usernames are case-normalized, must be 3–100 characters, and may contain letters, numbers,
underscore, period, and hyphen. Passwords supplied during creation or reset must be 12–256
characters. API responses never include a password or password hash.

An administrator cannot disable their own account or change their own profile assignments through
`PATCH /users/{user_id}`. Disabling another user makes all existing sessions unusable immediately
and marks active sessions revoked. Re-enabling a user does not restore revoked sessions; the user
must log in again.

### Password reset and change-required behavior

An administrator with `user.manage` resets a password by sending a new value to:

```console
POST /api/v1/users/<user-id>/password
Authorization: Bearer <access-token>
Content-Type: application/json

{"password":"<new-password>"}
```

On success, the endpoint returns HTTP 204, clears any login-throttling record for the user, and
revokes all existing sessions, including the current session when an administrator resets their own
password. The administrator must establish a new session afterward if their own token was revoked.

Reset also sets `must_change_password=true`. The user can authenticate with the temporary reset
password, but every normal permission-protected operation is denied until the password-change
flow succeeds. The frontend routes the next login directly to that flow; API clients call
`POST /api/v1/auth/change-password`. Transmit the temporary password through an approved secure
channel and never include it in email, logs, tickets, or audit metadata.

## Profiles (roles) and permissions

A profile is AsterForge's persisted role object. A user may have multiple profiles; effective
permissions are the union of permission codes in every enabled assigned profile. A disabled profile
contributes no permissions, even to an existing session.

Profile and permission administration requires `profile.manage`.

| Method and path | Current behavior |
| --- | --- |
| `GET /api/v1/permissions` | Lists stable permission codes and descriptions |
| `GET /api/v1/profiles` | Lists profiles and their permission codes |
| `POST /api/v1/profiles` | Creates an enabled profile with a selected permission set |
| `PATCH /api/v1/profiles/{profile_id}` | Changes a non-system profile's name, description, enabled state, or complete permission set |

The initial permission catalog is versioned by migration `0002_identity_access`:

| Area | Permission codes |
| --- | --- |
| Dashboards and inventory | `dashboard.read`, `server.read`, `server.manage` |
| Asterisk and calls | `asterisk.read`, `call.read` |
| Logs and alerts | `log.read`, `alert.read`, `alert.acknowledge` |
| Incidents and maintenance | `incident.read`, `maintenance.manage` |
| Reports | `report.read`, `report.export` |
| Identity and audit | `user.manage`, `profile.manage`, `audit.read` |
| Monitoring configuration | `monitoring_config.manage` |

The system `Super Administrator` profile is immutable through the profile API. Other profiles can be
disabled or updated. Unknown permission codes and profile IDs are rejected rather than ignored.

## RBAC and authorization model

Authorization is enforced by FastAPI dependencies on the server. The default is deny:

- missing, invalid, expired, or revoked authentication returns HTTP 401;
- an authenticated user without the required permission receives HTTP 403;
- disabled users cannot authenticate or continue using existing sessions;
- disabled profiles stop contributing permissions on the next request;
- permission changes take effect on the next request because permissions are resolved from the
  database for every authenticated call.

Administrative APIs also prevent privilege escalation. An administrator cannot grant a permission
they do not currently hold, either directly to a profile or indirectly by assigning a profile to a
user. Server-side checks prevent removing the last effective account that has both `user.manage`
and `profile.manage`. Frontend controls, when added later, are convenience only and must never be
treated as the authorization boundary.

## Audit and security guidance

Successful and failed logins, user changes, profile changes, and administrator password resets
create append-oriented audit-event records. Initial bootstrap records `user.bootstrap_created` and
successful user password change records `user.password_changed`. Failure records identify the
attempted username only by a SHA-256 fingerprint. Audit metadata does not include submitted
passwords or bearer tokens.

Administrators should also observe these rules:

- expose the API only through a TLS-terminating reverse proxy outside local development;
- keep allowed CORS origins explicit; credentialed wildcard CORS is not enabled;
- never expose PostgreSQL or Asterisk AMI to browsers or the public Internet;
- keep `.env` outside version control and use deployment secret management for real credentials;
- review logs before sharing them, even though authentication code does not intentionally log
  submitted credentials or tokens;
- create separate least-privilege profiles for routine work instead of sharing the bootstrap
  administrator account;
- disable an account promptly when access must be removed, and use password reset when all of its
  sessions must be revoked.

Authorized users with `audit.read` can inspect redacted, append-oriented audit evidence through the
Audit area or `GET /api/v1/audit-events`. Direct database access should not be granted merely to
work around the authorization boundary.

## Troubleshooting

### Login returns HTTP 401

Confirm the username and password without printing either value. Usernames are normalized to
lowercase-like casefolded form. An unknown user, wrong password, and disabled user intentionally
produce the same response. If an administrator is available, use the authorized users endpoint to
check whether the account is enabled; do not inspect or copy password hashes from PostgreSQL.

### Login returns HTTP 429

The username fingerprint is temporarily blocked after repeated failures. Wait until the configured
block period expires. Restarting the backend does not clear the block because throttling state is in
PostgreSQL. Repeated retries extend operational noise and do not bypass the policy.

### A bearer token returns HTTP 401

The token may be malformed, expired, revoked by logout, revoked by password reset or account
disable, or associated with a disabled user. Authenticate again only after confirming the account is
enabled. Re-enabling an account does not reactivate old sessions.

### An authenticated request returns HTTP 403

The user lacks the endpoint's required effective permission. Check assigned profiles, whether those
profiles are enabled, and their permission codes. Because permissions are evaluated on every
request, log out and back in is not required after a profile change.

### Administrator creation fails

- `permission catalog is empty`: apply migrations with `podman compose run --rm migrate` before
  starting the backend.
- Configuration validation error: set both `ASTERFORGE_BOOTSTRAP_ADMIN_*` variables, verify the
  username format, and use a temporary password of at least 12 characters.
- The account exists but the configured temporary password does not work: bootstrap deliberately
  does not overwrite an existing account. Use the existing password or the authenticated reset
  endpoint; changing `.env` and restarting will not reset it.
- Database connection error: confirm PostgreSQL readiness and that `POSTGRES_DB`, `POSTGRES_USER`,
  `POSTGRES_PASSWORD`, and `ASTERFORGE_DATABASE_URL` agree. Do not paste their values into an issue.

### Password reset appears to sign the user out

This is expected. Reset revokes all active sessions for the target user. The user must authenticate
with the temporary password and then call `/api/v1/auth/change-password`. Until that succeeds,
normal protected routes return `password_change_required`.

## API contract reference

When the backend is running, OpenAPI JSON at <http://localhost:8000/openapi.json> and the generated
documentation at <http://localhost:8000/docs> are the canonical request/response schemas. This page
explains operator-visible behavior; it does not replace the generated contract.
