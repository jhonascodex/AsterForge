# Audit and monitoring configuration

## Access and evidence

The **Audit trail** screen and `GET /api/v1/audit-events` require `audit.read`. Results are
newest-first, paginated (maximum 200 rows), and filterable by action code, target type, outcome,
and UTC time range. Each record exposes its UTC timestamp, actor or system identity, action,
target, outcome, source, optional request correlation ID, and sanitized metadata.

Audit rows are append-only in PostgreSQL: normal APIs expose no update/delete operation and the
database rejects direct `UPDATE` or `DELETE` against `audit_events`. User-supplied acknowledgement
comments and credentials are not copied into audit metadata. The API also defensively redacts
metadata keys that resemble passwords, secrets, credentials, tokens, authorization headers, or
cookies.

## Alert rules

The **Monitoring configuration** screen and `/api/v1/monitoring-config/alert-rules` require
`monitoring_config.manage`. Operators may change severity, enabled state, activation duration,
debounce, and numeric trigger/clear thresholds. Rule codes and condition types are immutable.
High-condition trigger values must exceed their clear value; low-condition trigger values must be
below it. Categorical rules reject numeric thresholds. Successful changes create audit evidence
containing only changed field names. Percentage rules accept values from 0 through 100; the
recording-generation counter rule accepts values from 0 through 10000. Duration and debounce are
bounded to 0 through 86400 seconds.

## Retention

V1 supports a persisted retention policy for normalized operational logs. It accepts 1–3650 days
and a deletion batch of 1–10000 rows. The log worker reads the current database policy on every
cleanup cycle, deletes only one oldest-first batch, and records policy/execution audit events.
Audit events themselves have no V1 deletion policy.

The cleanup schedule is controlled by `ASTERFORGE_LOG_RETENTION_INTERVAL_SECONDS`; the policy
values are changed in the UI or with:

```http
GET /api/v1/monitoring-config/retention-policies
PATCH /api/v1/monitoring-config/retention-policies/operational_logs
```

The PATCH body is `{"retention_days":30,"batch_size":5000}`. These endpoints require a bearer
session and `monitoring_config.manage`.
