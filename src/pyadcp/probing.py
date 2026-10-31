"""Find out whether a host speaks ADCP, without opening a full session.

:func:`probe` opens one TCP connection, reads the single greeting line an ADCP
projector sends as soon as the connection is up, and closes the socket again.
The greeting alone answers the question:

* ``NOKEY``: ADCP with authentication switched off.
* a string of hexadecimal digits: ADCP with authentication on; the string is
  the challenge nonce.
* anything else, or nothing: not ADCP, or not recognisable as ADCP.

When a password is given and the projector sent a nonce, the probe also
answers the challenge and reports whether the projector accepted the password
(``OK``) or rejected it (``err_auth``). It never sends a command, so probing
cannot change anything on the projector.

Every expected outcome, including an unreachable host or a wrong password, is
reported as a :class:`ProbeOutcome` on the returned :class:`ProbeResult`
rather than raised. A wrong password is :attr:`ProbeOutcome.AUTH_FAILED`,
which is unambiguously ADCP (:attr:`ProbeResult.is_adcp` is ``True``): a
client that chooses between protocols must not mistake it for "not ADCP".

The first user is a configuration flow that has to choose between this
library and a client for an older protocol; that choice belongs to the client.
The library only reports what the host said.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from enum import StrEnum

from pyadcp.connection import (
    AUTH_OK,
    DEFAULT_PORT,
    ERR_AUTH,
    NOKEY,
    _answer_challenge,
    _looks_like_nonce,
    _read_line,
)
from pyadcp.exceptions import AdcpConnectionError, ProtocolError

__all__ = ["DEFAULT_PROBE_TIMEOUT", "ProbeOutcome", "ProbeResult", "probe"]

logger = logging.getLogger(__name__)

DEFAULT_PROBE_TIMEOUT = 15.0
"""Default seconds for a whole probe: connect, greeting and optional password check.

