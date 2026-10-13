# Host and recording-storage telemetry

AsterForge can collect read-only Linux host metrics from Prometheus `node_exporter`. Collection is
disabled by default and runs independently for every active, monitoring-enabled Asterisk server;
one unavailable exporter does not stop collection for other servers or AMI monitoring.

## Deployment model

Install and operate `node_exporter` on each target host using the host's supported package and
service-management process. AsterForge always requests the fixed `/metrics` path on the configured
port of the server's inventory hostname. It does not accept arbitrary telemetry URLs, paths, shell
commands, or remote execution parameters.

Restrict the exporter port at the network layer so only the AsterForge backend or its trusted
collector network can connect. The current adapter uses plaintext HTTP and implements neither TLS
nor exporter authentication; use a trusted private network and do not publish port 9100 to the
Internet. Leave telemetry disabled when that boundary cannot be provided.

The standard CPU, memory, filesystem, network-device and time collectors expose CPU, memory, swap,
load, filesystem capacity/inodes, bounded host-level network indicators and uptime. Network receive
and transmit bytes, errors and drops are summed across exported non-loopback devices into one object
per sample; interface names and per-interface series are not persisted. Byte rates need two samples
with increasing `node_time_seconds`. A counter reset leaves the affected rate `null` until the next
valid pair. Asterisk service state is available only when the systemd collector exports
`node_systemd_unit_state` for `asterisk.service`. Missing collectors or series produce `null` values
and absent capability names, never synthetic zeroes.

### Optional recording-generation failure input

Capacity or file-count changes cannot reliably prove that Asterisk failed to generate a recording.
For an explicit signal, an operator-controlled node_exporter textfile collector may publish these
label-free metrics from a trusted local detector:

```prometheus
# TYPE asterforge_recording_generation_failures_total counter
asterforge_recording_generation_failures_total 3
# TYPE asterforge_recording_generation_last_failure_timestamp_seconds gauge
asterforge_recording_generation_last_failure_timestamp_seconds 1789257600
```

The total must be a non-negative monotonic counter. The timestamp is optional Unix time for the most
recent detected failure. AsterForge reports the total and, after two valid samples, the count since
the previous sample plus `failure_detected`. A first sample or counter reset produces `null` for the
delta and detection result. If the total metric is absent, `recording_generation` is `null`; storage
growth is never treated as proof of recording success or failure.

## Configuration

Configure the backend through the environment variables documented in `.env.example`:

```dotenv
ASTERFORGE_HOST_TELEMETRY_ENABLED=true
ASTERFORGE_HOST_TELEMETRY_PORT=9100
ASTERFORGE_HOST_TELEMETRY_TIMEOUT_SECONDS=5
ASTERFORGE_HOST_TELEMETRY_POLL_INTERVAL_SECONDS=60
ASTERFORGE_HOST_TELEMETRY_INVENTORY_REFRESH_SECONDS=10
ASTERFORGE_HOST_TELEMETRY_RECORDING_MOUNTPOINT=/var/spool/asterisk/monitor
```

The port and recording mountpoint currently apply to every inventoried server. The recording path
must exactly match the `mountpoint` label exported by node_exporter. If recordings share a larger
filesystem, AsterForge reports that filesystem's capacity and growth, not the size of the recording
directory alone. CPU utilization needs two successful samples because it is calculated from
counter deltas.

Restart the backend after changing these settings. Samples are persisted at the polling interval;
the API calculates recording used bytes and growth from the latest two samples. Collection never
reads or deletes recording contents.

## API and authorization

Both endpoints require the `server.read` permission:

- `GET /api/v1/host/telemetry` returns the latest state for all active servers.
- `GET /api/v1/host/servers/{server_id}/telemetry` returns one active server or `404`.

Each result includes freshness/error state and the latest persisted sample. Capability names state
which metric families were present. Unsupported metrics remain `null`. `recording_storage`
includes capacity, inode availability, used-byte calculation, growth bytes, and the interval
between the two samples used for that calculation. `network` is the bounded host aggregate described
above. Network and recording-generation observations identify their provider metric source and use
the sample `observed_at` as collection freshness; `source_observed_at` additionally exposes
`node_time_seconds` when available.

The persisted/API contract also has an optional `voip_quality` object for trustworthy provider
observations: jitter, packet-loss percentage, RTT, MOS, codec and RTP packet counts, together with a
required source identifier and source timestamp. The node_exporter adapter does not infer or produce
these call-quality values. Until a verified RTCP/media-quality source is configured through a
provider, `voip_quality` is `null` and its capability is absent.

After a backend restart, a persisted sample can be returned while in-memory freshness is `unknown`
until the first new collection attempt. On a later scrape failure, the last successful sample is
retained and freshness becomes `stale`.

## Troubleshooting

If freshness is `unknown` with `host_telemetry_unavailable`, verify that telemetry is enabled, the
inventory hostname resolves inside the backend container, the configured port is reachable, the
exporter returns HTTP 200 at `/metrics`, and its response is no larger than 2 MiB. A missing metric
with otherwise fresh telemetry usually means the corresponding node_exporter collector or series
is not available; inspect the capability list before interpreting a `null` value. For recording
failure detection, also verify that the trusted textfile collector emits a label-free, finite,
non-negative counter and that `node_exporter` includes it in `/metrics`.
