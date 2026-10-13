# Centralized logs

AsterForge can ingest bounded batches from staged Asterisk text logs, normalize them into
PostgreSQL, and expose permission-protected search in the web console and API. Collection is
optional and isolated: a missing or unreadable source is reported by the source-status endpoint
and UI without affecting unrelated platform functions.

## Prepare the source mount

The initial adapter deliberately reads one fixed file per active monitored server. Find the
server UUID from `GET /api/v1/servers`, then stage that server's Asterisk log at:

```text
log-sources/<server UUID>/messages
```

The canonical Compose file mounts `./log-sources` read-only at
`/var/lib/asterforge/log-sources`. Supplying the files is deployment-owned; use a least-privilege
shipper or read-only bind source and do not grant AsterForge SSH, shell, or unrestricted
filesystem access. The adapter never accepts a path through the API.

Enable collection in the untracked `.env`:

```dotenv
ASTERFORGE_LOG_INGESTION_ENABLED=true
```

The cursor is persisted per server/source and survives backend restarts. It restarts from byte zero
when the source becomes shorter than the saved offset, as with truncation or the usual replacement
rotation. Each poll reads at most the configured byte and line limits.

## Normalization and redaction

The adapter recognizes common Asterisk lines such as:

```text
[2026-09-12T12:00:00Z] WARNING[42] res_pjsip.c: endpoint=1001 linkedid=call-1 message
```

It stores server, source (`asterisk/messages`), UTC timestamp, level, module, message, endpoint,
trunk, UniqueID, and LinkedID where present. Unknown formats remain searchable as `INFO` text
with ingestion time as the timestamp. Known authorization, password, secret, token, and API-key
patterns are replaced with `[REDACTED]` before persistence. Redaction is defense in depth, not a
license to collect SIP payloads, credentials, or recording contents; keep those out of source
logs. The UI renders messages as escaped text and provides no raw-HTML path.

## API and permissions

Both endpoints require the `log.read` permission:

- `GET /api/v1/logs` searches by environment, server, time, level, source, module, endpoint,
  trunk, UniqueID, LinkedID, and literal message text.
- `GET /api/v1/logs/sources` reports the latest in-process availability check per server/source.

Search defaults to the previous 24 hours, rejects ranges over 31 days, limits pages to 200 rows,
and caps the offset at 10,000. The web console uses 50-row pages and exposes the same principal
filters. Results are ordered newest first.

## Retention and settings

| Variable | Default | Bound / purpose |
| --- | ---: | --- |
| `ASTERFORGE_LOG_SOURCE_ROOT` | `/var/lib/asterforge/log-sources` | Absolute container path backed by the read-only source mount |
| `ASTERFORGE_LOG_POLL_INTERVAL_SECONDS` | `5` | 1–3600 seconds |
| `ASTERFORGE_LOG_INVENTORY_REFRESH_SECONDS` | `10` | 1–300 seconds |
| `ASTERFORGE_LOG_READ_MAX_BYTES` | `1048576` | 4096–8388608 bytes per source poll |
| `ASTERFORGE_LOG_READ_MAX_LINES` | `1000` | 1–10000 lines per source poll |
| `ASTERFORGE_LOG_RETENTION_INTERVAL_SECONDS` | `3600` | 60–86400 seconds |

The persisted `operational_logs` policy is administered in **Monitoring configuration** or through
`GET/PATCH /api/v1/monitoring-config/retention-policies/operational_logs` by users with
`monitoring_config.manage`. Retention accepts 1–3650 days and batches of 1–10000 rows. Each cleanup
deletes only the oldest eligible bounded batch. It appends
`logs.retention_policy_applied` and `logs.retention_executed` audit events containing the cutoff,
configured limits, and deletion count, without log content or secrets.

## Troubleshooting and limitations

- An unavailable source appears in the UI and `/api/v1/logs/sources`; confirm the UUID directory,
  filename, read permissions, and read-only mount.
- The source-status view is process-local and resets when the backend restarts; persisted logs and
  cursors remain durable.
- The first adapter supports UTF-8 Asterisk text logs staged into the fixed layout. Remote shipping,
  multiline reconstruction, compressed archives, and measured high-volume search backends are not
  part of this increment.