The manual says a VW, HW or GTZ series unit in low-power standby may stay
silent for up to 10 seconds before answering its first command. It does not
say whether the greeting is held back the same way, so the default leaves room
for that plus connection set-up, rather than misreporting a slow unit as
silent.
"""

_LINE_LIMIT = 256
"""Longest greeting line the probe reads. An ADCP greeting is a few bytes."""


class ProbeOutcome(StrEnum):
    """What a :func:`probe` found. The string values are stable and safe to store."""

    REFUSED = "refused"
    """The host actively refused the TCP connection: nothing listens on the port."""

    UNREACHABLE = "unreachable"
    """No TCP connection: name resolution failed, no route, or the connect timed out."""

    TIMEOUT = "timeout"
    """The TCP connection opened but no complete greeting line arrived in time."""

    CLOSED = "closed"
    """The TCP connection opened and the peer closed (or reset) it without a greeting.

    This is what a VPL-VW295ES does to a client outside its host allow-list:
    accept the connection and close it at once, with no bytes sent.
    """

    NOT_ADCP = "not_adcp"
    """The peer sent a greeting that is not ``NOKEY``, a hex nonce or ``err_auth``."""

    HOST_DENIED = "host_denied"
    """The peer greeted with ``err_auth``: ADCP, but this client may not connect.

    The manual does not describe what a rejected host sees. pyadcp keeps
    this outcome for a unit that answers that way, but the one tested
    (a VPL-VW295ES) closes the connection instead and probes as
    :attr:`CLOSED`.
    """

    NO_AUTH = "no_auth"
    """ADCP with authentication off (greeting ``NOKEY``). Any password is ignored."""

    AUTH_REQUIRED = "auth_required"
    """ADCP with authentication on (greeting is a nonce); no password was given."""

    AUTH_OK = "auth_ok"
    """ADCP with authentication on, and the projector accepted the password (``OK``)."""

    AUTH_FAILED = "auth_failed"
    """ADCP with authentication on, and the projector rejected the password (``err_auth``)."""

    AUTH_UNCONFIRMED = "auth_unconfirmed"
    """ADCP with authentication on, but the verdict on the password never arrived.

    The projector stayed silent, closed the socket or answered something other
    than ``OK`` or ``err_auth`` after the digest was sent.
    """

    @property
    def is_adcp(self) -> bool | None:
        """``True`` if the host speaks ADCP, ``False`` if it does not, ``None`` if unknown.

        ``False`` covers :attr:`NOT_ADCP` and :attr:`REFUSED` (nothing
        listens on that port). :attr:`UNREACHABLE`, :attr:`TIMEOUT` and
        :attr:`CLOSED` say nothing either way.
        """
        if self in _ADCP:
            return True
        if self in _NOT_ADCP:
            return False
        return None

    @property
    def auth_required(self) -> bool | None:
        """``True`` if the projector asked for a password, ``False`` for ``NOKEY``.

        ``None`` when the host is not (known to be) ADCP or it denied this
        client before any challenge.
        """
        if self in _AUTH_ON:
            return True
        if self is ProbeOutcome.NO_AUTH:
            return False
        return None

    @property
    def password_accepted(self) -> bool | None:
        """``True`` for :attr:`AUTH_OK`, ``False`` for :attr:`AUTH_FAILED`, else ``None``."""
        if self is ProbeOutcome.AUTH_OK:
            return True
        if self is ProbeOutcome.AUTH_FAILED:
            return False
        return None


_AUTH_ON = frozenset(
    {
        ProbeOutcome.AUTH_REQUIRED,
        ProbeOutcome.AUTH_OK,
        ProbeOutcome.AUTH_FAILED,
        ProbeOutcome.AUTH_UNCONFIRMED,
    }
)
_ADCP = _AUTH_ON | {ProbeOutcome.NO_AUTH, ProbeOutcome.HOST_DENIED}
_NOT_ADCP = frozenset({ProbeOutcome.NOT_ADCP, ProbeOutcome.REFUSED})


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """The answer from :func:`probe`.

    Attributes:
        host: The host that was probed.
        port: The TCP port that was probed.
        outcome: What the probe found; see :class:`ProbeOutcome`.
        greeting: The greeting line without its terminator, if a complete
            US-ASCII line arrived. For an ADCP projector with authentication
            on this is the (single-use) nonce.
        detail: A human-readable explanation for outcomes other than the
            plain successes, such as the operating system's connect error or
            the unexpected handshake reply. Meant for logs, not for parsing.
        elapsed: Seconds the probe took, socket set-up included.
    """

    host: str
    port: int
    outcome: ProbeOutcome
    greeting: str | None = None
    detail: str | None = None
    elapsed: float = 0.0

    @property
    def is_adcp(self) -> bool | None:
        """Shortcut for :attr:`ProbeOutcome.is_adcp`."""
        return self.outcome.is_adcp

    @property
    def auth_required(self) -> bool | None:
        """Shortcut for :attr:`ProbeOutcome.auth_required`."""
        return self.outcome.auth_required

    @property
    def password_accepted(self) -> bool | None:
        """Shortcut for :attr:`ProbeOutcome.password_accepted`."""
        return self.outcome.password_accepted


class _Probe:
    """Mutable state of one probe, so the outer function can always close the socket."""

    def __init__(self) -> None:
        self.writer: asyncio.StreamWriter | None = None
        self.greeting: str | None = None
        self.phase = "connect"


async def probe(
    host: str,
    port: int = DEFAULT_PORT,
    *,
    # A caller's own asyncio.timeout could not tell which phase ran out, and
    # an expired probe is a result (TIMEOUT, UNREACHABLE ...), not an error.
    timeout: float = DEFAULT_PROBE_TIMEOUT,  # noqa: ASYNC109
    password: str | None = None,
) -> ProbeResult:
    """Ask ``host`` whether it speaks ADCP and whether it needs a password.

    Opens one TCP connection, reads the greeting and closes the connection. No
    command is ever sent. With ``password`` given and authentication on, the
    challenge is answered too and the verdict reported; the session is then
    closed without sending anything else.

    Args:
        host: Address or host name to probe.
        port: TCP port, ``53595`` unless changed on the projector.
        timeout: Seconds allowed for the whole probe: the TCP connect, the
            greeting and, with a password, the verdict. The default,
            :data:`DEFAULT_PROBE_TIMEOUT`, is generous so a slow unit in
            standby is not reported as :attr:`ProbeOutcome.TIMEOUT`.
        password: Password to check when the projector asks for one. ``None``
            stops after the greeting. Ignored when authentication is off.

    Returns:
        A :class:`ProbeResult`. Every expected outcome, including an
        unreachable host and a rejected password, is reported there rather
        than raised.

    Raises:
        ValueError: if ``timeout`` is not positive.
        asyncio.CancelledError: if the calling task is cancelled; the socket
            is closed first.
    """
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    loop = asyncio.get_running_loop()
    started = loop.time()
    state = _Probe()
    try:
        outcome, detail = await _exchange(state, host, port, timeout, password)
    except BaseException:
        # Cancelled (or something unforeseen): never leave the socket open.
        if state.writer is not None:
            state.writer.close()
        raise
    if state.writer is not None:
        state.writer.close()
        with contextlib.suppress(OSError):
            await state.writer.wait_closed()
    result = ProbeResult(
        host=host,
        port=port,
        outcome=outcome,
        greeting=state.greeting,
        detail=detail,
        elapsed=loop.time() - started,
    )
    logger.debug("probe %s:%d -> %s (%s)", host, port, outcome, detail or "no detail")
    return result


async def _exchange(
    state: _Probe,
    host: str,
    port: int,
    budget: float,
    password: str | None,
) -> tuple[ProbeOutcome, str | None]:
    """Run the probe conversation and classify it. Leaves closing to the caller."""
    try:
        async with asyncio.timeout(budget):
            # all_errors keeps every per-address failure, so a host name that
            # resolves to several addresses is still recognisably "refused".
            reader, state.writer = await asyncio.open_connection(
                host, port, limit=_LINE_LIMIT, all_errors=True
            )
            state.phase = "greeting"
            greeting = state.greeting = await _read_line(reader)
            if greeting == NOKEY:
                return ProbeOutcome.NO_AUTH, None
            if greeting == ERR_AUTH:
                return ProbeOutcome.HOST_DENIED, "greeted with err_auth instead of a nonce"
            if not _looks_like_nonce(greeting):
                return ProbeOutcome.NOT_ADCP, f"unrecognised greeting {greeting!r}"
            if password is None:
                return ProbeOutcome.AUTH_REQUIRED, None
            state.phase = "verdict"
            verdict = await _answer_challenge(reader, state.writer, greeting, password)
    except TimeoutError:
        # Listed before OSError, of which TimeoutError is a subclass.
        match state.phase:
            case "connect":
                return ProbeOutcome.UNREACHABLE, f"no TCP connection within {budget}s"
            case "greeting":
                return ProbeOutcome.TIMEOUT, f"no greeting within {budget}s"
            case _:
                return ProbeOutcome.AUTH_UNCONFIRMED, f"no handshake verdict within {budget}s"
    except ExceptionGroup as group:
        # Only the connect raises a group: one failure per resolved address.
        _refused, other = group.split(ConnectionRefusedError)
        detail = "; ".join(_describe(exc) for exc in group.exceptions)
        if other is None:
            return ProbeOutcome.REFUSED, detail
        return ProbeOutcome.UNREACHABLE, detail
    except ConnectionRefusedError as exc:
        return ProbeOutcome.REFUSED, _describe(exc)
    except (OSError, AdcpConnectionError) as exc:
        match state.phase:
            case "connect":
                return ProbeOutcome.UNREACHABLE, _describe(exc)
            case "greeting":
                return ProbeOutcome.CLOSED, _describe(exc)
            case _:
                return ProbeOutcome.AUTH_UNCONFIRMED, _describe(exc)
    except ProtocolError as exc:
        # Overlong or non-ASCII line: not something an ADCP projector sends.
        if state.phase == "greeting":
            return ProbeOutcome.NOT_ADCP, str(exc)
        return ProbeOutcome.AUTH_UNCONFIRMED, str(exc)
    if verdict == AUTH_OK:
        return ProbeOutcome.AUTH_OK, None
    if verdict == ERR_AUTH:
        return ProbeOutcome.AUTH_FAILED, "projector rejected the password"
    return ProbeOutcome.AUTH_UNCONFIRMED, f"unexpected handshake reply {verdict!r}"


def _describe(exc: BaseException) -> str:
    return str(exc) or type(exc).__name__
