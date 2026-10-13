# Alerts, incidents, and maintenance

AsterForge turns repeated monitoring observations into durable, deduplicated alerts and
understandable incident timelines. Evaluation is platform-local and remains read-only toward
Asterisk. The web console exposes alert acknowledgement, incident evidence, and maintenance-window
management according to the signed-in user's permissions.

## Initial rules

Migrations `0010_alerts_incidents_maintenance` and `0014_initial_alert_rules` seed the initial
validated rules:

| Rule | Opens when | Opens after | Clears after |
| --- | --- | ---: | ---: |
| `asterisk.connection.unavailable` | The AMI connection is unavailable or misconfigured | 30 s | 30 s healthy |
| `trunk.registration.unavailable` | An outbound registration is not `REGISTERED` | 30 s | 30 s healthy |
| `endpoint.availability.unavailable` | A supported PJSIP endpoint observation is not `AVAILABLE` | 30 s | 30 s healthy |
| `host.cpu.high` | CPU usage is at least 90% | 300 s | 60 s at or below 80% |
| `host.memory.high` | Used RAM is at least 90% | 300 s | 60 s at or below 80% |
| `host.filesystem.low` | A reported filesystem has at most 10% available | 300 s | 60 s at or above 15% |
| `recording.storage.low` | Recording storage has at most 10% available | 300 s | 60 s at or above 15% |
| `calls.failed_rate.high` | At least 20% of 10 or more known CDR outcomes in the prior 5 minutes are failures | 300 s | 60 s at or below 10% |
| `recording.generation.failures` | The reliable recording-failure counter increases by at least 1 | immediately | 60 s with no increase |

Numeric rules use separate open and clear thresholds to avoid flapping. A condition must
remain continuously unhealthy for its configured duration. A healthy sample resets a pending
open duration; an open alert resolves only after the clear debounce is satisfied. Missing
capabilities are not treated as healthy or failing observations. Endpoint rules run only when the
AMI snapshot declares endpoint support. RAM and filesystem rules require complete size inputs.
Recording-generation alerts require the provenance-bearing textfile collector counter; capacity or
growth alone is never inferred as a failure. Failed-call rate uses only configured PostgreSQL CDR
data with known `ANSWERED`, `BUSY`, `FAILED`, `NO ANSWER`, `CONGESTION`, or `CHANUNAVAIL`
dispositions, a bounded five-minute window, and a minimum of ten known outcomes. Unknown
dispositions and smaller/unconfigured inputs do not open or resolve an alert.

Evaluation is connected to AMI health observations, reconciled endpoint/outbound-registration
snapshots, persisted host/storage samples, and configured bounded CDR reads. Monitoring and
persistence continue during maintenance.

## Dedupe, lifecycle, and incidents

The active key combines the rule, server, resource type, and resource key. Repeated samples update
one active alert rather than opening duplicates. The supported lifecycle is:

```text
OPEN -> ACKNOWLEDGED -> RESOLVED
OPEN ----------------> RESOLVED
```

Acknowledgement requires a non-empty operator comment. Resolved alert history is retained; if the
same condition later reappears for a full opening duration, AsterForge creates a new alert cycle.
Opening, acknowledgement, and resolution append audit evidence.

Alerts on the same server and condition scope (`availability` or `capacity`) join the same active
incident. The incident timeline records incident creation, attached alerts, acknowledgement,
suppression changes, alert resolution, and final incident resolution in time order. An incident
resolves automatically only when all its attached alerts are resolved.

## Maintenance behavior

A maintenance window always targets one server and may optionally narrow the scope to an exact
`asterisk`, `calls`, `endpoint`, `filesystem`, `host`, `storage`, or `trunk` resource key. It has a UTC start/end interval and a
required reason. While a matching window is active, new and continuing alerts are still evaluated,
stored, correlated, and visible, but are marked as notification-suppressed with the matching
window ID. The next collected sample removes suppression automatically after the window expires.
Operators may end an active or future window from the console; both creation and early ending are
audited.

No outbound notification transport is implemented in this increment. The suppression flag is the
authoritative input for a future notification dispatcher; it does not hide alerts or stop data
collection.

## API and permissions

- `GET /api/v1/alerts` requires `alert.read`; optional `state`, `server_id`, and bounded `limit`
  filters are available.
- `POST /api/v1/alerts/{id}/acknowledge` requires `alert.acknowledge`.
- `GET /api/v1/incidents` and `GET /api/v1/incidents/{id}/timeline` require `incident.read`.
- `GET` and `POST /api/v1/maintenance-windows`, and
  `PATCH /api/v1/maintenance-windows/{id}/end`, require `maintenance.manage`.

All protected routes are authorized by the backend. Frontend navigation and buttons are only user
experience guards. Lists are bounded to prevent unbounded operational reads.

Authorized users with `monitoring_config.manage` can list and update these persisted rules from
the Monitoring configuration area or `/api/v1/monitoring-config/alert-rules`. Updates validate
condition-specific threshold ordering, duration/debounce bounds and severity, and append redacted
audit evidence containing changed field names rather than submitted values.

## Troubleshooting and limitations

- Rules begin evaluating only when their corresponding collector is enabled and producing samples.
- CDR alerts are reevaluated by normal monitoring polls; disabling both AMI and host telemetry also
  removes those periodic evaluation triggers.
- Incident correlation is deliberately deterministic and server/scope based. Manual correlation,
  opaque AI correlation, escalation policies, and outbound notification transports are not part of
  this task.
- If an alert remains marked suppressed after a maintenance window expires, verify that its source
  collector is still producing samples; suppression is reconciled on the next observation.
