# Asterisk server inventory

AsterForge provides an authenticated REST inventory for Asterisk targets, deployment environments,
and tags. This feature stores connection metadata for later monitoring tasks; it does not connect
to AMI or change any Asterisk system.

## Permissions

All inventory routes require a valid bearer session and enforce permissions in the backend:

- `server.read` permits listing and reading servers, environments, and tags.
- `server.manage` permits creating, updating, archiving, and deleting supported metadata.

The initial `Super Administrator` profile has both permissions. Read permission does not imply
manage permission, and frontend visibility is never the authorization boundary.

## Product UI workflow

After signing in, users with the server.read permission can open **Infrastructure > Server
inventory** to list and filter registered targets by name/hostname/location, environment, tag,
monitoring state, and archival state. This is separate from **Monitor > Servers**, which remains
the operational monitoring and drill-down view. An empty filtered result is shown independently
from an API or transport failure.

The same **Infrastructure** area contains the canonical destinations in this order: **Server
inventory**, **Environments**, and **Tags**. The latter two are dedicated catalog views. Users with
server.read can inspect each catalog and the number of associated servers; users with server.manage
can create and edit both classifications, enable or disable environments, delete unused
environments, and delete tags. Environment codes become immutable after creation. Deleting a tag
removes its server associations, while deleting an environment still fails when any server record
(including an archived record) references it. Counts include those preserved archived records so
the displayed usage remains consistent with that data-integrity constraint.

A clean installation seeds DEV, QA, and PROD. Additional environments use the same validated API
contract and require no SQL or source changes. A deployment with no custom tags shows an explicit
empty state and a create-first-tag action to authorized administrators. Loading, empty, transport/API
error, read-only management, and fully unauthorized states are presented separately in both views.

Users who also have server.manage can explicitly register a server, edit its supported metadata,
enable or disable monitoring, replace or clear AMI credentials, and archive the record. Registration
requires a name, hostname/IP, and enabled environment; location, tags, AMI port/TLS, monitoring
state, and a complete AMI username/secret pair are optional. AsterForge does not scan the network
or discover Asterisk targets automatically.

Server create/edit forms select one enabled Environment and zero or more Tags. Editing restores the
persisted assignments; clearing all Tag checkboxes persists an empty assignment. The page consumes
the existing reusable catalogs but does not embed their administration controls. If no Tags exist,
the form explicitly explains that Tags are optional. Catalog loading and API-error states remain
distinct from an empty catalog. User-defined codes, names, Tags, and logical-location text are not
translated. Logical location remains free-form server metadata and is not a managed catalog.

Stored AMI values are never populated into the browser form. The UI shows only whether a complete
credential pair is configured. To rotate credentials, enter both new values; leaving both blank
preserves the current pair. Clearing is an explicit option shown only for an existing configured
server. Secrets are not stored in browser persistence.

## Resources and behavior

| Resource | Endpoints | Behavior |
| --- | --- | --- |
| Environments | `GET/POST /api/v1/environments`, `PATCH/DELETE /api/v1/environments/{id}` | DEV, QA, and PROD are seeded; additional codes can be created without schema changes. An environment assigned to a server cannot be deleted. List entries include `server_count`. |
| Tags | `GET/POST /api/v1/server-tags`, `PATCH/DELETE /api/v1/server-tags/{id}` | Names are trimmed and unique without regard to case. Deleting a tag removes only its inventory associations. List entries include `server_count`. |
| Servers | `GET/POST /api/v1/servers`, `GET/PATCH/DELETE /api/v1/servers/{id}` | Server names are unique without regard to case. `DELETE` archives and disables monitoring rather than removing the record. |

Environment and Tag catalog maintenance is available through both the authorized API and the
dedicated product views. Catalog CRUD remains outside the Server inventory page.

Server creation accepts `name`, `hostname`, `environment_id`, optional `ami_port` (default `5038`),
optional `ami_tls` (default `false`), `location`, `monitoring_enabled`, `tag_ids`, and an optional
complete `ami_username`/`ami_secret` pair. Updating a server accepts the same mutable metadata. Use
`clear_ami_credentials: true` to remove both stored credentials. Archived servers are immutable.

`GET /api/v1/servers` returns a paginated object with `items`, `total`, `limit`, and `offset`.
Supported filters are exact environment code (`environment`), case-insensitive tag name (`tag`),
monitoring state (`monitoring_enabled`), and name/hostname/location substring (`search`). Archived records are
hidden by default; authorized clients can request `include_archived=true`. Page size defaults to 50
and is limited to 200.

When `ami_tls` is enabled, the monitoring worker uses certificate and hostname verification.
Disabling monitoring changes only future collection eligibility. It does not archive the server or
delete inventory/history. Archiving also disables monitoring and preserves the server record for
historical references.

## Credential encryption and redaction

The canonical backend requires `ASTERFORGE_INTEGRATION_ENCRYPTION_KEY`, a URL-safe base64 Fernet
key encoding exactly 32 random bytes. Generate one in a trusted Python environment with the pinned
backend dependencies installed:

```console
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Capture the output directly into an approved deployment secret store or the untracked `.env` for
local evaluation. Do not paste it into source control, logs, screenshots, tickets, or API requests.
Keep the key stable and backed up: changing or losing it prevents future monitoring components from
decrypting credentials already stored with that key.

AMI username and secret inputs are encrypted before persistence. API responses expose only
`ami_username_configured` and `ami_secret_configured`; they never return either credential or its
ciphertext. Audit records report only non-secret changed-field names and use `ami_credentials` as a
generic marker for credential changes.

## Audit and current limitations

Successful environment, tag, and server mutations append `inventory.*` audit events. There is no
public audit-query endpoint yet. AMI connection health and freshness are documented in
[Asterisk core monitoring](asterisk-monitoring.md).

OpenAPI at `/openapi.json` and generated documentation at `/docs` remain the canonical schema for
request and response bodies.
