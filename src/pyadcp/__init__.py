"""pyadcp: asyncio-native client for Sony projectors' ADCP protocol.

Typical use::

    from pyadcp import Projector

    async with Projector("192.168.1.50", password="Projector") as projector:
        await projector.power_on()
        status = await projector.poll_status()
        caps = await projector.capabilities()
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
from pyadcp.projector import (
    Capabilities,
    CommandCapability,
    NumericRange,
    PowerStatus,
    Projector,
    ProjectorIdentity,
    ProjectorStatus,
)
from pyadcp.sdap import (
    SDAP_PORT,
    SdapError,
    SdapPacket,
    build_packet,
    discover,
    listen,
    parse_packet,
)

__version__ = "0.1.0"

__all__ = [
    "COMMANDS",
    "DEFAULT_PASSWORD",
    "DEFAULT_PORT",
    "INFO_TYPE_KINDS",
    "KEY_CODES",
    "SDAP_PORT",
    "AdcpConnection",
    "AdcpConnectionError",
    "AdcpError",
    "AdcpTimeoutError",
    "AuthenticationError",
    "Capabilities",
    "Command",
    "CommandCapability",
    "CommandError",
    "CommandFormError",
    "CommandFormatError",
    "CommandInactiveError",
    "CommandKind",
    "CommandOptionError",
    "CommandValueError",
    "Internal1Error",
    "Internal2Error",
    "InternalError",
    "JsonValue",
    "NumericRange",
    "PowerStatus",
    "Projector",
    "ProjectorIdentity",
    "ProjectorStatus",
    "ProtocolError",
    "SdapError",
    "SdapPacket",
    "UnknownCommandError",
    "__version__",
    "build_packet",
    "discover",
    "get_command",
    "listen",
    "parse_packet",
]
