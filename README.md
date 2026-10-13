# AsterForge

AsterForge is an Asterisk Infrastructure Monitoring & Management Platform for infrastructure,
VoIP, NOC, support, and security teams.

This README is the entry point for the validated **V1 development baseline**. It provides a
runnable application foundation—React, FastAPI, PostgreSQL, migrations, health checks, tests,
Podman workflows, authentication/RBAC, secure Asterisk server inventory, and resilient AMI core
monitoring. V1 is strictly read-only toward Asterisk; configuration changes
and live-call controls are outside its scope.

> **Project status:** pre-release and intended for development or evaluation, not production use.

## What currently runs

| Component | Technology | Purpose | Host access |
| --- | --- | --- | --- |
| Frontend | React + Vite + TypeScript | Minimal responsive application shell | <http://localhost:4173> |
| Backend | Python + FastAPI | APIs plus isolated AMI monitoring workers | <http://localhost:8000> |
| Database | PostgreSQL 18 | Persistent application database and Alembic state | Internal network only |
| Migrations | SQLAlchemy + Alembic | One-shot database schema upgrades | No published port |

The backend and frontend containers run as non-root users. PostgreSQL is isolated on an internal
Podman network, while the two web ports bind only to `127.0.0.1`.

## Prerequisites

- Podman 5.x with a running Podman machine on Windows or macOS
- A working Compose provider, reported by `podman compose version`
- Git
- Optional for host-side development: Python 3.14 and Node.js 24 with npm

The canonical runtime uses Podman and OCI images. It does not require a Docker daemon.

## Current capabilities

- Platform liveness and database readiness checks
- Login, secure full-page-reload session restoration, revocable bearer sessions, logout, and current-user permission discovery
- Environment-driven, idempotent initial-administrator bootstrap with a dedicated mandatory-password-change UI
- English and Spanish product UI with an accessible, locally persisted language selector
- User, profile/role, and stable permission administration through the API
- Server-side, default-deny RBAC and immediate enforcement of disabled users and profiles
- Persistent login throttling and security-sensitive identity audit records
- Authorized Infrastructure views for server inventory and independent Environment/Tag catalog
  administration, including server-usage counts, explicit onboarding, filters, archival, reusable
  classification assignment, and encrypted write-only AMI credentials
- Isolated TCP/TLS AMI sessions, bounded reconnect, explicit freshness, and normalized core health
- Reconciled PJSIP snapshots and persisted outbound-registration trunk state transitions
- LinkedID/UniqueID live-call correlation plus authorized, resynchronizable operational WebSocket
  change notifications
- Capability-aware, bounded CDR search and deterministic CEL call timelines for configured PostgreSQL feeds
- Optional, isolated node_exporter host, bounded network and recording telemetry with persisted,
  capability-aware samples
- Optional, bounded Asterisk text-log ingestion with normalized, redacted, authorized search
- Deterministic availability/capacity/call/recording alert evaluation, deduplicated lifecycle,
  incident timelines, explicit unavailable inputs, and maintenance suppression
- Permission-aware global and per-server monitoring dashboards with persistent investigation
  filters and authorized operational realtime resynchronization
- Permission-filtered global identifier search, seven bounded report families, and secured CSV exports
- Authorized append-only audit exploration plus validated alert-threshold and operational-log
  retention administration

See [Identity, authentication, and access administration](docs/identity-access.md) for initial
administrator setup, first API access, environment settings, RBAC behavior, security guidance,
and troubleshooting. That guide describes the authoritative backend and API behavior.

See [Asterisk server inventory](docs/server-inventory.md) for inventory endpoints, permissions,
filtering, archival behavior, and encryption-key operations.

See [Asterisk and PJSIP monitoring](docs/asterisk-monitoring.md) for AMI guidance, health and PJSIP
endpoints, state semantics, trunk discovery/history, runtime settings, and current limitations.

See [Operational realtime API](docs/operational-realtime.md) for WebSocket authentication, event
families, permission mapping, ordering/resynchronization semantics, and process-local constraints.

See [Host and recording-storage telemetry](docs/host-telemetry.md) for node_exporter deployment,
collector capabilities, configuration, API behavior, permissions, and security limitations.

See [Centralized logs](docs/centralized-logs.md) for the fixed source layout, ingestion limits,
search API and UI, redaction, retention, permissions, and troubleshooting.

See [Alerts, incidents, and maintenance](docs/alerts-incidents-maintenance.md) for initial rules,
lifecycle and correlation behavior, permissions, suppression semantics, APIs, and limitations.

