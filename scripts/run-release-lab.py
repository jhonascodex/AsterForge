"""Run the release integration lab in an exactly owned disposable Podman project."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from uuid import uuid4

PROJECT_PATTERN = re.compile(r"^asterforge-release-lab-[0-9a-f]{12}$")


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


def exists(command: list[str], *, environment: dict[str, str]) -> bool:
    return run(command, environment=environment, check=False).returncode == 0


def main() -> int:
    repository = Path(__file__).resolve().parent.parent
    compose_file = repository / "compose.release-lab.yaml"
    project = f"asterforge-release-lab-{uuid4().hex[:12]}"
    if not PROJECT_PATTERN.fullmatch(project):
        raise RuntimeError("refusing to manage an invalid release-lab project name")
    if compose_file.parent != repository or not compose_file.is_file():
        raise RuntimeError("release-lab Compose file is outside the repository or missing")

    environment = os.environ.copy()
    compose = [
        "podman",
        "compose",
        "--project-name",
        project,
        "--file",
        str(compose_file),
    ]
    container_label = f"io.podman.compose.project={project}"
    network_name = f"{project}_lab-internal"
    volume_names = (
        f"{project}_lab_postgres_data",
        f"{project}_lab_asterisk_logs",
    )
    collision = run(
        ["podman", "ps", "--all", "--quiet", "--filter", f"label={container_label}"],
        environment=environment,
        capture=True,
    ).stdout.strip()
    if collision or exists(["podman", "network", "exists", network_name], environment=environment):
        raise RuntimeError("refusing to reuse an existing release-lab project")
    if any(
        exists(["podman", "volume", "exists", volume], environment=environment)
        for volume in volume_names
    ):
        raise RuntimeError("refusing to reuse an existing release-lab volume")

    failure: BaseException | None = None
    try:
        print(f"release-lab project: {project}")
        run([*compose, "config", "--quiet"], environment=environment)
        run([*compose, "build", "lab-asterisk", "lab-backend"], environment=environment)
        run(
            [*compose, "up", "--detach", "lab-database", "lab-asterisk"],
            environment=environment,
        )
        run([*compose, "run", "--rm", "lab-migrate"], environment=environment)
        run([*compose, "run", "--rm", "lab-seed"], environment=environment)
        run([*compose, "up", "--detach", "lab-backend"], environment=environment)
        run([*compose, "run", "--rm", "lab-verify"], environment=environment)
    except BaseException as exc:  # noqa: BLE001 - cleanup must run on interruption.
        failure = exc
    finally:
        if failure is not None:
            run(
                [
                    *compose,
                    "logs",
                    "--no-color",
                    "--tail",
                    "200",
                    "lab-asterisk",
                    "lab-backend",
                ],
                environment=environment,
                check=False,
            )
        cleanup = run(
            [*compose, "down", "--volumes", "--remove-orphans"],
            environment=environment,
            check=False,
        )

    residue = run(
        ["podman", "ps", "--all", "--quiet", "--filter", f"label={container_label}"],
        environment=environment,
        capture=True,
    ).stdout.strip()
    residue_exists = bool(residue) or exists(
        ["podman", "network", "exists", network_name], environment=environment
    )
    residue_exists = residue_exists or any(
        exists(["podman", "volume", "exists", volume], environment=environment)
        for volume in volume_names
    )
    if failure is not None:
        raise failure
    if cleanup.returncode != 0 or residue_exists:
        raise RuntimeError("release lab passed but exact-project cleanup was incomplete")
    print("release lab cleanup passed: containers=0 networks=0 volumes=0 persisted_data=0")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except subprocess.CalledProcessError as exc:
        if exc.stdout:
            print(exc.stdout)
        if exc.stderr:
            print(exc.stderr)
        raise SystemExit(exc.returncode) from None
