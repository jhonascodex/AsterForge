# Changelog

All notable project changes are recorded here. The format follows Keep a Changelog principles;
versions use semantic versioning once releases begin.

## Unreleased

### Added

- V1 monitoring and observability baseline for identity/RBAC, inventory, AMI/PJSIP, calls,
  telemetry, logs, alerts/incidents/maintenance, dashboards, reporting, audit, and monitoring
  configuration.
- Podman deployment, migrations, isolated PostgreSQL validation, bilingual UI, and public operator
  and contributor documentation.
- A source-controlled, disposable release lab covering real Asterisk 22.9.0 AMI/PJSIP/live-call
  behavior, node_exporter host and recording telemetry, staged log ingestion, redaction, and exact
  Podman resource cleanup.

### Security

- Opaque revocable sessions with HttpOnly reload continuation, forced temporary-password change,
  encrypted AMI credentials, default-deny RBAC, sanitized errors/logs, bounded exports, redacted
  append-only audit evidence, and a strict read-only Asterisk boundary.
- Updated `cryptography` to 50.0.1 to resolve the dependency advisories identified by the V1
  release audit.

No V1 release has been published. A release date/version is added only by the publication process.