See [Monitoring dashboards](docs/monitoring-dashboards.md) for global health summaries, server
drill-down, investigation filters, permission boundaries, and realtime freshness behavior.

See [Reporting, global search, and CSV export](docs/reporting-search.md) for supported identifiers,
report families, permission combinations, date/size limits, and spreadsheet-safety behavior.

See [Audit and monitoring configuration](docs/audit-monitoring-configuration.md) for audit access,
redaction, threshold validation, and supported retention policy behavior. Deployment lifecycle,
database backup/restore, upgrades, and operational troubleshooting are in the
[Podman operations guide](docs/operations.md). Contributors should start with
[CONTRIBUTING.md](CONTRIBUTING.md) and the [development guide](docs/development.md). Review
[V1 known limitations](docs/known-limitations.md), [security reporting](SECURITY.md), the
[changelog](CHANGELOG.md), [code of conduct](CODE_OF_CONDUCT.md), and [MPL‑2.0 license](LICENSE)
before publication or deployment.

Confirm the container environment before setup:

```console
podman --version
podman info
podman compose version
```

On Windows, start the Podman machine first if `podman info` cannot reach the Linux socket:

```powershell
podman machine list --all-providers
podman machine start
```

## Configure the environment

Copy the tracked example without committing the resulting `.env` file:

```powershell
# PowerShell
Copy-Item .env.example .env
```

```sh
# POSIX shell
cp .env.example .env
```

Replace every placeholder before starting the stack. For the current runtime, these values are
relevant:

