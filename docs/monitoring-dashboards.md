# Monitoring dashboards

AsterForge provides two read-only operational views backed by the existing monitoring APIs:

- **Global monitoring dashboard** summarizes monitored servers, PJSIP trunks and endpoints, live
  calls, open alerts, host telemetry, and recording-storage capacity. Its cards are operational
  counts and health signals rather than decorative charts.
- **Server monitoring** drills into Asterisk health, PJSIP resources, live calls, host and storage
  telemetry, recent logs, and recent alerts for one inventory server.

## Filters and investigation context

The environment, server, and investigation-range filters are shared between the global and server
views for the current authenticated session. Selecting **Inspect server** on the fleet table opens
that server without losing the selected time range. The time range bounds recent log queries and
alert presentation; live health, calls, PJSIP, host, and storage values remain current-state data.

## Permissions and capabilities

The UI requests and displays each signal only when the current user has its backend permission:

| Signal | Permission |
| --- | --- |
| Inventory, host telemetry, recording storage | `server.read` |
| Asterisk health, endpoints, trunks | `asterisk.read` |
| Live calls and realtime call updates | `call.read` |
| Recent logs | `log.read` |
| Alerts | `alert.read` |

The global view itself requires `dashboard.read`; unavailable subordinate permissions appear as
restricted signals and do not trigger unauthorized API requests. Missing provider capabilities
are shown as unavailable instead of being inferred or reported as zero.

## Freshness and realtime behavior

Fresh, stale, synchronizing, and disconnected conditions are labeled with text and symbols as well
as color. Live-call WebSocket sessions authenticate without putting the bearer token in the URL,
reconnect with bounded exponential backoff, and accept the server's initial snapshot as the
authoritative resynchronization point. REST data remains visible while reconnecting, with a clear
stale/disconnected indication.

The dashboards also consume the authorized [operational realtime change feed](operational-realtime.md)
for Asterisk health, PJSIP, host/storage telemetry, and alerts. These messages are invalidations,
not replacement data: the client reloads only the affected, permitted REST read model. Every
initial connection, reconnect, and buffer-overflow boundary keeps the view in a resynchronizing
state until all resources listed by `operations.resync_required` have been reloaded successfully.
Failed resynchronization leaves the existing data visibly disconnected/stale and triggers the
bounded reconnect path instead of presenting missed events as current.

Global subscriptions follow the selected server or environment scope; server-detail subscriptions
are restricted to that server. Both the server subscription and each REST refresh remain filtered
by the user's permissions. Provider capability gaps continue to render as unavailable after a
realtime refresh.

These views expose no originate, transfer, hangup, configuration, reload, or restart controls.
