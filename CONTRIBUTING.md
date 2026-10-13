# Contributing to AsterForge

Thank you for helping improve AsterForge. Discuss substantial behavior or architecture changes with
the maintainers before implementation. Keep each change focused and preserve the V1 read-only
Asterisk boundary.

## Development workflow

1. Create a focused branch from the current main branch.
2. Never commit `.env`, credentials, logs, database dumps, recordings, or private infrastructure
   data.
3. Add tests for changed behavior and migrations for schema changes.
4. Update public documentation when commands, configuration, APIs, or operator behavior change.
5. Run the gates in [the development guide](docs/development.md), including disposable PostgreSQL
   validation for persistence or migration changes and the controlled release lab for changes to
   AMI, PJSIP, calls, host telemetry, or log-ingestion boundaries.
6. Describe security/data impact, validation evidence, and remaining limitations in the pull
   request.

Frontend text must remain available in English and Spanish through the centralized catalog.
Backend permission checks are authoritative; frontend visibility never substitutes for RBAC.

By contributing, you agree that your contributions are licensed under MPL‑2.0 as described in
`LICENSE`. Report vulnerabilities through [the security process](SECURITY.md), not a public issue.
