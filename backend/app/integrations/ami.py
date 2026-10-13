import asyncio
import hashlib
import ssl
from dataclasses import dataclass
from enum import StrEnum
from secrets import token_hex
from typing import Protocol
from uuid import UUID

MAX_AMI_MESSAGE_BYTES = 65_536
MAX_AMI_FIELDS = 256
MAX_AMI_LIST_ITEMS = 10_000
_ALLOWED_ACTIONS = frozenset(
    {
        "Login",
        "Ping",
        "CoreSettings",
        "CoreStatus",
        "CoreShowChannels",
        "PJSIPShowEndpoints",
        "PJSIPShowContacts",
        "PJSIPShowRegistrationInboundContactStatuses",
        "PJSIPShowRegistrationsOutbound",
        "Logoff",
    }
)


class AmiError(RuntimeError):
    """Sanitized base error for AMI transport and protocol failures."""


class AmiProtocolError(AmiError):
    pass


class AmiAuthenticationError(AmiError):
    pass


@dataclass(frozen=True)
class AmiTarget:
    server_id: UUID
    hostname: str
    port: int
    tls: bool
    username: str
    secret: str


@dataclass(frozen=True)
class AmiCoreSnapshot:
    system_name: str | None
    asterisk_version: str | None
    started_at_source: str | None
    reloaded_at_source: str | None
    current_calls: int | None
    max_calls: int | None


@dataclass(frozen=True)
class AmiChannelSnapshot:
    unique_id: str
    linked_id: str | None
    channel: str | None
    state: str | None
    caller_number: str | None
    connected_number: str | None


@dataclass(frozen=True)
class AmiCallEvent:
    event_type: str
    unique_id: str
    linked_id: str | None
    channel: str | None
    state: str | None
    caller_number: str | None
    connected_number: str | None
    destination_unique_id: str | None = None
    destination_linked_id: str | None = None


class EndpointState(StrEnum):
    UNKNOWN = "unknown"
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    IN_USE = "in_use"


class ContactState(StrEnum):
    UNKNOWN = "unknown"
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    NON_QUALIFIED = "non_qualified"


class RegistrationState(StrEnum):
    UNKNOWN = "unknown"
    REGISTERED = "registered"
    UNREGISTERED = "unregistered"
    REJECTED = "rejected"
    STOPPED = "stopped"


@dataclass(frozen=True)
class AmiEndpointSnapshot:
    source_id: str
    state: EndpointState
    raw_state: str | None
    transport: str | None
    aors: str | None
    active_channels: int | None
    contacts_count: int | None


@dataclass(frozen=True)
class AmiContactSnapshot:
    source_id: str
    endpoint_name: str | None
    aor: str | None
    uri: str | None
    address: str | None
    port: int | None
    user_agent: str | None
    state: ContactState
    raw_state: str | None
    roundtrip_usec: int | None


@dataclass(frozen=True)
class AmiRegistrationSnapshot:
    source_id: str
    direction: str
    endpoint_name: str | None
    server_uri: str | None
    client_uri: str | None
    transport: str | None
    state: RegistrationState
    raw_state: str | None


@dataclass(frozen=True)
class AmiTransportSnapshot:
    source_id: str
    protocol: str | None = None
    bind: str | None = None


@dataclass(frozen=True)
class AmiPjsipSnapshot:
    endpoints: tuple[AmiEndpointSnapshot, ...]
    contacts: tuple[AmiContactSnapshot, ...]
    registrations: tuple[AmiRegistrationSnapshot, ...]
    transports: tuple[AmiTransportSnapshot, ...]
    capabilities: frozenset[str]


class AmiSession(Protocol):
    async def core_snapshot(self) -> AmiCoreSnapshot: ...

    async def pjsip_snapshot(self) -> AmiPjsipSnapshot: ...

    async def live_channels_snapshot(self) -> tuple[AmiChannelSnapshot, ...]: ...

    async def drain_call_events(self) -> tuple[AmiCallEvent, ...]: ...

    async def wait_call_events(self, wait_seconds: float) -> tuple[AmiCallEvent, ...]: ...

    async def ping(self) -> None: ...

    async def close(self) -> None: ...


class AmiConnector(Protocol):
    async def connect(self, target: AmiTarget) -> AmiSession: ...