| Variable | Meaning |
| --- | --- |
| `ASTERFORGE_ENVIRONMENT` | Runtime mode: `development`, `test`, or `production` |
| `ASTERFORGE_LOG_LEVEL` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL` |
| `ASTERFORGE_DATABASE_URL` | Async SQLAlchemy PostgreSQL URL; must use `postgresql+asyncpg` |
| `ASTERFORGE_CORS_ORIGINS` | JSON array of explicitly allowed frontend origins |
| `ASTERFORGE_SESSION_TTL_MINUTES` | Bearer-session lifetime; default `480`, allowed range `5`–`10080` |
| `ASTERFORGE_SESSION_COOKIE_SECURE` | Requires HTTPS for the HttpOnly continuation cookie; must be `true` in production |
| `ASTERFORGE_LOGIN_ATTEMPT_LIMIT` | Failed attempts in a window before temporary blocking; default `5` |
| `ASTERFORGE_LOGIN_ATTEMPT_WINDOW_MINUTES` | Failed-login counting window; default `15` minutes |
| `ASTERFORGE_LOGIN_BLOCK_MINUTES` | Login block duration after the limit is reached; default `15` minutes |
| `ASTERFORGE_BOOTSTRAP_ADMIN_USERNAME` | Username created once when the configured account does not exist |
| `ASTERFORGE_BOOTSTRAP_ADMIN_PASSWORD` | Temporary initial password; must be replaced on first access |
| `ASTERFORGE_INTEGRATION_ENCRYPTION_KEY` | Stable Fernet key used only by the backend to encrypt integration credentials |
| `ASTERFORGE_AMI_MONITORING_ENABLED` | Enables the backend AMI supervisor; default `true` |
| `ASTERFORGE_AMI_CONNECT_TIMEOUT_SECONDS` | Per-target TCP/TLS connection deadline; default `5` |
| `ASTERFORGE_AMI_ACTION_TIMEOUT_SECONDS` | AMI I/O/action deadline; default `5` |
| `ASTERFORGE_AMI_POLL_INTERVAL_SECONDS` | Core reconciliation/keepalive interval; default `30` |
| `ASTERFORGE_AMI_INVENTORY_REFRESH_SECONDS` | Inventory refresh interval; default `10` |
| `ASTERFORGE_AMI_RECONNECT_MIN_SECONDS` | Initial reconnect backoff; default `1` |
| `ASTERFORGE_AMI_RECONNECT_MAX_SECONDS` | Maximum reconnect backoff including jitter; default `60` |
| `ASTERFORGE_CDR_SOURCE` | Historical CDR adapter: `disabled` (default) or normalized `postgres` feed |
| `ASTERFORGE_CEL_SOURCE` | Historical CEL adapter: `disabled` (default) or normalized `postgres` feed |
| `ASTERFORGE_HOST_TELEMETRY_ENABLED` | Enables node_exporter collection; default `false` |
| `ASTERFORGE_HOST_TELEMETRY_PORT` | Fixed node_exporter port used with each inventory hostname; default `9100` |
| `ASTERFORGE_HOST_TELEMETRY_TIMEOUT_SECONDS` | Per-scrape connect/read deadline; default `5` |
| `ASTERFORGE_HOST_TELEMETRY_POLL_INTERVAL_SECONDS` | Persisted sample interval; default `60`, minimum `5` |
| `ASTERFORGE_HOST_TELEMETRY_INVENTORY_REFRESH_SECONDS` | Server inventory refresh interval; default `10` |
| `ASTERFORGE_HOST_TELEMETRY_RECORDING_MOUNTPOINT` | Exact exported filesystem mountpoint containing recordings |
| `ASTERFORGE_LOG_INGESTION_ENABLED` | Enables fixed-path Asterisk text-log ingestion; default `false` |
| `ASTERFORGE_LOG_SOURCE_ROOT` | Absolute container source root mounted read-only from `./log-sources` |
| `ASTERFORGE_LOG_POLL_INTERVAL_SECONDS` | Per-source polling interval; default `5` |
| `ASTERFORGE_LOG_INVENTORY_REFRESH_SECONDS` | Inventory refresh interval; default `10` |
| `ASTERFORGE_LOG_READ_MAX_BYTES` | Maximum bytes read from one source per poll; default `1048576` |
| `ASTERFORGE_LOG_READ_MAX_LINES` | Maximum lines ingested from one source per poll; default `1000` |
| `ASTERFORGE_LOG_RETENTION_INTERVAL_SECONDS` | Cleanup interval; default `3600` |
| `POSTGRES_DB` | Database created during first PostgreSQL initialization |
| `POSTGRES_USER` | PostgreSQL application user |
| `POSTGRES_PASSWORD` | Password assigned during first PostgreSQL initialization |

The password embedded in `ASTERFORGE_DATABASE_URL` must match `POSTGRES_PASSWORD`. Percent-encode
characters that have special meaning in a URL. Keep CORS origins narrow and never commit `.env`,
credentials, or deployment-specific values. Production settings require
`ASTERFORGE_SESSION_COOKIE_SECURE=true`; keep the frontend and API same-site behind TLS and list
only exact trusted frontend origins so the strict continuation cookie and credentialed CORS work.

Generate the integration key once, place it only in the untracked `.env` or deployment secret
store, and back it up securely. Losing or replacing it makes existing encrypted AMI credentials
unreadable; it is deliberately required by the canonical backend container.

Set both bootstrap variables together in the untracked `.env`. The password must contain at least
12 characters and should be generated uniquely for the deployment. The backend creates the account
only when that normalized username does not exist; restarts never overwrite an existing account or
password. Remove the bootstrap variables from the deployment environment after the administrator
has changed the temporary password.

## Build and start with Podman

Run all commands from the repository root:

```console
podman compose config
podman compose build
podman compose up -d database
podman compose run --rm migrate
podman compose up -d backend frontend
podman compose ps
```

The startup order is intentional: PostgreSQL must become healthy before Alembic applies migrations,
and migrations must finish before the application is considered ready.

Check the application:

```console
curl --fail http://localhost:8000/api/v1/health/live
curl --fail http://localhost:8000/api/v1/health/ready
curl --fail http://localhost:4173/
```

Equivalent PowerShell checks are:

```powershell
Invoke-RestMethod http://localhost:8000/api/v1/health/live
Invoke-RestMethod http://localhost:8000/api/v1/health/ready
Invoke-WebRequest http://localhost:4173/
```

Expected backend responses are `{"status":"ok"}` for liveness and `{"status":"ready"}` for
readiness.

## Health and logs

- `/api/v1/health/live` confirms that the FastAPI process can serve requests.
- `/api/v1/health/ready` also executes a database query. It returns HTTP 503 with a sanitized error
  when PostgreSQL is unavailable.
- Podman health checks cover PostgreSQL and the backend. Frontend availability is verified over
  HTTP.

Inspect status and recent logs with:

```console
podman compose ps
podman compose logs --tail 100 database
podman compose logs --tail 100 backend
podman compose logs --tail 100 frontend
```

Do not publish logs without reviewing them for environment-specific or sensitive information.

## Database migrations

Apply all pending migrations against the containerized PostgreSQL instance:

```console
podman compose up -d database
podman compose run --rm migrate
```

Re-running the migration command is safe: Alembic records the current revision in PostgreSQL and
only applies pending revisions. The bootstrap begins at revision `0001_initial`; later revisions
may exist as subsequent project tasks are completed.

Migration files live in `backend/migrations/versions/`. Generate or edit migrations only as part of
a task that intentionally changes the database schema.

Migration `0002_identity_access` adds users, profiles, permission assignments, bearer sessions,
login throttling state, audit events, and the versioned V1 permission catalog. Migration
`0003_forced_password_change` adds the backend-enforced password-change state. Migration
`0004_asterisk_inventory` adds extensible environments, server tags, Asterisk inventory records,
and encrypted credential columns, and seeds DEV, QA, and PROD environments. Migration
`0005_ami_monitoring` adds the per-server AMI TLS transport selection.
Migration `0006_pjsip_monitoring` adds endpoint, contact, registration, referenced transport and
trunk state, plus append-only trunk state transitions.
Migration `0007_call_history` adds normalized, indexed CDR and CEL records for configured
historical-source feeds.
Migration `0008_host_telemetry` adds indexed, capability-aware host and recording-storage samples.
Migration `0009_centralized_logs` adds indexed normalized log events and durable source cursors.
Migration `0010_alerts_incidents_maintenance` adds seeded alert rules, condition state, alert and
incident history, deterministic incident timelines, and maintenance windows.
Migration `0011_session_continuation` adds digested browser continuation credentials and the
additional access bearers issued to memory after a valid reload.
Migration `0012_audit_retention` adds explicit audit outcome/source fields, PostgreSQL append-only
enforcement, and the persisted operational-log retention policy used by the background worker.
Migration `0013_telemetry_capabilities` adds bounded network aggregates, explicit recording-failure
observations and provenance-bearing optional VoIP-quality fields to host telemetry samples.
Migration `0014_initial_alert_rules` adds endpoint, RAM, general-filesystem, bounded failed-call-rate
and reliable recording-generation-failure rules without changing the shared alert lifecycle.

## PostgreSQL persistence and cleanup

PostgreSQL stores data in the Compose named volume `postgres_data` (created by Podman with a
project-qualified name such as `asterforge_postgres_data`). Removing application containers does
not remove that volume:

```console
podman compose down
podman compose up -d database
podman compose run --rm migrate
podman compose up -d backend frontend
```

To destroy the local database and recreate it from an empty volume, use the following command only
when data loss is intended:

```console
podman compose down --volumes
podman compose up -d database
podman compose run --rm migrate
podman compose up -d backend frontend
```

Changing `POSTGRES_PASSWORD` does not update an already initialized database. For disposable local
bootstrap data, remove the volume and recreate the stack. Preserve or migrate important data
instead of deleting its volume.

## Repository structure

```text
asterforge-dev/
├── backend/
│   ├── app/                 # FastAPI, configuration, API, and database boundaries
│   ├── migrations/          # Alembic environment and versioned migrations
│   ├── tests/               # Backend automated tests
│   ├── Containerfile
│   └── pyproject.toml
├── frontend/
│   ├── src/                 # React application and tests
│   ├── Containerfile
│   ├── package.json
│   └── package-lock.json
├── integration-lab/
│   ├── asterisk/            # Pinned Asterisk/node_exporter release-lab target
│   ├── seed.py              # Disposable PostgreSQL inventory bootstrap
│   └── verify.py            # End-to-end release assertions
├── scripts/
│   ├── run-postgres-tests.py # Disposable PostgreSQL test-stack runner
│   ├── run-compose-smoke.py  # Disposable canonical full-stack release smoke
│   └── run-release-lab.py    # Disposable real-integration release gate
├── .env.example             # Safe defaults and credential placeholders
├── compose.yaml             # Canonical Podman full-stack definition
├── compose.test.yaml        # Isolated PostgreSQL integration-test definition
├── compose.release-lab.yaml # Internal-only Asterisk integration lab
└── README.md
```

## Development and quality gates

### Backend

From `backend/`, create an isolated environment and install the pinned development dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

On a POSIX shell, activate with `source .venv/bin/activate`. Then run:

```console
python -m ruff check .
python -m mypy app
python -m pytest
python -m pip check
```

The normal backend suite uses test-owned in-memory SQLite databases only for portable API and unit
behavior. Its test bootstrap forcibly supplies `ASTERFORGE_ENVIRONMENT=test`, a non-runtime
database URL, a generated encryption key, and no bootstrap administrator. It therefore does not
load a developer administrator, database URL, integration key, or other sensitive values from the
developer's `.env` or shell.

### Isolated PostgreSQL validation

Alembic, real PostgreSQL commits, and bootstrap persistence are validated in a separate disposable
Podman stack. Run this from the repository root; do not point the normal application Compose stack
at a test database or use the development volume for this purpose:

```console
python scripts/run-postgres-tests.py
```

The runner generates a unique `asterforge-test-<id>` Compose project, uses the fixed
`asterforge_test` database on an internal-only network, applies Alembic, runs the backend suite,
and removes the test containers and named volume in a `finally` cleanup path. Cleanup therefore
runs after a failed migration, failed assertion, exception, or keyboard interruption. The runner
does not accept a database URL or project name, and the PostgreSQL integration test independently
rejects a runtime mode, database name, host, or runner identifier outside that disposable boundary.
Repeated runs cannot consume the development database or depend on a prior test volume.

An abrupt host or Podman-machine termination can prevent any process from completing its `finally`
block. Because every run has a unique project name, such an interrupted resource is never reused;
after the Podman machine is available, remove that exact `asterforge-test-<id>` project explicitly.
Never use broad name-pattern cleanup.

### Controlled Asterisk release validation

Run the real integration release gate from the repository root:

```console
python scripts/run-release-lab.py
```

The runner builds a pinned Asterisk 22.9.0 target with node_exporter 1.11.1, creates a fresh
PostgreSQL database on an internal-only network, starts the actual backend supervisors, and checks
AMI health, PJSIP endpoint/trunk reconciliation, an internal PSTN-free live call, host/recording
telemetry, staged Asterisk log ingestion and secret redaction. It also proves that the AsterForge
AMI account cannot originate calls. A separate test-only AMI fixture account creates the internal
call solely so the read-only application path can observe it.

Every run uses a unique `asterforge-release-lab-<id>` project and removes its containers, network,
database volume, and log volume in `finally`, then verifies that no exact-project resource remains.
The runner accepts no external target or project-name input. If the host or Podman machine is
terminated before cleanup completes, remove only the exact project name printed by that run.

### Frontend

From `frontend/`:

```console
npm ci
npm run lint
npm run typecheck
npm test
npm run build
```

Use `npm run dev` for the Vite development server. The production-style container build always
runs `npm ci` and `npm run build` from the lockfile.

The frontend supports English (`en`) and Spanish (`es`). English is used by default and whenever
the saved locale is missing, invalid, or unsupported. Users can change the language on both the
sign-in/password-change screens and the authenticated top bar; the UI updates immediately and
stores only the non-sensitive locale in browser local storage. Bearer tokens remain memory-only;
reload continuity uses a separate server-backed, revocable HttpOnly cookie.

Contributors must add product-owned UI text to both typed catalogs in `frontend/src/i18n.tsx` and
reference it by translation key from components. Keep permission codes, identifiers, hostnames,
administrator-defined names, Asterisk values, and other operational source data untranslated
unless a stable presentation mapping is intentionally defined.

## Bootstrap troubleshooting

### Podman cannot connect

Confirm that the Podman machine is running and that the client can reach its socket:

```console
podman machine list --all-providers
podman machine start
podman info
```

### A container is unhealthy or exits

Inspect status first, then the affected service logs:

```console
podman compose ps
podman compose logs --tail 100 database
podman compose logs --tail 100 backend
podman compose logs --tail 100 frontend
```

If readiness returns HTTP 503, verify that PostgreSQL is healthy and that the database name, user,
password, and URL agree in `.env`. Apply migrations again after connectivity is restored.

### A host port is already in use

Ports 4173 and 8000 must be available on loopback. Find the conflicting container with
`podman ps`, stop it if appropriate, and retry. Do not expose PostgreSQL merely to work around an
application connectivity issue.

### Compose logs fail to render on Windows

Some Python-based Compose providers can raise `UnicodeEncodeError` when container output contains
Unicode characters and the Windows console uses a legacy code page. Switch the terminal to UTF-8
before retrying Compose logs, or resolve the exact container name with `podman compose ps` and use
`podman logs <exact-container-name>`.

### Migration cleanup messages

The migration service is a one-shot container invoked with `--rm`. Some Compose providers may note
during `down` that its already-removed container is absent. If `podman compose down` exits
successfully and `podman compose ps` shows no remaining project containers, cleanup completed.

## Scope and security boundary

The backend connects to configured Asterisk targets only through its read-only, allowlisted AMI
monitoring adapter. It contains no call-control or Asterisk configuration-change behavior. Do not add credentials to
frontend code, expose PostgreSQL publicly, connect browsers directly to Asterisk AMI, or add
administrative Asterisk operations under the V1 scope.
