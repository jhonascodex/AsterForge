# Operational realtime API

AsterForge exposes an authorized process-local WebSocket change feed at
`WS /api/v1/ws/operations`. REST APIs remain the authoritative source of operational state: the
feed carries bounded invalidation messages that tell a client which authorized snapshot to reload,
not a second copy of the resource payload.

## Authentication and scope

The client must send an authentication frame within five seconds:

```json
{
  "type": "authenticate",
  "token": "<current access token>",
  "server_ids": ["<optional-server-uuid>"]
}
```

`server_ids` is optional, accepts at most 200 UUIDs, and narrows delivery to those servers. It does
not grant access. The backend maps each event family to the same permission used by its REST API,
filters every subscription server-side, and revalidates the live session and current permissions
before delivering each queued event. A revoked/expired session or a user with no supported read
permission is disconnected; the token is never placed in the WebSocket URL.

| Event type | Resource | Required permission | Authoritative REST state |
| --- | --- | --- | --- |
| `asterisk.health.changed` | `asterisk_health` | `asterisk.read` | `/api/v1/asterisk/health` |
| `pjsip.snapshot.changed` | `pjsip` | `asterisk.read` | `/api/v1/asterisk/endpoints`, `/contacts`, `/registrations`, `/transports`, `/trunks` |
| `host.telemetry.changed` | `host_telemetry` | `server.read` | `/api/v1/host/telemetry` |
| `alerts.changed` | `alerts` | `alert.read` | `/api/v1/alerts` |
| `incidents.changed` | `incidents` | `incident.read` | `/api/v1/incidents` |

The current RBAC model grants these read permissions globally across non-archived inventory. The
optional server list is therefore a subscription filter, not a substitute for authorization.

## Envelope and resynchronization

Immediately after authentication, the server sends this boundary before ordinary updates:

```json
{
  "type": "operations.resync_required",
  "version": 1,
  "timestamp": "2026-09-13T20:00:00+00:00",
  "resource": "operations",
  "server_id": null,
  "stream_id": "<backend-stream-uuid>",
  "sequence": 42,
  "payload": {
    "reason": "initial_subscription",
    "resources": ["asterisk_health", "pjsip"]
  }
}
```

The client must reload the listed REST resources before treating the feed as current. This is also
required after every reconnect. If the bounded per-client queue overflows, the backend discards the
queued deltas and sends another `operations.resync_required` with reason `buffer_overflow`.

Ordinary updates use the same versioned envelope, identify one server and carry
`{"resync":"rest"}` as their payload. `stream_id` changes when the backend process restarts, and
`sequence` establishes ordering within that process. Sequence values are global and may have
expected gaps after permission or server-scope filtering; clients must use explicit resync messages,
not numeric contiguity, as the missed-state boundary. Duplicate or out-of-order source observations
for the same event family and server are not republished.

## Runtime boundary

Realtime fan-out, ordering and duplicate suppression are process-local. The supported deployment
therefore uses one backend process. Multiple backend replicas require a shared event transport and
coordinated subscription state before this contract can be considered coherent across replicas.
The endpoint is read-only and adds no AMI action, call control, Asterisk configuration, CLI or shell
execution path. Use `wss` outside trusted local development.
