# Architecture, development, and testing

AsterForge V1 is a modular monolith: React/Vite provides the browser console; FastAPI owns API,
RBAC, and application services; separate cancellable background supervisors collect AMI, host,
and log observations; PostgreSQL is the durable store. External integrations sit behind adapters.
The browser never connects directly to AMI, and no V1 adapter accepts arbitrary shell, CLI, or AMI
actions.

Supported local toolchains are Python 3.14 and Node.js 24. The canonical full stack is Podman; host
toolchains are for focused quality checks.

## Backend gates

```console
cd backend
python -m venv .venv
python -m pip install -e ".[dev]"
python -m ruff check .
python -m mypy app
python -m pytest
python -m pip check
```

Portable unit/API tests use isolated in-memory SQLite stores. PostgreSQL-specific migration,
constraint, index, commit, and bootstrap claims require the disposable Podman runner from the
repository root:

```console
python scripts/run-postgres-tests.py
```

It creates a unique Compose project, rejects non-test targets, and removes its containers and
volume in `finally`. Abrupt host termination may leave that uniquely named project; remove only the
exact project printed by that run.

The full canonical Compose smoke uses the same ownership rules, additionally checks loopback
health, non-root application users, internal-only PostgreSQL exposure, and volume persistence:

```console
python scripts/run-compose-smoke.py
```

The V1 real-integration gate uses an internal-only, disposable lab rather than a production or
developer PBX:

```console
python scripts/run-release-lab.py
```

It builds pinned Asterisk 22.9.0 and node_exporter 1.11.1 packages, migrates and seeds a fresh
PostgreSQL volume, and exercises the actual backend AMI, PJSIP, live-call, telemetry, and log
supervisors. The AsterForge AMI account is verified unable to originate calls; a distinct test-only
fixture account originates only an internal `Local` channel with no carrier or PSTN access. The
test also requires redaction of a controlled secret in an Asterisk log.

The runner accepts no external target configuration. Its unique
`asterforge-release-lab-<id>` project, containers, internal network, database volume, and log volume
are removed in `finally`, and exact-project absence is asserted. If abrupt host termination prevents
that cleanup, remove only the exact project identifier printed for the interrupted run.

## Frontend gates

```console
cd frontend
npm ci
npm run lint
npm run typecheck
npm test
npm run build
npm audit --omit=dev
```

Add every product-owned string to both typed catalogs in `src/i18n.tsx`. Preserve operational
identifiers and source values unless a deliberate localized presentation mapping exists. Reuse
shared controls and include loading, empty, error, unauthorized, and stale states as applicable.

## Change discipline

Keep V1 read-only toward Asterisk. Add no configuration writes, service/module control, live-call
control, generic AMI actions, SSH commands, or shell pass-through. Database changes require an
Alembic migration and real PostgreSQL validation. Security-sensitive platform changes require
redacted audit evidence and server-side authorization.
