"""Asyncio TCP client for the ADCP line protocol.

The connection speaks the wire format described in Sony's COMMON protocol
manual: one US-ASCII command per line terminated by CR LF, exactly one reply
line per command, and an optional SHA-256 challenge handshake when the
projector has authentication switched on.

:class:`AdcpConnection` is deliberately low level. It knows nothing about
individual commands; see :mod:`pyadcp.commands` and :mod:`pyadcp.projector`
for that. What it does do:

* open the socket and complete the ``NOKEY`` / nonce handshake,
* serialise commands with a lock so only one is in flight at a time,
* parse every reply into ``None`` (for ``ok``), a JSON value, or a typed
  :class:`~pyadcp.exceptions.CommandError`,
* reconnect transparently when the projector closes an idle session,
* retry the first command after a connect once when the projector stays
  silent or answers ``err_cmd``, which is how the manual says a unit in
  standby behaves while it wakes up.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import re
from typing import TYPE_CHECKING, Self

from pyadcp.exceptions import (
    AdcpConnectionError,
    AdcpTimeoutError,
    AuthenticationError,
    CommandError,
    CommandFormatError,
    ProtocolError,
    error_for_reply,
)

if TYPE_CHECKING:
    from types import TracebackType

__all__ = [
    "DEFAULT_PASSWORD",
    "DEFAULT_PORT",
    "MAX_COMMAND_BYTES",
    "AdcpConnection",
    "JsonValue",
    "auth_response",
    "is_query",
    "parse_reply",
]

logger = logging.getLogger(__name__)

DEFAULT_PORT = 53595
"""Factory-default TCP port for ADCP."""

DEFAULT_PASSWORD = "Projector"
"""Factory-default authentication password (shared with the web admin page)."""

MAX_COMMAND_BYTES = 512
"""Maximum command size the projector accepts, including the CR LF terminator."""

LINE_END = b"\r\n"
NOKEY = "NOKEY"
AUTH_OK = "OK"
REPLY_OK = "ok"
ERR_AUTH = "err_auth"

type JsonValue = str | int | float | bool | list[JsonValue] | dict[str, JsonValue] | None
"""Any value the projector can return in a reply line."""


def auth_response(nonce: str, password: str) -> str:
    """Return the handshake response for ``nonce``.

    The projector expects the lowercase hexadecimal SHA-256 digest of the nonce
    string immediately followed by the password, with nothing in between.
    """
    return hashlib.sha256((nonce + password).encode("utf-8")).hexdigest()


_NONCE = re.compile(r"[0-9A-Fa-f]+")


def _looks_like_nonce(greeting: str) -> bool:
    """Whether a greeting line has the shape of an authentication nonce (hex digits)."""
    return _NONCE.fullmatch(greeting) is not None


async def _read_line(reader: asyncio.StreamReader) -> str:
    """Read one CR LF terminated line and return it without the terminator.

    Raises:
        AdcpConnectionError: the peer closed the socket before a full line.
        ProtocolError: the line exceeds the reader's limit or is not US-ASCII.
    """
    try:
        raw = await reader.readuntil(LINE_END)
    except asyncio.IncompleteReadError as exc:
        raise AdcpConnectionError("connection closed by projector") from exc
    except asyncio.LimitOverrunError as exc:
        raise ProtocolError("reply line too long") from exc
    try:
        return raw[: -len(LINE_END)].decode("ascii")
    except UnicodeDecodeError as exc:
        raise ProtocolError(f"non-ASCII reply {raw!r}") from exc


async def _answer_challenge(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    nonce: str,
    password: str,
) -> str:
    """Send the digest for ``nonce`` and return the projector's verdict line.

    The verdict is ``OK`` on success and ``err_auth`` for a wrong password;
    the caller decides what anything else means. Shared by
    :meth:`AdcpConnection._open` and :func:`pyadcp.probing.probe`.
    """
    writer.write(auth_response(nonce, password).encode("ascii") + LINE_END)
    await writer.drain()
    return await _read_line(reader)


def parse_reply(command: str, line: str) -> JsonValue:
    """Turn a reply line into a Python value.

    ``ok`` becomes ``None``. Any ``err_*`` token raises the matching
    :class:`~pyadcp.exceptions.CommandError` subclass. Everything else must be
    a JSON value (``"standby"``, ``88``, ``["a","b"]``, ``{"min":0,"max":100}``).
    """
    if line == REPLY_OK:
        return None
    if line.startswith("err_"):
        raise error_for_reply(command, line)
    try:
        value: JsonValue = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"unparseable reply {line!r} to {command!r}") from exc
    return value


class AdcpConnection:
    """A single authenticated ADCP session to one projector.

    Args:
        host: Projector IP address or host name.
        port: ADCP TCP port, ``53595`` unless changed on the projector.
        password: Authentication password. Ignored when the projector answers
            ``NOKEY`` because authentication is off.
        connect_timeout: Seconds allowed for the TCP connect and the handshake.
        command_timeout: Seconds to wait for a reply to a normal command.
        wake_timeout: Seconds to wait for the reply to the first command after
            a connect. The manual says a VW or HW series unit in low-power
            standby may stay silent for up to 10 seconds, so this defaults
            higher than ``command_timeout``.
        wake_retry_delay: Pause before resending the first command when the
            projector stayed silent or answered ``err_cmd``.
        wake_retries: How many times the first command after a connect may be
            resent. ``0`` disables the standby workaround.
    """

    def __init__(
        self,
        host: str,
        port: int = DEFAULT_PORT,
        password: str = DEFAULT_PASSWORD,
        *,
        connect_timeout: float = 5.0,
        command_timeout: float = 5.0,
        wake_timeout: float = 10.0,
        wake_retry_delay: float = 1.0,
        wake_retries: int = 1,
    ) -> None:
        self.host = host
        self.port = port
        self._password = password
        self.connect_timeout = connect_timeout
        self.command_timeout = command_timeout
        self.wake_timeout = wake_timeout
        self.wake_retry_delay = wake_retry_delay
        if wake_retries < 0:
            raise ValueError("wake_retries must not be negative")
        self.wake_retries = wake_retries
        self._lock = asyncio.Lock()
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._fresh = False
        self._authenticated: bool | None = None

    # -- lifecycle -----------------------------------------------------------

    @property
    def connected(self) -> bool:
        """Whether a socket is currently open. It may still be closed by the peer."""
        return self._writer is not None and not self._writer.is_closing()

    @property
    def authenticated(self) -> bool | None:
        """``True`` if the last handshake used a password, ``False`` for ``NOKEY``.

        ``None`` before the first connect.
        """
        return self._authenticated

    async def connect(self) -> None:
        """Open the socket and complete the handshake.

        Calling this is optional; :meth:`send` connects on demand. It is useful
        to fail fast on a bad password or unreachable host.
        """
        async with self._lock:
            if not self.connected:
                await self._open()

    async def close(self) -> None:
        """Close the socket. The next :meth:`send` reconnects automatically."""
        async with self._lock:
            await self._close()

    async def __aenter__(self) -> Self:
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def _open(self) -> None:
        logger.debug("connecting to %s:%d", self.host, self.port)
        writer: asyncio.StreamWriter | None = None
        try:
            async with asyncio.timeout(self.connect_timeout):
                reader, writer = await asyncio.open_connection(self.host, self.port)
                greeting = await _read_line(reader)
                logger.debug("greeting from %s:%d: %s", self.host, self.port, greeting)
                if greeting == NOKEY:
                    self._authenticated = False
                elif greeting == ERR_AUTH:
                    raise AuthenticationError("projector refused the connection")
                else:
                    verdict = await _answer_challenge(reader, writer, greeting, self._password)
                    if verdict == ERR_AUTH:
                        raise AuthenticationError("projector rejected the password")
                    if verdict != AUTH_OK:
                        raise ProtocolError(f"unexpected handshake reply {verdict!r}")
                    self._authenticated = True
        except TimeoutError as exc:
            self._abandon(writer)
            raise AdcpTimeoutError(
                f"no handshake from {self.host}:{self.port} within {self.connect_timeout}s"
            ) from exc
        except OSError as exc:
            self._abandon(writer)
            raise AdcpConnectionError(f"cannot connect to {self.host}:{self.port}: {exc}") from exc
        except BaseException:
            # AuthenticationError, ProtocolError, a lost connection, or a
            # cancellation mid-handshake: never leak the half-open socket.
            self._abandon(writer)
            raise
        self._reader = reader
        self._writer = writer
        self._fresh = True
        logger.debug("connected to %s:%d (auth=%s)", self.host, self.port, self._authenticated)

    @staticmethod
    def _abandon(writer: asyncio.StreamWriter | None) -> None:
        if writer is not None:
            writer.close()

    def _drop(self) -> asyncio.StreamWriter | None:
        """Forget the current socket immediately; returns the writer to await, if any."""
        writer = self._writer
        self._reader = None
        self._writer = None
        self._fresh = False
        if writer is not None:
            writer.close()
        return writer

    async def _close(self) -> None:
        writer = self._drop()
        if writer is not None:
            with contextlib.suppress(OSError):
                await writer.wait_closed()

    # -- commands ------------------------------------------------------------

    async def send(self, command: str) -> JsonValue:
        """Send one command line and return its parsed reply.

        Args:
            command: The command without the CR LF terminator, for example
                ``'power "on"'`` or ``'power_status ?'``.

        Returns:
            ``None`` when the projector answered ``ok``, otherwise the JSON
            value it returned.

        Raises:
            CommandError: for any ``err_*`` reply, as the matching subclass.
            AdcpConnectionError: when the projector cannot be reached, or the
                session was lost and a single reconnect did not help. A
                session lost after a *set* command was written is reported
                rather than retried, because the projector may have acted on
                it; queries are resent once on a fresh socket.
            AuthenticationError: when a handshake fails; never retried.
            AdcpTimeoutError: when no reply arrived in time.
            ProtocolError: when the reply is not valid ADCP.

        The projector is strictly request/response, so a socket that may
        still owe a reply is never reused: on a timeout, a protocol error or
        a cancellation mid-exchange the session is dropped and the next call
        reconnects.
        """
        payload = self._encode(command)
        async with self._lock:
            return await self._send_locked(command, payload)

    async def _send_locked(self, command: str, payload: bytes) -> JsonValue:
        reader = self._reader
        if not self.connected or (reader is not None and reader.at_eof()):
            # Either never connected, or the projector already closed an
            # idle session (its close is sitting in the buffer). Nothing
            # has been sent, so reconnecting first is always safe.
            await self._close()
            await self._open()
        first = self._fresh
        wake_left = self.wake_retries if first else 0
        resend_left = 1 if is_query(command) else 0
        while True:
            wait = self.wake_timeout if self._fresh else self.command_timeout
            try:
                line = await self._roundtrip(payload, wait)
            except AdcpConnectionError:
                # The socket died after the write. The projector may have
                # processed a set or exec, so only a query is sent again.
                await self._close()
                if resend_left == 0:
                    raise
                resend_left -= 1
                logger.debug("session lost, reconnecting to %s:%d", self.host, self.port)
                await self._open()
                continue
            if line is None:
                # The projector stayed silent. It may still answer later, so
                # this socket can never be trusted again: drop it, and if the
                # standby workaround allows, come back on a fresh one.
                await self._close()
                if wake_left == 0:
                    raise AdcpTimeoutError(f"no reply to {command!r} within {wait}s")
                wake_left -= 1
                logger.debug("no reply to first command, reconnecting after wake delay")
                await asyncio.sleep(self.wake_retry_delay)
                await self._open()
                continue
            logger.debug("%s -> %s", command, line)
            try:
                value = parse_reply(command, line)
            except CommandFormatError:
                if first and wake_left > 0:
                    # Some series answer err_cmd to the first command after
                    # waking from standby; the manual says to send it again.
                    wake_left -= 1
                    logger.debug("err_cmd on first command, retrying after wake delay")
                    await asyncio.sleep(self.wake_retry_delay)
                    continue
                self._fresh = False
                raise
            except ProtocolError:
                # The stream is in an unknown state; start over next time.
                await self._close()
                raise
            except CommandError:
                self._fresh = False
                raise
            self._fresh = False
            return value

    @staticmethod
    def _encode(command: str) -> bytes:
        if "\r" in command or "\n" in command:
            raise ValueError("command must not contain line breaks")
        try:
            payload = command.encode("ascii") + LINE_END
        except UnicodeEncodeError as exc:
            raise ValueError("command must be US-ASCII") from exc
        if len(payload) > MAX_COMMAND_BYTES:
            raise ValueError(f"command exceeds {MAX_COMMAND_BYTES} bytes")
        return payload

    async def _roundtrip(self, payload: bytes, wait: float) -> str | None:
        """Write ``payload`` and read one line. ``None`` means the projector stayed silent."""
        reader, writer = self._reader, self._writer
        if reader is None or writer is None:
            raise AdcpConnectionError("not connected")
        try:
            writer.write(payload)
            await writer.drain()
            async with asyncio.timeout(wait):
                return await _read_line(reader)
        except TimeoutError:
            return None
        except (OSError, ConnectionError) as exc:
            raise AdcpConnectionError(f"write failed: {exc}") from exc
        except AdcpConnectionError:
            raise
        except BaseException:
            # Cancelled (or a malformed reply) while a reply may still be in
            # flight. Drop the socket synchronously so a late reply can never
            # be mistaken for the next command's answer.
            self._drop()
            raise


_QUOTED = re.compile(r'"[^"]*"')


def is_query(command: str) -> bool:
    """Whether ``command`` only reads state (``cmd ?``, ``cmd --x ? --info`` ...)."""
    return " ?" in _QUOTED.sub("", command)
