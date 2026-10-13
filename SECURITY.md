# Security policy

## Supported versions

AsterForge has not published a stable release. Security fixes target the current maintained V1
branch until a version support table is announced in a release.

## Reporting a vulnerability

Do not open a public issue or include exploit details in logs, screenshots, or discussions. Use the
repository host's private vulnerability-reporting feature when it is available. If the public host
does not offer private reporting, contact the maintainers through the private contact channel
published on that host before sharing details. The repository currently has no verified security
mailbox, so this document deliberately does not invent one.

Include the affected version/commit, deployment shape, reproduction steps, impact, and a minimal
proof of concept with secrets and personal data removed. Maintainers should acknowledge receipt,
coordinate disclosure, and publish remediation/version guidance through the private report and
`CHANGELOG.md`.

## Deployment expectations

Production operators must terminate TLS, enable secure continuation cookies, restrict CORS and AMI
network access, use least-privilege integration accounts, protect `.env` and backups, retain the
integration encryption key, and keep dependencies and base images patched. AsterForge must never
be granted Asterisk mutation permissions for V1.
