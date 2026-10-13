# Asterisk monitoring and live calls

AsterForge maintains one backend-owned AMI session for each active inventory server that has
monitoring enabled and a complete encrypted credential pair. Sessions collect read-only Asterisk
core settings/status, send AMI keepalives, and reconnect independently after failures. Browsers
never receive or connect with AMI credentials.

## Inventory and transport

Configure a server through the existing `/api/v1/servers` API. In addition to `hostname`,
`ami_port`, and the write-only `ami_username`/`ami_secret` pair, `ami_tls` selects the transport:

- `ami_tls: false` uses plain AMI TCP, normally on port 5038. Restrict it to a trusted private
  network; it does not protect credentials in transit.
- `ami_tls: true` uses TLS with normal certificate-chain and hostname verification. The certificate
  must be trusted by the backend container and valid for the inventory hostname.

Changing a monitored target, rotating its credentials, disabling monitoring, or archiving it is
picked up by the inventory supervisor. Credentials remain encrypted in PostgreSQL and are decrypted
only inside the backend worker.

Use a dedicated AMI account restricted to the backend source network. The adapter sends only
`Login`, `Ping`, `CoreSettings`, `CoreStatus`, `CoreShowChannels`, the four read-only PJSIP list
actions described below, and `Logoff`. It has no generic AMI, CLI, configuration, or call-control
pass-through. Grant the
account only the AMI authorization classes required by these allowlisted actions on the deployed
Asterisk version. Asterisk class authorization is coarse-grained, so verify the effective account
against a controlled PBX and use network ACLs rather than broadening access for convenience.

## Health API

Both endpoints require the backend permission `asterisk.read`:

- `GET /api/v1/asterisk/health` returns health for all non-archived inventory servers.
- `GET /api/v1/asterisk/servers/{server_id}/health` returns one server or `404` when it is missing
  or archived.

Connection states are `unknown`, `connecting`, `connected`, `disconnected`, and `misconfigured`.
Freshness is independent: `unknown` means no successful observation exists, `fresh` is a current
observation, and `stale` means a previously successful observation can no longer be trusted after a
disconnect or configuration problem. Responses include timestamps, a sanitized error code, bounded
reconnect delay, and normalized core values when known. Unsupported or malformed numeric fields are
returned as `null` instead of failing the API.

Core startup and reload timestamps are exposed as `*_source` strings because AMI does not attach an
unambiguous timezone to those values. They must not be interpreted as UTC by clients.

## PJSIP and trunk collection

Each monitoring cycle requests these read-only AMI lists when supported by the connected Asterisk:

- `PJSIPShowEndpoints` for endpoint availability, AoR/transport references, contacts, and active
  channel counts;
- `PJSIPShowContacts` for contact URI, address/port, user agent, reachability, and round-trip time;
- `PJSIPShowRegistrationInboundContactStatuses` for dynamic inbound registration status;
- `PJSIPShowRegistrationsOutbound` for outbound registration status and conservative trunk
  discovery.

An unavailable action is an unsupported capability, not an AMI-session failure. Previous values
owned by that capability become stale. Unknown source states normalize to `unknown` while preserving
a bounded `raw_state`. Endpoint states are `available`, `unavailable`, `in_use`, or `unknown`;
contact states are `available`, `unavailable`, `non_qualified`, or `unknown`; registrations and
trunks are `registered`, `unregistered`, `rejected`, `stopped`, or `unknown`.

Transport rows contain identifiers referenced by endpoint/outbound-registration lists. Those lists
do not include bind/protocol details, so `bind` and `protocol` remain `null`. Asterisk has no
universal trunk object: AsterForge identifies a trunk only when an outbound PJSIP registration
exists, using its object name as stable source ID and label. Static provider endpoints without an
outbound registration are not guessed as trunks.

Snapshots persist in PostgreSQL. A complete supported list marks omitted resources stale,
disconnect marks every resource for that server stale, and reconnect performs full reconciliation.
Trunks retain `last_state_change_at`; every normalized state change, including initial observation,
is appended to history.

## PJSIP APIs

All routes require `asterisk.read`, return `total`, `limit`, `offset`, and `items`, and accept
`limit=1..200` plus a non-negative `offset`:

- `GET /api/v1/asterisk/endpoints` filters by `server_id`, `state`, and `stale`.
- `GET /api/v1/asterisk/contacts` filters by `server_id`, exact `endpoint`, `state`, and `stale`.
- `GET /api/v1/asterisk/registrations` filters by `server_id`, `direction`, `state`, and `stale`.
- `GET /api/v1/asterisk/transports` filters by `server_id` and `stale`.
- `GET /api/v1/asterisk/trunks` filters by `server_id`, `state`, and `stale`.
- `GET /api/v1/asterisk/trunks/{trunk_id}/transitions` returns newest transitions first.

Trunk responses include provider/label, state, last change, calculated duration, and freshness.
`failure_indicator` is `true` only for rejected, `false` for registered, and otherwise `null`.
`active_calls` remains `null` because the trunk response is not yet joined to the separate live-call
projection; consumers should use the live-call API described below.

## Live-call correlation and realtime API

Each connected worker reconciles active channels with the read-only `CoreShowChannels` action and
then consumes recognized channel events while it waits for the next full monitoring cycle. A call
is not treated as a single channel: channels sharing an AMI `LinkedID` form one logical call, with
`UniqueID` used as the explicit fallback when `LinkedID` is absent. Duplicate normalized events are
ignored, a hangup observed before a late channel event prevents stale resurrection, and the next
authoritative snapshot repairs gaps after reconnect.

