"""Run PostgreSQL tests in a uniquely named, disposable Podman Compose project."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from uuid import uuid4

PROJECT_PATTERN = re.compile(r"^asterforge-test-[0-9a-f]{12}$")


def run(command: list[str], *, environment: dict[str, str], check: bool = True) -> int:
    completed = subprocess.run(command, env=environment, check=False)
    if check and completed.returncode != 0:
        raise subprocess.CalledProcessError(completed.returncode, command)
    return completed.returncode


def main() -> int:
    repository = Path(__file__).resolve().parent.parent
    compose_file = repository / "compose.test.yaml"
    project = f"asterforge-test-{uuid4().hex[:12]}"
    if not PROJECT_PATTERN.fullmatch(project):
        raise RuntimeError("refusing to manage an invalid test project name")

    environment = os.environ.copy()
    environment["ASTERFORGE_TEST_RUN_ID"] = project
    compose = [
        "podman",
        "compose",
        "--project-name",
        project,
        "--file",
        str(compose_file),
    ]
    failure: BaseException | None = None
    try:
        run([*compose, "up", "--detach", "test-database"], environment=environment)
        run([*compose, "run", "--rm", "migrate-test"], environment=environment)
        run([*compose, "run", "--rm", "backend-tests"], environment=environment)
    except BaseException as exc:  # noqa: BLE001 - cleanup must run on interruption.
        failure = exc
    finally:
        # Only the generated, test-exclusive project can reach this destructive operation.
        cleanup_status = run(
            [*compose, "down", "--volumes", "--remove-orphans"],
            environment=environment,
            check=False,
        )

    if failure is not None:
        raise failure
    if cleanup_status != 0:
        raise RuntimeError("PostgreSQL tests passed but disposable test cleanup failed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except subprocess.CalledProcessError as exc:
        raise SystemExit(exc.returncode) from None
