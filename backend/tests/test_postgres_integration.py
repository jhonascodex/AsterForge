import asyncio
import os
import re
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import AuditEvent, User
from app.services.bootstrap import ensure_initial_administrator

pytestmark = pytest.mark.skipif(
    os.environ.get("ASTERFORGE_RUN_POSTGRES_TESTS") != "1",
    reason="requires the disposable PostgreSQL test stack",
)


def _disposable_database_url() -> str:
    if os.environ.get("ASTERFORGE_ENVIRONMENT") != "test":
        raise RuntimeError("PostgreSQL integration tests require the test environment")
    run_id = os.environ.get("ASTERFORGE_TEST_RUN_ID", "")
    if re.fullmatch(r"asterforge-test-[0-9a-f]{12}", run_id) is None:
        raise RuntimeError("PostgreSQL integration tests require an isolated runner ID")
    database_url = os.environ["ASTERFORGE_DATABASE_URL"]
    parsed = make_url(database_url)
    if parsed.database != "asterforge_test" or parsed.host != "test-database":
        raise RuntimeError("refusing a PostgreSQL target outside the disposable test stack")
    return database_url


def test_migrations_commits_and_bootstrap_use_disposable_postgresql() -> None:
    database_url = _disposable_database_url()

    async def exercise() -> None:
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        username = f"bootstrap-{os.environ['ASTERFORGE_TEST_RUN_ID'][-12:]}"
        try:
            async with engine.connect() as connection:
                revision = await connection.scalar(text("SELECT version_num FROM alembic_version"))
                environment_codes = list(
                    (
                        await connection.scalars(
                            text("SELECT code FROM inventory_environments ORDER BY code")
                        )
                    ).all()
                )
                alert_rules = list(
                    (
                        await connection.execute(
                            text(
                                "SELECT code, threshold, clear_threshold "
                                "FROM alert_rules ORDER BY code"
                            )
                        )
                    ).all()
                )
            expected_revision = ScriptDirectory.from_config(
                Config("alembic.ini")
            ).get_current_head()
            assert revision == expected_revision
            assert environment_codes == ["DEV", "PROD", "QA"]
            assert {row.code for row in alert_rules} == {
                "asterisk.connection.unavailable",
                "calls.failed_rate.high",
                "endpoint.availability.unavailable",
                "host.cpu.high",
                "host.filesystem.low",
                "host.memory.high",
                "recording.generation.failures",
                "recording.storage.low",
                "trunk.registration.unavailable",
            }
            failed_rate = next(
                row for row in alert_rules if row.code == "calls.failed_rate.high"
            )
            assert (failed_rate.threshold, failed_rate.clear_threshold) == (20.0, 10.0)

            server_id = uuid4()
            started_from = datetime.now(UTC) - timedelta(days=1)
            started_to = datetime.now(UTC)
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "INSERT INTO asterisk_servers "
                        "(id, name, name_key, hostname, environment_id, created_at, updated_at) "
                        "SELECT :server_id, 'Plan PBX', :name_key, 'plan.test', id, :now, :now "
                        "FROM inventory_environments ORDER BY code LIMIT 1"
                    ),
                    {"server_id": server_id, "name_key": f"plan-{server_id}", "now": started_to},
                )
                telemetry_id = uuid4()
                await connection.execute(
                    text(
                        "INSERT INTO host_telemetry_samples "
                        "(id, server_id, provider, capabilities_json, filesystems_json, "
                        "recording_mountpoint, network_interface_count, "
                        "network_receive_bytes_total, network_source, "
                        "network_source_observed_at, recording_failures_total, "
                        "recording_failures_since_previous_sample, recording_failure_source, "
                        "recording_failure_source_observed_at, voip_jitter_ms, "
                        "voip_packet_loss_percent, voip_rtt_ms, voip_mos, voip_codec, "
                        "voip_source, voip_source_observed_at, observed_at) VALUES "
                        "(:id, :server_id, 'node_exporter', "
                        "'[\"network\",\"recording_generation_failures\",\"voip_quality\"]', "
                        "'[]', '/recordings', 1, 1234, 'node_exporter:network', :now, "
                        "2, 1, 'node_exporter:textfile:test', :now, 4.5, 0.2, 18, 4.3, "
                        "'ulaw', 'rtcp_probe:test', :now, :now)"
                    ),
                    {"id": telemetry_id, "server_id": server_id, "now": started_to},
                )
                telemetry_row = (
                    await connection.execute(
                        text(
                            "SELECT network_receive_bytes_total, recording_failures_total, "
                            "voip_packet_loss_percent, voip_source "
                            "FROM host_telemetry_samples WHERE id = :id"
                        ),
                        {"id": telemetry_id},
                    )
                ).one()
                assert tuple(telemetry_row) == (1234, 2, 0.2, "rtcp_probe:test")
                await connection.execute(
                    text(
                        "INSERT INTO call_detail_records "
                        "(id, server_id, source_id, unique_id, linked_id, started_at) "
                        "SELECT md5('cdr-' || value)::uuid, :server_id, 'cdr-' || value, "
                        "'unique-' || value, "
                        "CASE WHEN value = 1 THEN 'test-call' ELSE 'linked-' || value END, "
                        "CAST(:started_to AS timestamptz) - make_interval(secs => value) "
                        "FROM generate_series(1, 5000) AS value"
                    ),
                    {"server_id": server_id, "started_to": started_to},
                )
                await connection.execute(
                    text(
                        "INSERT INTO channel_event_records "
                        "(id, server_id, source_id, event_type, event_at, unique_id, linked_id) "
                        "SELECT md5('cel-' || value)::uuid, :server_id, 'cel-' || value, "
                        "'TEST', CAST(:started_to AS timestamptz) - make_interval(secs => value), "
                        "'unique-' || value, "
                        "CASE WHEN value = 1 THEN 'test-call' ELSE 'linked-' || value END "
                        "FROM generate_series(1, 5000) AS value"
                    ),
                    {"server_id": server_id, "started_to": started_to},
                )
                await connection.execute(text("ANALYZE call_detail_records"))
                await connection.execute(text("ANALYZE channel_event_records"))
                cdr_plan_rows = await connection.execute(
                    text(
                        "EXPLAIN SELECT id FROM call_detail_records "
                        "WHERE server_id = :server_id AND linked_id = :correlation_id "
                        "AND started_at >= :started_from AND started_at < :started_to"
                    ),
                    {
                        "server_id": server_id,
                        "correlation_id": "test-call",
                        "started_from": started_from,
                        "started_to": started_to,
                    },
                )
                cel_plan_rows = await connection.execute(
                    text(
                        "EXPLAIN SELECT id FROM channel_event_records "
                        "WHERE server_id = :server_id AND linked_id = :correlation_id "
                        "AND event_at >= :started_from AND event_at < :started_to"
                    ),
                    {
                        "server_id": server_id,
                        "correlation_id": "test-call",
                        "started_from": started_from,
                        "started_to": started_to,
                    },
                )
                cdr_plan = "\n".join(str(row[0]) for row in cdr_plan_rows)
                cel_plan = "\n".join(str(row[0]) for row in cel_plan_rows)
                assert "ix_cdr_server_linked_started" in cdr_plan
                assert "ix_cel_server_linked_time" in cel_plan

            async with factory() as session:
                assert await ensure_initial_administrator(
                    session,
                    username=username,
                    temporary_password="isolated temporary password",
                )
            async with factory() as session:
                stored_user = await session.scalar(select(User).where(User.username == username))
                audit_action = await session.scalar(
                    select(AuditEvent.action).where(AuditEvent.target_id == str(stored_user.id))
                ) if stored_user is not None else None
                assert stored_user is not None
                assert stored_user.must_change_password
                assert audit_action == "user.bootstrap_created"
                audit_id = await session.scalar(select(AuditEvent.id).limit(1))
                assert audit_id is not None
                with pytest.raises(DBAPIError, match="append-only"):
                    await session.execute(
                        text("UPDATE audit_events SET action = 'tampered' WHERE id = :id"),
                        {"id": audit_id},
                    )
                    await session.commit()
                await session.rollback()
                assert await session.get(AuditEvent, audit_id) is not None
        finally:
            await engine.dispose()

    asyncio.run(exercise())
