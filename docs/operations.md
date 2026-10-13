# Podman operations and maintenance

Run commands from the repository root. Copy `.env.example` to the untracked `.env`, replace every
placeholder, then validate configuration before creating containers:

```console
podman compose config
podman compose build
podman compose up -d database
podman compose run --rm migrate
podman compose up -d backend frontend
podman compose ps
```

The supported host endpoints are `127.0.0.1:4173` (frontend) and `127.0.0.1:8000` (API).
PostgreSQL has no host-published port. Production requires a same-site TLS reverse proxy, exact
CORS origins, and `ASTERFORGE_SESSION_COOKIE_SECURE=true`.

## Start, stop, and health

```console
podman compose up -d database
podman compose run --rm migrate
podman compose up -d backend frontend
podman compose ps
curl --fail http://localhost:8000/api/v1/health/live
curl --fail http://localhost:8000/api/v1/health/ready
podman compose down
```

`down` preserves the named PostgreSQL volume. Never add `--volumes` unless permanent deletion of
the deployment database is intended.

## Backup and restore

Back up the database before an update. The following POSIX-shell commands use the credentials
already present inside the database container and create a plain SQL backup outside the container:

```sh
podman compose exec -T database sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner' > asterforge-backup.sql
test -s asterforge-backup.sql
```

Store the backup together with a protected copy of the deployment `.env`, especially the stable
integration encryption key. A database backup without that key cannot decrypt saved AMI
credentials.

Restore only into an empty or intentionally replaceable AsterForge database, with application
containers stopped:

```sh
podman compose stop backend frontend
podman compose exec -T database sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < asterforge-backup.sql
podman compose run --rm migrate
podman compose up -d backend frontend
```

Test restores in a separate deployment before relying on a backup procedure. Do not point the
automated PostgreSQL test runner at a deployment database.

## Upgrade and rollback

1. Read `CHANGELOG.md` and back up PostgreSQL and the encryption key.
2. Stop the web containers with `podman compose stop backend frontend`.
3. Update the checked-out source to the intended reviewed release.
4. Run `podman compose build`, start `database`, and run the one-shot `migrate` service.
5. Start `backend frontend`; verify readiness, login, and representative monitoring pages.

Alembic migrations move the database forward. V1 does not promise automatic application rollback
across schema changes. Restore the pre-upgrade database backup and the matching source/images if a
release rollback is required.

## Monitoring and troubleshooting

```console
podman compose ps
podman compose logs --tail 100 database
podman compose logs --tail 100 backend
podman compose logs --tail 100 frontend
```

- HTTP 503 from readiness means the API cannot complete its PostgreSQL check.
- Repeated AMI disconnections are isolated per server; verify target DNS/routing, TLS selection,
  port, least-privilege AMI credentials, and Asterisk ACLs.
- Missing host telemetry does not imply AMI failure. Verify node_exporter reachability and enabled
  collectors separately.
- Log-source errors require the exact `log-sources/<server UUID>/messages` path and readable host
  file. The mount is read-only to AsterForge.
- A lost integration encryption key requires re-entering AMI credentials; rotating the key without
  re-encryption makes existing ciphertext unusable.
