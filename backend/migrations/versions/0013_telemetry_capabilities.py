"""Add bounded network, recording-failure and VoIP quality observations."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_telemetry_capabilities"
down_revision: str | None = "0012_audit_retention"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    columns = (
        sa.Column("network_interface_count", sa.Integer()),
        sa.Column("network_receive_bytes_total", sa.BigInteger()),
        sa.Column("network_transmit_bytes_total", sa.BigInteger()),
        sa.Column("network_receive_errors_total", sa.BigInteger()),
        sa.Column("network_transmit_errors_total", sa.BigInteger()),
        sa.Column("network_receive_dropped_total", sa.BigInteger()),
        sa.Column("network_transmit_dropped_total", sa.BigInteger()),
        sa.Column("network_receive_bytes_per_second", sa.Float()),
        sa.Column("network_transmit_bytes_per_second", sa.Float()),
        sa.Column("network_source", sa.String(100)),
        sa.Column("network_source_observed_at", sa.DateTime(timezone=True)),
        sa.Column("recording_failures_total", sa.BigInteger()),
        sa.Column("recording_failures_since_previous_sample", sa.BigInteger()),
        sa.Column("recording_last_failure_at", sa.DateTime(timezone=True)),
        sa.Column("recording_failure_source", sa.String(150)),
        sa.Column("recording_failure_source_observed_at", sa.DateTime(timezone=True)),
        sa.Column("voip_jitter_ms", sa.Float()),
        sa.Column("voip_packet_loss_percent", sa.Float()),
        sa.Column("voip_rtt_ms", sa.Float()),
        sa.Column("voip_mos", sa.Float()),
        sa.Column("voip_codec", sa.String(100)),
        sa.Column("voip_rtp_receive_packets", sa.BigInteger()),
        sa.Column("voip_rtp_transmit_packets", sa.BigInteger()),
        sa.Column("voip_source", sa.String(150)),
        sa.Column("voip_source_observed_at", sa.DateTime(timezone=True)),
    )
    for column in columns:
        op.add_column("host_telemetry_samples", column)
    op.create_check_constraint(
        "ck_host_telemetry_network_count",
        "host_telemetry_samples",
        "network_interface_count IS NULL OR network_interface_count >= 0",
    )
    op.create_check_constraint(
        "ck_host_telemetry_recording_failures",
        "host_telemetry_samples",
        "(recording_failures_total IS NULL OR recording_failures_total >= 0) AND "
        "(recording_failures_since_previous_sample IS NULL OR "
        "recording_failures_since_previous_sample >= 0)",
    )
    op.create_check_constraint(
        "ck_host_telemetry_network_provenance",
        "host_telemetry_samples",
        "network_interface_count IS NULL OR network_source IS NOT NULL",
    )
    op.create_check_constraint(
        "ck_host_telemetry_recording_failure_provenance",
        "host_telemetry_samples",
        "recording_failures_total IS NULL OR recording_failure_source IS NOT NULL",
    )
    op.create_check_constraint(
        "ck_host_telemetry_voip_quality",
        "host_telemetry_samples",
        "(voip_jitter_ms IS NULL OR voip_jitter_ms >= 0) AND "
        "(voip_packet_loss_percent IS NULL OR "
        "(voip_packet_loss_percent >= 0 AND voip_packet_loss_percent <= 100)) AND "
        "(voip_rtt_ms IS NULL OR voip_rtt_ms >= 0) AND "
        "(voip_mos IS NULL OR (voip_mos >= 1 AND voip_mos <= 5))",
    )
    op.create_check_constraint(
        "ck_host_telemetry_voip_provenance",
        "host_telemetry_samples",
        "(voip_source IS NULL AND voip_source_observed_at IS NULL AND "
        "voip_jitter_ms IS NULL AND voip_packet_loss_percent IS NULL AND "
        "voip_rtt_ms IS NULL AND voip_mos IS NULL AND voip_codec IS NULL AND "
        "voip_rtp_receive_packets IS NULL AND voip_rtp_transmit_packets IS NULL) OR "
        "(voip_source IS NOT NULL AND voip_source_observed_at IS NOT NULL)",
    )


def downgrade() -> None:
    for name in (
        "ck_host_telemetry_voip_provenance",
        "ck_host_telemetry_voip_quality",
        "ck_host_telemetry_recording_failure_provenance",
        "ck_host_telemetry_network_provenance",
        "ck_host_telemetry_recording_failures",
        "ck_host_telemetry_network_count",
    ):
        op.drop_constraint(name, "host_telemetry_samples", type_="check")
    for name in (
        "voip_source_observed_at",
        "voip_source",
        "voip_rtp_transmit_packets",
        "voip_rtp_receive_packets",
        "voip_codec",
        "voip_mos",
        "voip_rtt_ms",
        "voip_packet_loss_percent",
        "voip_jitter_ms",
        "recording_failure_source_observed_at",
        "recording_failure_source",
        "recording_last_failure_at",
        "recording_failures_since_previous_sample",
        "recording_failures_total",
        "network_source_observed_at",
        "network_source",
        "network_transmit_bytes_per_second",
        "network_receive_bytes_per_second",
        "network_transmit_dropped_total",
        "network_receive_dropped_total",
        "network_transmit_errors_total",
        "network_receive_errors_total",
        "network_transmit_bytes_total",
        "network_receive_bytes_total",
        "network_interface_count",
    ):
        op.drop_column("host_telemetry_samples", name)
