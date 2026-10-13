from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

profile_permissions = Table(
    "profile_permissions",
    Base.metadata,
    Column("profile_id", ForeignKey("profiles.id", ondelete="CASCADE"), primary_key=True),
    Column("permission_id", ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True),
)

user_profiles = Table(
    "user_profiles",
    Base.metadata,
    Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("profile_id", ForeignKey("profiles.id", ondelete="CASCADE"), primary_key=True),
)

asterisk_server_tags = Table(
    "asterisk_server_tags",
    Base.metadata,
    Column("server_id", ForeignKey("asterisk_servers.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", ForeignKey("server_tags.id", ondelete="CASCADE"), primary_key=True),
)

incident_alerts = Table(
    "incident_alerts",
    Base.metadata,
    Column("incident_id", ForeignKey("incidents.id", ondelete="CASCADE"), primary_key=True),
    Column("alert_id", ForeignKey("alerts.id", ondelete="CASCADE"), primary_key=True),
)


class Permission(Base):
    __tablename__ = "permissions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    code: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(String(255), nullable=False)


class Profile(Base):
    __tablename__ = "profiles"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    permissions: Mapped[list[Permission]] = relationship(
        secondary=profile_permissions, lazy="selectin"
    )
    users: Mapped[list[User]] = relationship(
        secondary=user_profiles, back_populates="profiles", lazy="selectin"
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(150), nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    profiles: Mapped[list[Profile]] = relationship(
        secondary=user_profiles, back_populates="users", lazy="selectin"
    )
    sessions: Mapped[list[AuthSession]] = relationship(back_populates="user")


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    continuation_token_hash: Mapped[str | None] = mapped_column(
        String(64), unique=True
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user: Mapped[User] = relationship(back_populates="sessions", lazy="selectin")
    access_tokens: Mapped[list[AuthSessionAccessToken]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class AuthSessionAccessToken(Base):
    __tablename__ = "auth_session_access_tokens"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("auth_sessions.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    session: Mapped[AuthSession] = relationship(back_populates="access_tokens")


class LoginAttempt(Base):
    __tablename__ = "login_attempts"

    identifier_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    failed_count: Mapped[int] = mapped_column(default=0, nullable=False)
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    blocked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (UniqueConstraint("id", "occurred_at"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    actor_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    target_type: Mapped[str] = mapped_column(String(50), nullable=False)
    target_id: Mapped[str] = mapped_column(String(100), nullable=False)
    outcome: Mapped[str] = mapped_column(String(20), default="success", nullable=False)
    source: Mapped[str] = mapped_column(String(50), default="platform", nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(100))
    metadata_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)


class RetentionPolicy(Base):
    __tablename__ = "retention_policies"
    __table_args__ = (
        CheckConstraint("retention_days >= 1 AND retention_days <= 3650"),
        CheckConstraint("batch_size >= 1 AND batch_size <= 10000"),
    )

    category: Mapped[str] = mapped_column(String(50), primary_key=True)
    retention_days: Mapped[int] = mapped_column(Integer, nullable=False)
    batch_size: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )


class InventoryEnvironment(Base):
    __tablename__ = "inventory_environments"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    servers: Mapped[list[AsteriskServer]] = relationship(back_populates="environment")


class ServerTag(Base):
    __tablename__ = "server_tags"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    name_key: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    servers: Mapped[list[AsteriskServer]] = relationship(
        secondary=asterisk_server_tags, back_populates="tags"
    )


class AsteriskServer(Base):
    __tablename__ = "asterisk_servers"
    __table_args__ = (CheckConstraint("ami_port >= 1 AND ami_port <= 65535"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    name_key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    hostname: Mapped[str] = mapped_column(String(253), nullable=False)
    ami_port: Mapped[int] = mapped_column(Integer, default=5038, nullable=False)
    ami_tls: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    environment_id: Mapped[UUID] = mapped_column(
        ForeignKey("inventory_environments.id", ondelete="RESTRICT"), index=True
    )
    location: Mapped[str | None] = mapped_column(String(150))
    monitoring_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    ami_username_encrypted: Mapped[str | None] = mapped_column(Text)
    ami_secret_encrypted: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    environment: Mapped[InventoryEnvironment] = relationship(
        back_populates="servers", lazy="selectin"
    )
    tags: Mapped[list[ServerTag]] = relationship(
        secondary=asterisk_server_tags, back_populates="servers", lazy="selectin"
    )


class PjsipEndpoint(Base):
    __tablename__ = "pjsip_endpoints"
    __table_args__ = (
        UniqueConstraint("server_id", "source_id"),
        Index("ix_pjsip_endpoints_server_state", "server_id", "state"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    server_id: Mapped[UUID] = mapped_column(
        ForeignKey("asterisk_servers.id", ondelete="CASCADE"), index=True
    )
    source_id: Mapped[str] = mapped_column(String(255), nullable=False)
    state: Mapped[str] = mapped_column(String(30), nullable=False)
    raw_state: Mapped[str | None] = mapped_column(String(100))
    transport: Mapped[str | None] = mapped_column(String(255))
    aors: Mapped[str | None] = mapped_column(Text)
    active_channels: Mapped[int | None] = mapped_column(Integer)
    contacts_count: Mapped[int | None] = mapped_column(Integer)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    stale: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class PjsipContact(Base):
    __tablename__ = "pjsip_contacts"
    __table_args__ = (
        UniqueConstraint("server_id", "source_id"),
        Index("ix_pjsip_contacts_server_state", "server_id", "state"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    server_id: Mapped[UUID] = mapped_column(
        ForeignKey("asterisk_servers.id", ondelete="CASCADE"), index=True
    )
    source_id: Mapped[str] = mapped_column(String(500), nullable=False)
    endpoint_name: Mapped[str | None] = mapped_column(String(255), index=True)
    aor: Mapped[str | None] = mapped_column(String(255))
    uri: Mapped[str | None] = mapped_column(Text)
    address: Mapped[str | None] = mapped_column(String(255))
    port: Mapped[int | None] = mapped_column(Integer)
    user_agent: Mapped[str | None] = mapped_column(String(500))
    state: Mapped[str] = mapped_column(String(30), nullable=False)
    raw_state: Mapped[str | None] = mapped_column(String(100))
    roundtrip_usec: Mapped[int | None] = mapped_column(Integer)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    stale: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class PjsipRegistration(Base):
    __tablename__ = "pjsip_registrations"
    __table_args__ = (
        UniqueConstraint("server_id", "direction", "source_id"),
        Index("ix_pjsip_registrations_server_state", "server_id", "state"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    server_id: Mapped[UUID] = mapped_column(
        ForeignKey("asterisk_servers.id", ondelete="CASCADE"), index=True
    )
    source_id: Mapped[str] = mapped_column(String(500), nullable=False)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    endpoint_name: Mapped[str | None] = mapped_column(String(255), index=True)
    server_uri: Mapped[str | None] = mapped_column(Text)
    client_uri: Mapped[str | None] = mapped_column(Text)
    transport: Mapped[str | None] = mapped_column(String(255))
    state: Mapped[str] = mapped_column(String(30), nullable=False)
    raw_state: Mapped[str | None] = mapped_column(String(100))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    stale: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class PjsipTransport(Base):
    __tablename__ = "pjsip_transports"
    __table_args__ = (UniqueConstraint("server_id", "source_id"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    server_id: Mapped[UUID] = mapped_column(
        ForeignKey("asterisk_servers.id", ondelete="CASCADE"), index=True
    )
    source_id: Mapped[str] = mapped_column(String(255), nullable=False)
    protocol: Mapped[str | None] = mapped_column(String(30))
    bind: Mapped[str | None] = mapped_column(String(255))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    stale: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class SipTrunk(Base):
    __tablename__ = "sip_trunks"
    __table_args__ = (
        UniqueConstraint("server_id", "source_id"),
        Index("ix_sip_trunks_server_state", "server_id", "state"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    server_id: Mapped[UUID] = mapped_column(
        ForeignKey("asterisk_servers.id", ondelete="CASCADE"), index=True
    )
    source_id: Mapped[str] = mapped_column(String(255), nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    provider: Mapped[str | None] = mapped_column(String(255))
    state: Mapped[str] = mapped_column(String(30), nullable=False)
    raw_state: Mapped[str | None] = mapped_column(String(100))
    endpoint_name: Mapped[str | None] = mapped_column(String(255))
    server_uri: Mapped[str | None] = mapped_column(Text)
    last_state_change_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    stale: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class TrunkStateTransition(Base):
    __tablename__ = "trunk_state_transitions"
    __table_args__ = (Index("ix_trunk_transitions_trunk_time", "trunk_id", "occurred_at"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    trunk_id: Mapped[UUID] = mapped_column(
        ForeignKey("sip_trunks.id", ondelete="CASCADE"), nullable=False
    )
    from_state: Mapped[str | None] = mapped_column(String(30))
    to_state: Mapped[str] = mapped_column(String(30), nullable=False)
    raw_state: Mapped[str | None] = mapped_column(String(100))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CallDetailRecord(Base):
    __tablename__ = "call_detail_records"
    __table_args__ = (
        UniqueConstraint("server_id", "source_id"),
        CheckConstraint("duration_seconds IS NULL OR duration_seconds >= 0"),
        CheckConstraint("billable_seconds IS NULL OR billable_seconds >= 0"),
        Index("ix_cdr_server_started", "server_id", "started_at"),
        Index("ix_cdr_server_linked_started", "server_id", "linked_id", "started_at"),
        Index("ix_cdr_server_unique_started", "server_id", "unique_id", "started_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    server_id: Mapped[UUID] = mapped_column(
        ForeignKey("asterisk_servers.id", ondelete="CASCADE"), nullable=False
    )
    source_id: Mapped[str] = mapped_column(String(255), nullable=False)
    unique_id: Mapped[str | None] = mapped_column(String(255))
    linked_id: Mapped[str | None] = mapped_column(String(255))
    caller_number: Mapped[str | None] = mapped_column(String(255))
    connected_number: Mapped[str | None] = mapped_column(String(255))
    disposition: Mapped[str | None] = mapped_column(String(100))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    billable_seconds: Mapped[int | None] = mapped_column(Integer)


class ChannelEventRecord(Base):
    __tablename__ = "channel_event_records"
    __table_args__ = (
        UniqueConstraint("server_id", "source_id"),
        Index("ix_cel_server_event_time", "server_id", "event_at"),
        Index("ix_cel_server_linked_time", "server_id", "linked_id", "event_at"),
        Index("ix_cel_server_unique_time", "server_id", "unique_id", "event_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    server_id: Mapped[UUID] = mapped_column(
        ForeignKey("asterisk_servers.id", ondelete="CASCADE"), nullable=False
    )
    source_id: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    unique_id: Mapped[str | None] = mapped_column(String(255))
    linked_id: Mapped[str | None] = mapped_column(String(255))
    channel: Mapped[str | None] = mapped_column(String(500))
    peer: Mapped[str | None] = mapped_column(String(500))
    caller_number: Mapped[str | None] = mapped_column(String(255))
    connected_number: Mapped[str | None] = mapped_column(String(255))


class HostTelemetrySample(Base):
    __tablename__ = "host_telemetry_samples"
    __table_args__ = (
        CheckConstraint(
            "cpu_usage_percent IS NULL OR "
            "(cpu_usage_percent >= 0 AND cpu_usage_percent <= 100)"
        ),
        CheckConstraint(
            "voip_packet_loss_percent IS NULL OR "
            "(voip_packet_loss_percent >= 0 AND voip_packet_loss_percent <= 100)"
        ),
        CheckConstraint("voip_mos IS NULL OR (voip_mos >= 1 AND voip_mos <= 5)"),
        CheckConstraint(
            "network_interface_count IS NULL OR network_interface_count >= 0"
        ),
        CheckConstraint(
            "recording_failures_total IS NULL OR recording_failures_total >= 0"
        ),
        CheckConstraint(
            "recording_failures_since_previous_sample IS NULL OR "
            "recording_failures_since_previous_sample >= 0"
        ),
        CheckConstraint(
            "network_interface_count IS NULL OR network_source IS NOT NULL"
        ),
        CheckConstraint(
            "recording_failures_total IS NULL OR recording_failure_source IS NOT NULL"
        ),
        CheckConstraint("voip_jitter_ms IS NULL OR voip_jitter_ms >= 0"),
        CheckConstraint("voip_rtt_ms IS NULL OR voip_rtt_ms >= 0"),
        CheckConstraint(
            "(voip_source IS NULL AND voip_source_observed_at IS NULL AND "
            "voip_jitter_ms IS NULL AND voip_packet_loss_percent IS NULL AND "
            "voip_rtt_ms IS NULL AND voip_mos IS NULL AND voip_codec IS NULL AND "
            "voip_rtp_receive_packets IS NULL AND voip_rtp_transmit_packets IS NULL) OR "
            "(voip_source IS NOT NULL AND voip_source_observed_at IS NOT NULL)"
        ),
        Index("ix_host_telemetry_server_time", "server_id", "observed_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    server_id: Mapped[UUID] = mapped_column(
        ForeignKey("asterisk_servers.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    capabilities_json: Mapped[str] = mapped_column(Text, nullable=False)
    cpu_usage_percent: Mapped[float | None] = mapped_column(Float)
    memory_total_bytes: Mapped[int | None] = mapped_column(BigInteger)
    memory_available_bytes: Mapped[int | None] = mapped_column(BigInteger)
    swap_total_bytes: Mapped[int | None] = mapped_column(BigInteger)
    swap_free_bytes: Mapped[int | None] = mapped_column(BigInteger)
    load_1: Mapped[float | None] = mapped_column(Float)
    load_5: Mapped[float | None] = mapped_column(Float)
    load_15: Mapped[float | None] = mapped_column(Float)
    uptime_seconds: Mapped[float | None] = mapped_column(Float)
    asterisk_service_state: Mapped[str | None] = mapped_column(String(30))
    filesystems_json: Mapped[str] = mapped_column(Text, nullable=False)
    recording_mountpoint: Mapped[str] = mapped_column(String(500), nullable=False)
    recording_total_bytes: Mapped[int | None] = mapped_column(BigInteger)
    recording_available_bytes: Mapped[int | None] = mapped_column(BigInteger)
    recording_files: Mapped[int | None] = mapped_column(BigInteger)
    recording_files_free: Mapped[int | None] = mapped_column(BigInteger)
    network_interface_count: Mapped[int | None] = mapped_column(Integer)
    network_receive_bytes_total: Mapped[int | None] = mapped_column(BigInteger)
    network_transmit_bytes_total: Mapped[int | None] = mapped_column(BigInteger)
    network_receive_errors_total: Mapped[int | None] = mapped_column(BigInteger)
    network_transmit_errors_total: Mapped[int | None] = mapped_column(BigInteger)
    network_receive_dropped_total: Mapped[int | None] = mapped_column(BigInteger)
    network_transmit_dropped_total: Mapped[int | None] = mapped_column(BigInteger)
    network_receive_bytes_per_second: Mapped[float | None] = mapped_column(Float)
    network_transmit_bytes_per_second: Mapped[float | None] = mapped_column(Float)
    network_source: Mapped[str | None] = mapped_column(String(100))
    network_source_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recording_failures_total: Mapped[int | None] = mapped_column(BigInteger)
    recording_failures_since_previous_sample: Mapped[int | None] = mapped_column(BigInteger)
    recording_last_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recording_failure_source: Mapped[str | None] = mapped_column(String(150))
    recording_failure_source_observed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    voip_jitter_ms: Mapped[float | None] = mapped_column(Float)
    voip_packet_loss_percent: Mapped[float | None] = mapped_column(Float)
    voip_rtt_ms: Mapped[float | None] = mapped_column(Float)
    voip_mos: Mapped[float | None] = mapped_column(Float)
    voip_codec: Mapped[str | None] = mapped_column(String(100))
    voip_rtp_receive_packets: Mapped[int | None] = mapped_column(BigInteger)
    voip_rtp_transmit_packets: Mapped[int | None] = mapped_column(BigInteger)
    voip_source: Mapped[str | None] = mapped_column(String(150))
    voip_source_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LogEvent(Base):
    __tablename__ = "log_events"
    __table_args__ = (
        CheckConstraint(
            "level IN ('DEBUG','NOTICE','INFO','WARNING','ERROR','CRITICAL')"
        ),
        Index("ix_log_events_server_time", "server_id", "occurred_at"),
        Index("ix_log_events_level_time", "level", "occurred_at"),
        Index("ix_log_events_source_time", "source", "occurred_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    server_id: Mapped[UUID] = mapped_column(
        ForeignKey("asterisk_servers.id", ondelete="CASCADE"), nullable=False
    )
    source: Mapped[str] = mapped_column(String(100), nullable=False)
    level: Mapped[str] = mapped_column(String(20), nullable=False)
    module: Mapped[str | None] = mapped_column(String(255))
    message: Mapped[str] = mapped_column(Text, nullable=False)
    endpoint: Mapped[str | None] = mapped_column(String(255))
    trunk: Mapped[str | None] = mapped_column(String(255))
    unique_id: Mapped[str | None] = mapped_column(String(255))
    linked_id: Mapped[str | None] = mapped_column(String(255))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LogSourceCursor(Base):
    __tablename__ = "log_source_cursors"
    __table_args__ = (
        UniqueConstraint("server_id", "source"),
        CheckConstraint("byte_offset >= 0"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    server_id: Mapped[UUID] = mapped_column(
        ForeignKey("asterisk_servers.id", ondelete="CASCADE"), nullable=False
    )
    source: Mapped[str] = mapped_column(String(100), nullable=False)
    byte_offset: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AlertRule(Base):
    __tablename__ = "alert_rules"
    __table_args__ = (
        CheckConstraint("severity IN ('INFO','WARNING','CRITICAL')"),
        CheckConstraint("duration_seconds >= 0"),
        CheckConstraint("debounce_seconds >= 0"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    code: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    condition_kind: Mapped[str] = mapped_column(String(50), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    threshold: Mapped[float | None] = mapped_column(Float)
    clear_threshold: Mapped[float | None] = mapped_column(Float)
    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    debounce_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MaintenanceWindow(Base):
    __tablename__ = "maintenance_windows"
    __table_args__ = (
        CheckConstraint("ends_at > starts_at"),
        CheckConstraint(
            "(resource_type IS NULL AND resource_key IS NULL) OR "
            "(resource_type IS NOT NULL AND resource_key IS NOT NULL)"
        ),
        Index("ix_maintenance_server_time", "server_id", "starts_at", "ends_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    server_id: Mapped[UUID] = mapped_column(
        ForeignKey("asterisk_servers.id", ondelete="CASCADE"), nullable=False
    )
    resource_type: Mapped[str | None] = mapped_column(String(50))
    resource_key: Mapped[str | None] = mapped_column(String(255))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Alert(Base):
    __tablename__ = "alerts"
    __table_args__ = (
        CheckConstraint("severity IN ('INFO','WARNING','CRITICAL')"),
        CheckConstraint("state IN ('OPEN','ACKNOWLEDGED','RESOLVED')"),
        Index("ix_alerts_server_state_seen", "server_id", "state", "last_seen"),
        Index("ix_alerts_resource", "resource_type", "resource_key"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    rule_id: Mapped[UUID] = mapped_column(
        ForeignKey("alert_rules.id", ondelete="RESTRICT"), nullable=False
    )
    server_id: Mapped[UUID] = mapped_column(
        ForeignKey("asterisk_servers.id", ondelete="CASCADE"), nullable=False
    )
    resource_type: Mapped[str] = mapped_column(String(50), nullable=False)
    resource_key: Mapped[str] = mapped_column(String(255), nullable=False)
    condition_scope: Mapped[str] = mapped_column(String(100), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(500), nullable=False)
    active_key: Mapped[str | None] = mapped_column(String(500), unique=True)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    acknowledgement_comment: Mapped[str | None] = mapped_column(String(500))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    condition_metadata_json: Mapped[str] = mapped_column(Text, nullable=False)
    suppressed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    maintenance_window_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("maintenance_windows.id", ondelete="SET NULL")
    )


class AlertConditionState(Base):
    __tablename__ = "alert_condition_states"

    dedupe_key: Mapped[str] = mapped_column(String(500), primary_key=True)
    rule_id: Mapped[UUID] = mapped_column(
        ForeignKey("alert_rules.id", ondelete="CASCADE"), nullable=False
    )
    server_id: Mapped[UUID] = mapped_column(
        ForeignKey("asterisk_servers.id", ondelete="CASCADE"), nullable=False
    )
    resource_type: Mapped[str] = mapped_column(String(50), nullable=False)
    resource_key: Mapped[str] = mapped_column(String(255), nullable=False)
    active_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    clear_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_is_firing: Mapped[bool] = mapped_column(Boolean, nullable=False)
    last_value_json: Mapped[str] = mapped_column(Text, nullable=False)
    last_evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Incident(Base):
    __tablename__ = "incidents"
    __table_args__ = (
        CheckConstraint("state IN ('OPEN','RESOLVED')"),
        Index("ix_incidents_server_state_opened", "server_id", "state", "opened_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    server_id: Mapped[UUID] = mapped_column(
        ForeignKey("asterisk_servers.id", ondelete="CASCADE"), nullable=False
    )
    correlation_key: Mapped[str] = mapped_column(String(500), nullable=False)
    active_key: Mapped[str | None] = mapped_column(String(500), unique=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IncidentEvent(Base):
    __tablename__ = "incident_events"
    __table_args__ = (Index("ix_incident_events_incident_time", "incident_id", "occurred_at"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    incident_id: Mapped[UUID] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False
    )
    alert_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("alerts.id", ondelete="SET NULL")
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actor_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False)
