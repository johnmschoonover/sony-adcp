"""pyadcp: asyncio-native client for Sony projectors' ADCP protocol.

Typical use::

    from pyadcp import Projector

    async with Projector("192.168.1.50", password="Projector") as projector:
        await projector.power_on()
        status = await projector.poll_status()
        caps = await projector.capabilities()
"""

from pyadcp.commands import COMMANDS, KEY_CODES, Command, CommandKind, get_command
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
    ProjectorStatus,
)
from pyadcp.sdap import SDAP_PORT, SdapError, SdapPacket, discover, listen

__version__ = "0.1.0"

__all__ = [
    "COMMANDS",
    "DEFAULT_PASSWORD",
    "DEFAULT_PORT",
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
    "ProjectorStatus",
    "ProtocolError",
    "SdapError",
    "SdapPacket",
    "__version__",
    "discover",
    "get_command",
    "listen",
]