The live model is process-local and contains only active calls. Disconnect marks retained state
`stale`; a successful reconnect snapshot returns it to `fresh` and removes channels that no longer
exist. The backend does not persist this model as CDR/CEL history.

Both interfaces require the effective permission `call.read`:

- `GET /api/v1/calls/live` returns the current calls grouped by server, including freshness,
  logical/channel identifiers, source state, parties when reported, and observation timestamps.
- `WS /api/v1/ws/calls` sends versioned `calls.initial` and `calls.snapshot` envelopes. The client
  must send `{ "type": "authenticate", "token": "..." }` as its first frame within five seconds;
  no monitoring data is delivered before the server validates the live platform session and
  `call.read`. Use `wss` outside trusted local development.

The frontend Live calls page uses the REST snapshot for initial state and the WebSocket for typed
updates. It explicitly shows fresh, stale, waiting, and realtime-disconnected states. All displayed
AMI identifiers, parties, and states are source values rather than translated operational data.
There are no originate, transfer, redirect, or hangup routes or controls.

Core-health and PJSIP snapshot changes are also published as authorized REST-resync notifications
through the separate [operational realtime API](operational-realtime.md), alongside the other V1
operational families. That feed does not duplicate AMI or PJSIP payloads in WebSocket messages.

## Historical CDR and CEL adapters

Historical sources are disabled by default. Set `ASTERFORGE_CDR_SOURCE=postgres` and/or
`ASTERFORGE_CEL_SOURCE=postgres` only after a deployment-owned, least-privilege ingestion process
populates the normalized `call_detail_records` and `channel_event_records` tables. These tables keep
only investigation fields; they do not provide a raw payload column for SIP bodies, AMI frames, or
arbitrary source data. AsterForge does not assume that every Asterisk deployment exposes CDR and CEL
through the same mechanism.

All historical endpoints require `call.read`:

- `GET /api/v1/calls/history/capabilities` reports each source as `available` or `unconfigured`.
- `GET /api/v1/calls/history` requires timezone-aware `started_from` and `started_to`. It supports
  `server_id`, exact caller/connected number, disposition, and LinkedID/UniqueID correlation filters.
- `GET /api/v1/calls/timeline` requires a server, correlation identifier, and the same bounded time
  window. CEL events sort by event time and stable source identity for deterministic timelines.

Search windows are limited to 31 days. CDR pages allow 1–200 records, CEL pages 1–500 records, and
offsets are capped at 10,000. A configured source with zero matching records returns `available`
with an empty list; an absent source returns `unconfigured`, so missing CEL is never represented as
an ordinary empty timeline.

Authenticated users with `call.read` can use **Call history** in the Investigate navigation group.
The view applies the API's bounded date range and pagination, supports the server, caller, connected
party, disposition, and UniqueID/LinkedID filters, and opens a CEL timeline from a CDR's LinkedID
(or UniqueID fallback). It renders timeline events in the order returned by the deterministic API.
The source badges and result states deliberately distinguish an unconfigured CDR/CEL source from a
configured source with no matching data. In particular, an unconfigured CEL source is shown as
unavailable capability and never as an empty timeline. Request failures and missing permission are
separate states. The UI displays only the normalized investigation fields returned by these APIs.

## Runtime settings

The canonical backend enables monitoring by default. These settings are available as environment
variables; durations are seconds:

| Variable | Default | Purpose |
| --- | ---: | --- |
| `ASTERFORGE_AMI_MONITORING_ENABLED` | `true` | Starts/stops the monitoring supervisor for the backend process. |
| `ASTERFORGE_AMI_CONNECT_TIMEOUT_SECONDS` | `5` | Connection deadline. |
| `ASTERFORGE_AMI_ACTION_TIMEOUT_SECONDS` | `5` | AMI banner, write, and response deadline. |
| `ASTERFORGE_AMI_POLL_INTERVAL_SECONDS` | `30` | Core reconciliation and keepalive interval. |
| `ASTERFORGE_AMI_INVENTORY_REFRESH_SECONDS` | `10` | Inventory change discovery interval. |
| `ASTERFORGE_AMI_RECONNECT_MIN_SECONDS` | `1` | Initial reconnect backoff. |
| `ASTERFORGE_AMI_RECONNECT_MAX_SECONDS` | `60` | Maximum reconnect backoff, including jitter. |
| `ASTERFORGE_CDR_SOURCE` | `disabled` | Use `postgres` only when the normalized CDR feed is populated. |
| `ASTERFORGE_CEL_SOURCE` | `disabled` | Use `postgres` only when the normalized CEL feed is populated. |

Reconnect is exponential with bounded jitter. One unreachable PBX runs in its own asynchronous
worker and does not block other targets or HTTP request handling.

## Current limits

Health and active calls are live, process-local read models and reset when the backend restarts; the
worker immediately reconciles enabled targets again. Realtime fan-out is also process-local, so the
current single-backend deployment is required for coherent WebSocket publication until a shared
event transport is introduced. Historical core health is not implemented. Historical-call APIs
depend on a deployment-provided normalized PostgreSQL CDR/CEL feed; AsterForge does not ingest a
vendor-specific database or file format itself.
AsterForge never changes Asterisk configuration and provides no generic AMI, CLI, or call-control
endpoint.
