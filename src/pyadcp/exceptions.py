"""Exception hierarchy for pyadcp.

Every projector error reply (``err_cmd``, ``err_option`` and so on) maps to its
own :class:`CommandError` subclass so callers can catch exactly the condition
they care about. Transport problems are :class:`AdcpConnectionError`; a failed
authentication handshake is :class:`AuthenticationError`.
"""

from __future__ import annotations


class AdcpError(Exception):
    """Base class for every error raised by pyadcp."""


class AdcpConnectionError(AdcpError):
    """The TCP connection could not be opened or was lost."""


class AuthenticationError(AdcpConnectionError):
    """The projector rejected the password during the handshake (``err_auth``)."""


class AdcpTimeoutError(AdcpError):
    """The projector did not answer within the configured timeout."""


class ProtocolError(AdcpError):
    """The projector sent something that is not a valid ADCP reply."""


class CommandError(AdcpError):
    """The projector answered a command with an ``err_*`` reply.

    Attributes:
        command: The command line that was sent, without the line terminator.
        reply: The raw error token the projector returned.
    """

    reply_token: str = "err"

    def __init__(self, command: str, reply: str | None = None) -> None:
        self.command = command
        self.reply = reply if reply is not None else self.reply_token
        super().__init__(f"{self.reply!r} in reply to {command!r}")


class CommandFormatError(CommandError):
    """``err_cmd``: the command is unknown to this projector or malformed."""

    reply_token = "err_cmd"


class CommandOptionError(CommandError):
    """``err_option``: an option such as a ``--suffix`` is not accepted."""

    reply_token = "err_option"


class CommandInactiveError(CommandError):
    """``err_inactive``: the command is temporarily unavailable, e.g. in standby."""

    reply_token = "err_inactive"


class CommandValueError(CommandError):
    """``err_val``: the value is out of range or not one of the valid choices."""

    reply_token = "err_val"


class InternalError(CommandError):
    """``err_internal1`` / ``err_internal2``: a communication error inside the projector."""

    reply_token = "err_internal"


class Internal1Error(InternalError):
    """``err_internal1``."""

    reply_token = "err_internal1"


class Internal2Error(InternalError):
    """``err_internal2``."""

    reply_token = "err_internal2"


ERROR_REPLIES: dict[str, type[CommandError]] = {
    cls.reply_token: cls
    for cls in (
        CommandFormatError,
        CommandOptionError,
        CommandInactiveError,
        CommandValueError,
        Internal1Error,
        Internal2Error,
    )
}
"""Maps each error token the projector can send to the exception that represents it."""


def error_for_reply(command: str, reply: str) -> CommandError:
    """Build the exception matching an ``err_*`` reply.

    Unknown ``err_*`` tokens become a plain :class:`CommandError` so a firmware
    that adds new error codes does not break parsing.
    """
    cls = ERROR_REPLIES.get(reply, CommandError)
    return cls(command, reply)