def parse_ami_message(payload: bytes) -> dict[str, str]:
    if len(payload) > MAX_AMI_MESSAGE_BYTES:
        raise AmiProtocolError("AMI message exceeds the size limit")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AmiProtocolError("AMI message is not valid UTF-8") from exc
    fields: dict[str, str] = {}
    lines = [line for line in text.replace("\r\n", "\n").split("\n") if line]
    if len(lines) > MAX_AMI_FIELDS:
        raise AmiProtocolError("AMI message has too many fields")
    for line in lines:
        key, separator, value = line.partition(":")
        if not separator or not key.strip():
            raise AmiProtocolError("AMI message contains a malformed field")
        fields[key.strip().casefold()] = value.lstrip()
    if not fields:
        raise AmiProtocolError("AMI message is empty")
    return fields


def _optional_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed >= 0 else None


def _csv_count(value: str | None) -> int | None:
    if value is None:
        return None
    return len([item for item in value.split(",") if item.strip()])


def normalize_core_snapshot(fields: dict[str, str]) -> AmiCoreSnapshot:
    def combined(date_key: str, time_key: str) -> str | None:
        date_value = fields.get(date_key)
        time_value = fields.get(time_key)
        return f"{date_value} {time_value}" if date_value and time_value else None

    return AmiCoreSnapshot(
        system_name=fields.get("systemname") or None,
        asterisk_version=fields.get("asteriskversion") or None,
        started_at_source=combined("corestartupdate", "corestartuptime"),
        reloaded_at_source=combined("corereloaddate", "corereloadtime"),
        current_calls=_optional_int(fields.get("corecurrentcalls")),
        max_calls=_optional_int(fields.get("coremaxcalls")),
    )


def normalize_channel_snapshot(fields: dict[str, str]) -> AmiChannelSnapshot | None:
    unique_id = _identity(fields.get("uniqueid"), 255)
    if unique_id is None:
        return None
    return AmiChannelSnapshot(
        unique_id=unique_id,
        linked_id=_identity(fields.get("linkedid"), 255),
        channel=_bounded(fields.get("channel"), 500),
        state=_bounded(fields.get("channelstatedesc"), 100),
        caller_number=_bounded(fields.get("calleridnum"), 255),
        connected_number=_bounded(fields.get("connectedlinenum"), 255),
    )


_CALL_EVENTS = frozenset(
    {
        "newchannel",
        "newstate",
        "newcallerid",
        "newconnectedline",
        "dialbegin",
        "bridgeenter",
        "bridgeleave",
        "hangup",
    }
)


def normalize_call_event(fields: dict[str, str]) -> AmiCallEvent | None:
    event_type = fields.get("event", "").strip().casefold()
    unique_id = _identity(fields.get("uniqueid"), 255)
    if event_type not in _CALL_EVENTS or unique_id is None:
        return None
    return AmiCallEvent(
        event_type=event_type,
        unique_id=unique_id,
        linked_id=_identity(fields.get("linkedid"), 255),
        channel=_bounded(fields.get("channel"), 500),
        state=_bounded(fields.get("channelstatedesc"), 100),
        caller_number=_bounded(fields.get("calleridnum"), 255),
        connected_number=_bounded(fields.get("connectedlinenum"), 255),
        destination_unique_id=_identity(fields.get("destuniqueid"), 255),
        destination_linked_id=_identity(fields.get("destlinkedid"), 255),
    )


def _bounded(value: str | None, limit: int) -> str | None:
    if not value:
        return None
    cleaned = value.strip()
    return cleaned[:limit] or None


def _identity(value: str | None, limit: int) -> str | None:
    cleaned = _bounded(value, MAX_AMI_MESSAGE_BYTES)
    if cleaned is None or len(cleaned) <= limit:
        return cleaned
    digest = hashlib.sha256(cleaned.encode()).hexdigest()[:16]
    return f"{cleaned[: limit - 17]}#{digest}"


def normalize_endpoint_state(value: str | None) -> EndpointState:
    normalized = (value or "").strip().casefold().replace("-", "_").replace(" ", "_")
    if normalized == "not_inuse":
        return EndpointState.AVAILABLE
    if normalized in {"invalid", "unavailable"}:
        return EndpointState.UNAVAILABLE
    if normalized in {"inuse", "busy", "ringing", "ringinuse", "onhold"}:
        return EndpointState.IN_USE
    return EndpointState.UNKNOWN


