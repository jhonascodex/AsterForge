"""Validate the canonical Podman stack in a uniquely owned disposable project."""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import time
import urllib.request
from pathlib import Path
from uuid import uuid4

PROJECT_PATTERN = re.compile(r"^asterforge-smoke-[0-9a-f]{12}$")


def run(
    command: list[str],
    *,
    environment: dict[str, str],
    check: bool = True,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command,
        env=environment,
        check=False,
        text=True,
        capture_output=capture,
    )
    if check and completed.returncode != 0:
        raise subprocess.CalledProcessError(
            completed.returncode, command, completed.stdout, completed.stderr
        )
    return completed


def available_port() -> int:
    with socket.socket() as candidate:
        candidate.bind(("127.0.0.1", 0))
        return int(candidate.getsockname()[1])


def wait_json(url: str, expected: str) -> None:
    deadline = time.monotonic() + 90
    last_error: BaseException | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                payload = json.load(response)
            if payload.get("status") == expected:
                return
        except (OSError, ValueError) as exc:
            last_error = exc
        time.sleep(2)
    raise RuntimeError(f"health check did not reach {expected}: {url}") from last_error


def wait_http_ok(url: str) -> None:
    deadline = time.monotonic() + 90
    last_error: BaseException | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                if response.status == 200:
                    return
        except OSError as exc:
            last_error = exc
        time.sleep(2)
    raise RuntimeError(f"HTTP check did not reach 200: {url}") from last_error


def main() -> int:
    project = f"asterforge-smoke-{uuid4().hex[:12]}"
    if not PROJECT_PATTERN.fullmatch(project):
        raise RuntimeError("refusing an invalid smoke project name")
    repository = Path(__file__).resolve().parent.parent
    frontend_port = available_port()
    backend_port = available_port()
    compose_file = repository / f".compose-{project}.yaml"
    if compose_file.parent != repository or compose_file.exists():
        raise RuntimeError("refusing an unsafe or colliding smoke compose path")
    compose_text = (repository / "compose.yaml").read_text(encoding="utf-8")
    compose_text = compose_text.replace(
        '"127.0.0.1:8000:8000"', f'"127.0.0.1:{backend_port}:8000"'
    ).replace(
        '"127.0.0.1:4173:4173"', f'"127.0.0.1:{frontend_port}:4173"'
    )
    if f"127.0.0.1:{backend_port}:8000" not in compose_text or (
        f"127.0.0.1:{frontend_port}:4173" not in compose_text
    ):
        raise RuntimeError("canonical host-port declarations were not found")
    compose_file.write_text(compose_text, encoding="utf-8")
    environment = os.environ.copy()
    environment.update(
        {
            "POSTGRES_DB": "asterforge_smoke",
            "POSTGRES_USER": "asterforge_smoke",
            "POSTGRES_PASSWORD": "smoke-test-only-password",
            "ASTERFORGE_DATABASE_URL": "postgresql+asyncpg://asterforge_smoke:smoke-test-only-password@database:5432/asterforge_smoke",
            "ASTERFORGE_ENVIRONMENT": "test",
            "ASTERFORGE_CORS_ORIGINS": '["http://localhost:4173"]',
            "ASTERFORGE_SESSION_COOKIE_SECURE": "false",
            "ASTERFORGE_INTEGRATION_ENCRYPTION_KEY": "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
            "ASTERFORGE_BOOTSTRAP_ADMIN_USERNAME": "release-smoke-admin",
            "ASTERFORGE_BOOTSTRAP_ADMIN_PASSWORD": "smoke test temporary password",
        }
    )
    compose = [
        "podman",
        "compose",
        "--project-name",
        project,
        "--file",
        str(compose_file),
    ]
    label = f"io.podman.compose.project={project}"
    collision = run(
        ["podman", "ps", "--all", "--quiet", "--filter", f"label={label}"],
        environment=environment,
        capture=True,
    ).stdout.strip()
    if collision:
        raise RuntimeError("refusing to reuse an existing smoke project")
    failure: BaseException | None = None
    try:
        run([*compose, "config", "--quiet"], environment=environment)
        run([*compose, "build"], environment=environment)
        run([*compose, "up", "--detach", "database"], environment=environment)
        run([*compose, "run", "--rm", "migrate"], environment=environment)
        run([*compose, "up", "--detach", "backend", "frontend"], environment=environment)
        wait_json(f"http://127.0.0.1:{backend_port}/api/v1/health/live", "ok")
        wait_json(f"http://127.0.0.1:{backend_port}/api/v1/health/ready", "ready")
        wait_http_ok(f"http://127.0.0.1:{frontend_port}/")
        backend_uid = run(
            [*compose, "exec", "-T", "backend", "id", "-u"],
            environment=environment,
            capture=True,
        ).stdout.strip()
        frontend_uid = run(
            [*compose, "exec", "-T", "frontend", "id", "-u"],
            environment=environment,
            capture=True,
        ).stdout.strip()
        if backend_uid == "0" or frontend_uid == "0":
            raise RuntimeError("application container unexpectedly runs as root")
        database_port = run(
            [*compose, "port", "database", "5432"],
            environment=environment,
            check=False,
            capture=True,
        ).stdout.strip()
        if database_port:
            raise RuntimeError("database port is unexpectedly published")
        query = [
            *compose,
            "exec",
            "-T",
            "database",
            "sh",
            "-c",
            'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT version_num FROM alembic_version"',
        ]
        before = run(query, environment=environment, capture=True).stdout.strip()
        run(
            [*compose, "up", "--detach", "--force-recreate", "database"],
            environment=environment,
        )
        time.sleep(8)
        after = run(query, environment=environment, capture=True).stdout.strip()
        if before != "0014_initial_alert_rules" or after != before:
            raise RuntimeError("database volume did not preserve the migrated schema")
        print(
            "compose smoke passed: health=ok/ready frontend=200 "
            f"uids={backend_uid}/{frontend_uid} database_port=internal persistence={after} "
            f"host_ports={frontend_port}/{backend_port}"
        )
    except BaseException as exc:  # noqa: BLE001 - cleanup must run on interruption.
        failure = exc
    finally:
        cleanup = run(
            [*compose, "down", "--volumes", "--remove-orphans"],
            environment=environment,
            check=False,
        )
        compose_file.unlink(missing_ok=True)
    if failure is not None:
        raise failure
    if cleanup.returncode != 0:
        raise RuntimeError("smoke passed but exact-project cleanup failed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except subprocess.CalledProcessError as exc:
        raise SystemExit(exc.returncode) from None
