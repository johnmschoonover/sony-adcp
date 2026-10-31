"""pyadcp: asyncio-native client for Sony projectors' ADCP protocol.

Typical use::

    from pyadcp import Projector

    async with Projector("192.168.1.50", password="Projector") as projector:
        await projector.power_on()
        status = await projector.poll_status()
        caps = await projector.capabilities()

To check whether a host speaks ADCP at all before opening a session, use
:func:`probe`.
"""

from pyadcp.commands import (
    COMMANDS,
    INFO_TYPE_KINDS,
    KEY_CODES,
    Command,
    CommandFormError,
    CommandKind,
    UnknownCommandError,
    get_command,
)
from pyadcp.connection import DEFAULT_PASSWORD, DEFAULT_PORT, AdcpConnection, JsonValue
from pyadcp.exceptions import (
    AdcpConnectionError,
    AdcpError,
    AdcpTimeoutError,
    AuthenticationError,
    CommandError,
    CommandFormatError,
    CommandInactiveError,
    CommandOptionError,
    CommandValueError,
    Internal1Error,
    Internal2Error,
    InternalError,
    ProtocolError,
)
from pyadcp.probing import DEFAULT_PROBE_TIMEOUT, ProbeOutcome, ProbeResult, probe
from pyadcp.projector import (
    CAPABILITIES_FORMAT,
    Capabilities,
    CapabilityChanges,
    CommandCapability,
    DiscoveryEvent,
    NumericRange,
    PowerStatus,
    Projector,
    ProjectorIdentity,
    ProjectorStatus,
    SuffixCapability,
)
from pyadcp.sdap import (
    POWER_STATUS_NAMES,
    SDAP_PORT,
    SdapError,
    SdapPacket,
    build_packet,
    discover,
    listen,
    parse_packet,
)

__version__ = "1.0.0"

__all__ = [
    "CAPABILITIES_FORMAT",
    "COMMANDS",
    "DEFAULT_PASSWORD",
    "DEFAULT_PORT",
    "DEFAULT_PROBE_TIMEOUT",
    "INFO_TYPE_KINDS",
    "KEY_CODES",
    "POWER_STATUS_NAMES",
    "SDAP_PORT",
    "AdcpConnection",
    "AdcpConnectionError",
    "AdcpError",
    "AdcpTimeoutError",
    "AuthenticationError",
    "Capabilities",
    "CapabilityChanges",
    "Command",
    "CommandCapability",
    "CommandError",
    "CommandFormError",
    "CommandFormatError",
    "CommandInactiveError",
    "CommandKind",
    "CommandOptionError",
    "CommandValueError",
    "DiscoveryEvent",
    "Internal1Error",
    "Internal2Error",
    "InternalError",
    "JsonValue",
    "NumericRange",
    "PowerStatus",
    "ProbeOutcome",
    "ProbeResult",
    "Projector",
    "ProjectorIdentity",
    "ProjectorStatus",
    "ProtocolError",
    "SdapError",
    "SdapPacket",
    "SuffixCapability",
    "UnknownCommandError",
    "__version__",
    "build_packet",
    "discover",
    "get_command",
    "listen",
    "parse_packet",
    "probe",
]