def normalize_contact_state(value: str | None) -> ContactState:
    normalized = (value or "").strip().casefold().replace("-", "_").replace(" ", "_")
    if normalized == "reachable":
        return ContactState.AVAILABLE
    if normalized in {"unreachable", "removed"}:
        return ContactState.UNAVAILABLE
    if normalized in {"nonqualified", "non_qualified", "unqualified"}:
        return ContactState.NON_QUALIFIED
    return ContactState.UNKNOWN


def normalize_registration_state(value: str | None) -> RegistrationState:
    normalized = (value or "").strip().casefold()
    return {
        "registered": RegistrationState.REGISTERED,
        "unregistered": RegistrationState.UNREGISTERED,
        "rejected": RegistrationState.REJECTED,
        "stopped": RegistrationState.STOPPED,
    }.get(normalized, RegistrationState.UNKNOWN)


def _endpoint(fields: dict[str, str]) -> AmiEndpointSnapshot | None:
    source_id = _identity(fields.get("objectname"), 255)
    if source_id is None:
        return None
    raw_state = _bounded(fields.get("devicestate"), 100)
    return AmiEndpointSnapshot(
        source_id=source_id,
        state=normalize_endpoint_state(raw_state),
        raw_state=raw_state,
        transport=_bounded(fields.get("transport"), 255),
        aors=_bounded(fields.get("aor"), MAX_AMI_MESSAGE_BYTES),
        active_channels=_optional_int(fields.get("activechannels")),
        contacts_count=_csv_count(fields.get("contacts")),
    )


def _contact(fields: dict[str, str]) -> AmiContactSnapshot | None:
    uri = _bounded(fields.get("uri"), MAX_AMI_MESSAGE_BYTES)
    source_id = _identity(fields.get("objectname") or uri, 500)
    if source_id is None:
        return None
    raw_state = _bounded(fields.get("status"), 100)
    return AmiContactSnapshot(
        source_id=source_id,
        endpoint_name=_bounded(fields.get("endpoint"), 255),
        aor=_bounded(fields.get("aor"), 255),
        uri=uri,
        address=_bounded(fields.get("viaaddr"), 255),
        port=_optional_int(fields.get("viaport")),
        user_agent=_bounded(fields.get("useragent"), 500),
        state=normalize_contact_state(raw_state),
        raw_state=raw_state,
        roundtrip_usec=_optional_int(fields.get("roundtripusec")),
    )


def _inbound_registration(fields: dict[str, str]) -> AmiRegistrationSnapshot | None:
    aor = _bounded(fields.get("aor"), 255)
    uri = _bounded(fields.get("uri"), MAX_AMI_MESSAGE_BYTES)
    source_id = _identity(fields.get("id") or f"{aor or ''}|{uri or ''}", 500)
    if source_id is None or source_id == "|":
        return None
    raw_state = _bounded(fields.get("status"), 100)
    return AmiRegistrationSnapshot(
        source_id=source_id,
        direction="inbound",
        endpoint_name=_bounded(fields.get("endpointname"), 255),
        server_uri=None,
        client_uri=uri,
        transport=None,
        state=(
            RegistrationState.REGISTERED
            if normalize_contact_state(raw_state) is ContactState.AVAILABLE
            else RegistrationState.UNREGISTERED
            if normalize_contact_state(raw_state)
            in {ContactState.UNAVAILABLE, ContactState.NON_QUALIFIED}
            else RegistrationState.UNKNOWN
        ),
        raw_state=raw_state,
    )


def _outbound_registration(fields: dict[str, str]) -> AmiRegistrationSnapshot | None:
    source_id = _identity(fields.get("objectname"), 500)
    if source_id is None:
        return None
    raw_state = _bounded(fields.get("status"), 100)
    return AmiRegistrationSnapshot(
        source_id=source_id,
        direction="outbound",
        endpoint_name=_bounded(fields.get("endpoint"), 255),
        server_uri=_bounded(fields.get("serveruri"), MAX_AMI_MESSAGE_BYTES),
        client_uri=_bounded(fields.get("clienturi"), MAX_AMI_MESSAGE_BYTES),
        transport=_bounded(fields.get("transport"), 255),
        state=normalize_registration_state(raw_state),
        raw_state=raw_state,
    )


