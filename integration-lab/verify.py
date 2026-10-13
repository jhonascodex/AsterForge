"""Verify real Asterisk, node_exporter, and staged-log paths through AsterForge."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any
from uuid import UUID

from app.integrations.ami import AmiTarget, StreamAmiConnector
from app.integrations.host_telemetry import (
    HostTelemetryTarget,
    NodeExporterHostTelemetryProvider,
)

SERVER_ID = UUID("00000000-0000-4000-8000-000000000016")
BACKEND_URL = os.environ["ASTERFORGE_RELEASE_LAB_BACKEND_URL"].rstrip("/")
ASTERISK_HOST = os.environ["ASTERFORGE_RELEASE_LAB_ASTERISK_HOST"]
INITIAL_PASSWORD = "test-only-temporary-password"
CHANGED_PASSWORD = "test-only-changed-password"


def request(
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    token: str | None = None,
) -> tuple[int, Any]:
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    operation = urllib.request.Request(
        f"{BACKEND_URL}{path}", data=data, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(operation, timeout=5) as response:
            body = response.read()
            return response.status, json.loads(body) if body else None
    except urllib.error.HTTPError as error:
        body = error.read()
        return error.code, json.loads(body) if body else None


def wait_for(label: str, probe: Callable[[], Any], *, timeout: float = 90) -> Any:
    deadline = time.monotonic() + timeout
    last_value: Any = None
    while time.monotonic() < deadline:
        try:
            last_value = probe()
            if last_value:
                return last_value
        except (OSError, ValueError):
            pass
        time.sleep(1)
    raise RuntimeError(f"timed out waiting for {label}; last value={last_value!r}")


def ami_message(stream: Any) -> dict[str, str]:
    fields: dict[str, str] = {}
    while True:
        line = stream.readline()
        if not line:
            raise RuntimeError("AMI fixture connection closed")
        if line in {b"\r\n", b"\n"}:
            if fields:
                return fields
            continue
        key, separator, value = line.decode("utf-8").partition(":")
        if separator:
            fields[key.strip().casefold()] = value.strip()


def ami_action(username: str, password: str, action: str, **fields: str) -> dict[str, str]:
    with socket.create_connection((ASTERISK_HOST, 5038), timeout=5) as connection:
        connection.settimeout(5)
        stream = connection.makefile("rb")
        banner = stream.readline()
        if not banner.startswith(b"Asterisk Call Manager/"):
            raise RuntimeError("unexpected AMI fixture banner")

        def send(name: str, **values: str) -> dict[str, str]:
            lines = [f"Action: {name}", *(f"{key}: {value}" for key, value in values.items())]
            connection.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
            while True:
                response = ami_message(stream)
                if "response" in response:
                    return response

        login = send("Login", Username=username, Secret=password, Events="off")
        if login.get("response", "").casefold() != "success":
            raise RuntimeError(f"AMI fixture login failed for {username}")
        return send(action, **fields)


async def adapter_preflight() -> None:
    session = await StreamAmiConnector(connect_timeout=3, action_timeout=3).connect(
        AmiTarget(
            server_id=SERVER_ID,
            hostname=ASTERISK_HOST,
            port=5038,
            tls=False,
            username="asterforge-readonly",
            secret="test-only-read-password",
        )
    )
    try:
        core = await session.core_snapshot()
        pjsip = await session.pjsip_snapshot()
        channels = await session.live_channels_snapshot()
    finally:
        await session.close()
    if core.asterisk_version is None or "endpoints" not in pjsip.capabilities or channels:
        raise RuntimeError(
            "real AMI adapter preflight returned incomplete or non-baseline state"
        )
    telemetry_provider = NodeExporterHostTelemetryProvider(timeout=3)
    telemetry_target = HostTelemetryTarget(
        server_id=SERVER_ID,
        hostname=ASTERISK_HOST,
        port=9100,
        recording_mountpoint="/var/spool/asterisk/monitor",
    )
    await telemetry_provider.collect(telemetry_target)
    await asyncio.sleep(1)
    telemetry = await telemetry_provider.collect(telemetry_target)
    if telemetry.cpu_usage_percent is None or telemetry.network is None:
        raise RuntimeError(
            f"real node_exporter preflight lacked representative deltas: {telemetry!r}"
        )


def main() -> None:
    asyncio.run(adapter_preflight())
    wait_for(
        "backend readiness",
        lambda: request("GET", "/api/v1/health/ready")[0] == 200,
    )
    status, login = request(
        "POST",
        "/api/v1/auth/login",
        payload={"username": "release-lab-admin", "password": INITIAL_PASSWORD},
    )
    if status != 200 or not login.get("must_change_password"):
        raise RuntimeError(f"release-lab bootstrap login failed: {status} {login!r}")
    token = login["access_token"]
    status, _ = request(
        "POST",
        "/api/v1/auth/change-password",
        payload={"current_password": INITIAL_PASSWORD, "new_password": CHANGED_PASSWORD},
        token=token,
    )
    if status != 204:
        raise RuntimeError(f"release-lab password change failed: {status}")

    def health_probe() -> dict[str, Any] | None:
        status, body = request(
            "GET", f"/api/v1/asterisk/servers/{SERVER_ID}/health", token=token
        )
        if status == 200 and body["connection_state"] == "connected" and body["core"]:
            return body
        return None

    health = wait_for("fresh AMI core health", health_probe)
    if not str(health["core"]["asterisk_version"]).startswith("22."):
        raise RuntimeError(f"unexpected controlled Asterisk version: {health['core']!r}")

    def endpoint_probe() -> dict[str, Any] | None:
        status, body = request(
            "GET",
            f"/api/v1/asterisk/endpoints?server_id={SERVER_ID}&limit=100",
            token=token,
        )
        if status != 200:
            return None
        return next(
            (item for item in body["items"] if item["source_id"] == "lab-endpoint"), None
        )

    endpoint = wait_for("persisted PJSIP endpoint", endpoint_probe)
    if endpoint["stale"]:
        raise RuntimeError("controlled PJSIP endpoint is unexpectedly stale")

    def trunk_probe() -> dict[str, Any] | None:
        status, body = request(
            "GET",
            f"/api/v1/asterisk/trunks?server_id={SERVER_ID}&limit=100",
            token=token,
        )
        if status != 200:
            return None
        return next(
            (
                item
                for item in body["items"]
                if item["source_id"] == "lab-trunk-registration"
            ),
            None,
        )

    trunk = wait_for("persisted outbound trunk state", trunk_probe)
    if trunk["stale"] or trunk["state"] not in {
        "registered",
        "unregistered",
        "rejected",
        "stopped",
        "unknown",
    }:
        raise RuntimeError(f"invalid controlled trunk state: {trunk!r}")

    def telemetry_probe() -> dict[str, Any] | None:
        status, body = request(
            "GET", f"/api/v1/host/servers/{SERVER_ID}/telemetry", token=token
        )
        sample = body.get("sample") if status == 200 else None
        required = {
            "cpu",
            "memory",
            "filesystem",
            "network",
            "asterisk_service",
            "recording_storage",
            "recording_generation_failures",
        }
        if (
            sample
            and required.issubset(sample["capabilities"])
            and sample["cpu_usage_percent"] is not None
            and sample["network"]["receive_bytes_per_second"] is not None
        ):
            return body
        return None

    telemetry = wait_for("representative node_exporter observations", telemetry_probe)
    sample = telemetry["sample"]
    if sample["asterisk_service_state"] != "active":
        raise RuntimeError(f"unexpected controlled service state: {sample!r}")
    if sample["recording_storage"]["total_bytes"] != 104857600:
        raise RuntimeError(f"controlled recording filesystem was not observed: {sample!r}")
    if sample["recording_generation"]["failures_total"] != 2:
        raise RuntimeError(f"controlled recording failure signal was not observed: {sample!r}")

    def log_probe() -> dict[str, Any] | None:
        status, body = request("GET", "/api/v1/logs?q=release-lab-log", token=token)
        if status == 200 and body["items"]:
            return body["items"][0]
        return None

    log = wait_for("controlled Asterisk log ingestion", log_probe)
    if "lab-sensitive-value" in log["message"] or "password=[REDACTED]" not in log["message"]:
        raise RuntimeError(f"controlled log was not redacted: {log!r}")

    denied = ami_action(
        "asterforge-readonly",
        "test-only-read-password",
        "Originate",
        Channel="Local/7000@release-lab",
        Application="Wait",
        Data="15",
        Async="true",
    )
    if denied.get("response", "").casefold() != "error":
        raise RuntimeError("AsterForge read-only AMI account unexpectedly originated a call")

    originated = ami_action(
        "release-lab-fixture",
        "test-only-fixture-password",
        "Originate",
        Channel="Local/7000@release-lab",
        Application="Wait",
        Data="15",
        CallerID="AsterForge Lab <1000>",
        Async="true",
    )
    if originated.get("response", "").casefold() != "success":
        raise RuntimeError(f"fixture could not originate internal call: {originated!r}")

    def active_call_probe() -> dict[str, Any] | None:
        status, body = request("GET", "/api/v1/calls/live", token=token)
        if status != 200:
            return None
        server = next(
            (item for item in body["servers"] if item["server_id"] == str(SERVER_ID)), None
        )
        return server if server and server["calls"] else None

    active_calls = wait_for("live call observed through AsterForge", active_call_probe, timeout=20)

    def cleared_call_probe() -> bool:
        status, body = request("GET", "/api/v1/calls/live", token=token)
        if status != 200:
            return False
        server = next(
            (item for item in body["servers"] if item["server_id"] == str(SERVER_ID)), None
        )
        return bool(server and server["freshness"] == "fresh" and not server["calls"])

    wait_for("live call hangup reconciliation", cleared_call_probe, timeout=35)
    print(
        "release lab passed: "
        f"asterisk={health['core']['asterisk_version']} "
        f"endpoint={endpoint['source_id']} "
        f"trunk={trunk['source_id']}:{trunk['state']} "
        f"telemetry={','.join(sample['capabilities'])} "
        f"log={log['source']}:redacted "
        f"live_call_channels={active_calls['calls'][0]['channel_count']} "
        "readonly_originate=denied cleanup=pending"
    )


if __name__ == "__main__":
    main()
