# V1 known limitations

## Publication status

The V1 technical baseline has passed its source-controlled controlled-environment release gate.
No stable release has been published. Repository-host configuration, including enabling the host's
private vulnerability-reporting channel when available, remains a publication-administration step
and is not performed by the application or its test runners.

## Supported-boundary limitations

- V1 observes Asterisk; it cannot write configuration, reload/restart services or modules, control
  calls, execute CLI/shell commands, delete/play recordings, or perform backup/restore of Asterisk.
- The release lab validates its pinned representative Asterisk version. Operators must still
  validate deployment-specific AMI permissions, network policy, version-specific events, PJSIP
  topology, reconnect reconciliation, and live-call correlation before production use.
- CDR/CEL adapters consume normalized PostgreSQL-backed source tables only when enabled; AsterForge
  does not configure Asterisk CDR/CEL production pipelines. Failed-call alerts ignore unknown
  dispositions and remain unavailable until at least ten known outcomes exist in the bounded
  five-minute window.
- The initial host provider is read-only node_exporter HTTP on a trusted private network. It has no
  built-in TLS/authentication transport.
- Network telemetry is a bounded aggregate across non-loopback devices, not a per-interface
  diagnostic view. Recording-generation failure detection requires an operator-owned reliable
  textfile-collector counter. No verified VoIP/RTCP quality source ships in V1, so quality fields
  remain explicitly unavailable unless a trustworthy provider supplies them.
- The initial log adapter reads staged UTF-8 text files from a fixed read-only directory. It is not
  a remote shipper and does not reconstruct multiline or compressed archives.
- Realtime fan-out and ordering are process-local and require the supported single-backend
  deployment. Dashboards consume operational invalidations but REST remains authoritative, so
  refresh failures are shown as disconnected/stale rather than applying event payloads locally.
- The frontend and API bind to loopback in the canonical Compose file. Production TLS/reverse-proxy
  topology is deployment-owned and must keep browser/API traffic same-site.
- V1 retention is configurable for operational logs only. Audit evidence is append-only and needs
  deployment capacity monitoring; no audit deletion policy is exposed.
- V1 ships no automatic database schema rollback across releases. Operators must back up and test
  restore procedures before upgrading.