class StreamAmiSession:
    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        *,
        action_timeout: float,
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._action_timeout = action_timeout
        self._closed = False
        self._call_events: list[AmiCallEvent] = []

    def _capture_call_event(self, message: dict[str, str]) -> None:
        event = normalize_call_event(message)
        if event is not None and len(self._call_events) < MAX_AMI_LIST_ITEMS:
            self._call_events.append(event)

    async def _read_message(self) -> dict[str, str]:
        parts: list[bytes] = []
        size = 0
        while True:
            try:
                line = await asyncio.wait_for(self._reader.readline(), self._action_timeout)
            except ValueError as exc:
                raise AmiProtocolError("AMI line exceeds the size limit") from exc
            if not line:
                raise AmiError("AMI connection closed")
            size += len(line)
            if size > MAX_AMI_MESSAGE_BYTES:
                raise AmiProtocolError("AMI message exceeds the size limit")
            if line in {b"\r\n", b"\n"}:
                if parts:
                    return parse_ami_message(b"".join(parts))
                continue
            parts.append(line)

    async def _request(self, action: str, **fields: str) -> dict[str, str]:
        if action not in _ALLOWED_ACTIONS:
            raise AmiProtocolError("AMI action is not allowed")
        action_id = token_hex(12)
        lines = [f"Action: {action}", f"ActionID: {action_id}"]
        lines.extend(f"{key}: {value}" for key, value in fields.items())
        self._writer.write(("\r\n".join(lines) + "\r\n\r\n").encode())
        await asyncio.wait_for(self._writer.drain(), self._action_timeout)
        while True:
            message = await self._read_message()
            self._capture_call_event(message)
            if message.get("response") is None:
                continue
            if message.get("actionid") not in {None, action_id}:
                continue
            return message

    async def _list_action(
        self, action: str, item_event: str, complete_event: str
    ) -> tuple[bool, list[dict[str, str]]]:
        if action not in _ALLOWED_ACTIONS:
            raise AmiProtocolError("AMI action is not allowed")
        action_id = token_hex(12)
        self._writer.write(
            f"Action: {action}\r\nActionID: {action_id}\r\n\r\n".encode()
        )
        await asyncio.wait_for(self._writer.drain(), self._action_timeout)
        items: list[dict[str, str]] = []
        while True:
            message = await self._read_message()
            self._capture_call_event(message)
            if message.get("actionid") not in {None, action_id}:
                continue
            response = message.get("response", "").casefold()
            if response and response != "success":
                return False, []
            event = message.get("event", "").casefold()
            if event == complete_event.casefold():
                return True, items
            if event == item_event.casefold():
                if len(items) >= MAX_AMI_LIST_ITEMS:
                    raise AmiProtocolError("AMI list exceeds the item limit")
                items.append(message)

    async def authenticate(self, username: str, secret: str) -> None:
        response = await self._request(
            "Login", Username=username, Secret=secret, Events="on"
        )
        if response.get("response", "").casefold() != "success":
            raise AmiAuthenticationError("AMI authentication failed")

    async def core_snapshot(self) -> AmiCoreSnapshot:
        settings = await self._request("CoreSettings")
        status = await self._request("CoreStatus")
        if settings.get("response", "").casefold() != "success":
            raise AmiProtocolError("AMI CoreSettings request failed")
        if status.get("response", "").casefold() != "success":
            raise AmiProtocolError("AMI CoreStatus request failed")
        return normalize_core_snapshot(settings | status)

    async def pjsip_snapshot(self) -> AmiPjsipSnapshot:
        capabilities: set[str] = set()
        endpoints_supported, endpoint_fields = await self._list_action(
            "PJSIPShowEndpoints", "EndpointList", "EndpointListComplete"
        )
        contacts_supported, contact_fields = await self._list_action(
            "PJSIPShowContacts", "ContactList", "ContactListComplete"
        )
        inbound_supported, inbound_fields = await self._list_action(
            "PJSIPShowRegistrationInboundContactStatuses",
            "ContactStatusDetail",
            "ContactStatusDetailComplete",
        )
        outbound_supported, outbound_fields = await self._list_action(
            "PJSIPShowRegistrationsOutbound",
            "OutboundRegistrationDetail",
            "OutboundRegistrationDetailComplete",
        )
        if endpoints_supported:
            capabilities.add("endpoints")
        if contacts_supported:
            capabilities.add("contacts")
        if inbound_supported:
            capabilities.add("inbound_registrations")
        if outbound_supported:
            capabilities.add("outbound_registrations")

        endpoints_list: list[AmiEndpointSnapshot] = []
        for fields in endpoint_fields:
            endpoint = _endpoint(fields)
            if endpoint is not None:
                endpoints_list.append(endpoint)
        contacts_list: list[AmiContactSnapshot] = []
        for fields in contact_fields:
            contact = _contact(fields)
            if contact is not None:
                contacts_list.append(contact)
        registrations_list: list[AmiRegistrationSnapshot] = []
        for fields in inbound_fields:
            inbound = _inbound_registration(fields)
            if inbound is not None:
                registrations_list.append(inbound)
        for fields in outbound_fields:
            outbound = _outbound_registration(fields)
            if outbound is not None:
                registrations_list.append(outbound)
        endpoints = tuple(endpoints_list)
        contacts = tuple(contacts_list)
        registrations = tuple(registrations_list)
        transport_names = {
            name.strip()
            for value in [
                *(item.transport for item in endpoints),
                *(item.transport for item in registrations),
            ]
            if value
            for name in value.split(",")
            if name.strip()
        }
        if endpoints_supported or outbound_supported:
            capabilities.add("transports")
        return AmiPjsipSnapshot(
            endpoints=endpoints,
            contacts=contacts,
            registrations=registrations,
            transports=tuple(
                AmiTransportSnapshot(source_id=_identity(name, 255) or name)
                for name in sorted(transport_names)
            ),
            capabilities=frozenset(capabilities),
        )

    async def live_channels_snapshot(self) -> tuple[AmiChannelSnapshot, ...]:
        supported, channel_fields = await self._list_action(
            "CoreShowChannels", "CoreShowChannel", "CoreShowChannelsComplete"
        )
        if not supported:
            raise AmiProtocolError("AMI CoreShowChannels request failed")
        channels: list[AmiChannelSnapshot] = []
        for fields in channel_fields:
            channel = normalize_channel_snapshot(fields)
            if channel is not None:
                channels.append(channel)
        return tuple(channels)

    async def drain_call_events(self) -> tuple[AmiCallEvent, ...]:
        events = tuple(self._call_events)
        self._call_events.clear()
        return events

    async def wait_call_events(self, wait_seconds: float) -> tuple[AmiCallEvent, ...]:
        if self._call_events:
            return await self.drain_call_events()
        try:
            message = await asyncio.wait_for(self._read_message(), wait_seconds)
        except TimeoutError:
            return ()
        self._capture_call_event(message)
        return await self.drain_call_events()

    async def ping(self) -> None:
        response = await self._request("Ping")
        if response.get("response", "").casefold() not in {"success", "pong"}:
            raise AmiProtocolError("AMI ping failed")

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self._request("Logoff")
        except (AmiError, OSError, TimeoutError, RuntimeError):
            pass
        try:
            self._writer.close()
        except RuntimeError:
            return
        try:
            await self._writer.wait_closed()
        except (OSError, RuntimeError):
            pass


class StreamAmiConnector:
    def __init__(self, *, connect_timeout: float, action_timeout: float) -> None:
        self._connect_timeout = connect_timeout
        self._action_timeout = action_timeout

    async def connect(self, target: AmiTarget) -> AmiSession:
        tls_context = ssl.create_default_context() if target.tls else None
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(
                target.hostname,
                target.port,
                ssl=tls_context,
                server_hostname=target.hostname if target.tls else None,
                limit=MAX_AMI_MESSAGE_BYTES,
            ),
            self._connect_timeout,
        )
        try:
            banner = await asyncio.wait_for(reader.readline(), self._action_timeout)
            if not banner.startswith(b"Asterisk Call Manager/"):
                raise AmiProtocolError("Unexpected AMI banner")
            session = StreamAmiSession(reader, writer, action_timeout=self._action_timeout)
            await session.authenticate(target.username, target.secret)
            return session
        except BaseException:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
            raise
