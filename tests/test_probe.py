"""Tests for :func:`pyadcp.probe` against the fake projector."""

from __future__ import annotations

import asyncio
import socket
from typing import TYPE_CHECKING, Any

import pytest

import pyadcp
from pyadcp import DEFAULT_PROBE_TIMEOUT, ProbeOutcome, ProbeResult, probe
from tests.conftest import PASSWORD, make_connection

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.fake_projector import FakeProjector

    FakeFactory = Callable[..., FakeProjector]

HOST = "127.0.0.1"


async def _start(factory: FakeFactory, **kwargs: Any) -> FakeProjector:
    projector = factory(**kwargs)
    await projector.start()
    return projector


async def _wait_until(condition: Callable[[], bool]) -> None:
    """Poll ``condition`` until it holds; fail the test if it never does within 2 s."""
    async with asyncio.timeout(2):
        while not condition():  # noqa: ASYNC110 - the fake exposes no event to wait on
            await asyncio.sleep(0.005)


async def _assert_hung_up(fake: FakeProjector) -> None:
    """The client closed its socket and the fake saw no command line."""
    await _wait_until(lambda: fake.open_sessions == 0)
    assert fake.received == []


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind((HOST, 0))
        port: int = sock.getsockname()[1]
    return port


# -- outcome semantics ---------------------------------------------------------

EXPECTED_FLAGS: dict[ProbeOutcome, tuple[bool | None, bool | None, bool | None]] = {
    # outcome: (is_adcp, auth_required, password_accepted)
    ProbeOutcome.REFUSED: (False, None, None),
    ProbeOutcome.UNREACHABLE: (None, None, None),
    ProbeOutcome.TIMEOUT: (None, None, None),
    ProbeOutcome.CLOSED: (None, None, None),
    ProbeOutcome.NOT_ADCP: (False, None, None),
    ProbeOutcome.HOST_DENIED: (True, None, None),
    ProbeOutcome.NO_AUTH: (True, False, None),
    ProbeOutcome.AUTH_REQUIRED: (True, True, None),
    ProbeOutcome.AUTH_OK: (True, True, True),
    ProbeOutcome.AUTH_FAILED: (True, True, False),
    ProbeOutcome.AUTH_UNCONFIRMED: (True, True, None),
}


def test_every_outcome_has_documented_flags() -> None:
    assert set(EXPECTED_FLAGS) == set(ProbeOutcome)


@pytest.mark.parametrize(("outcome", "flags"), EXPECTED_FLAGS.items())
def test_outcome_flags(
    outcome: ProbeOutcome, flags: tuple[bool | None, bool | None, bool | None]
) -> None:
    assert (outcome.is_adcp, outcome.auth_required, outcome.password_accepted) == flags
    result = ProbeResult(HOST, 1, outcome)
    assert (result.is_adcp, result.auth_required, result.password_accepted) == flags


def test_outcome_values_are_stable_strings() -> None:
    # Clients store these; renaming one is a breaking change.
    assert [o.value for o in ProbeOutcome] == [
        "refused",
        "unreachable",
        "timeout",
        "closed",
        "not_adcp",
        "host_denied",
        "no_auth",
        "auth_required",
        "auth_ok",
        "auth_failed",
        "auth_unconfirmed",
    ]
    assert ProbeOutcome("auth_failed") is ProbeOutcome.AUTH_FAILED


def test_public_exports() -> None:
    assert pyadcp.probe is probe
    assert DEFAULT_PROBE_TIMEOUT >= 10.0  # the manual's standby silence, at least


async def test_timeout_must_be_positive() -> None:
    with pytest.raises(ValueError, match="positive"):
        await probe(HOST, 1, timeout=0)


# -- ADCP greetings ------------------------------------------------------------


async def test_nokey_is_adcp_without_auth(fake_nokey: FakeProjector) -> None:
    result = await probe(HOST, fake_nokey.port, timeout=2)
    assert result.outcome is ProbeOutcome.NO_AUTH
    assert result.is_adcp is True
    assert result.auth_required is False
    assert result.greeting == "NOKEY"
    assert result.host == HOST
    assert result.port == fake_nokey.port
    assert result.elapsed >= 0
    await _assert_hung_up(fake_nokey)


async def test_nokey_ignores_password(fake_nokey: FakeProjector) -> None:
    result = await probe(HOST, fake_nokey.port, timeout=2, password="anything")
    assert result.outcome is ProbeOutcome.NO_AUTH
    await _assert_hung_up(fake_nokey)


async def test_nonce_without_password_needs_auth(fake: FakeProjector) -> None:
    result = await probe(HOST, fake.port, timeout=2)
    assert result.outcome is ProbeOutcome.AUTH_REQUIRED
    assert result.is_adcp is True
    assert result.auth_required is True
    assert result.password_accepted is None
    assert result.greeting is not None
    assert len(result.greeting) == 8
    await _assert_hung_up(fake)
    assert fake.handshake_received == []  # no digest sent without a password


async def test_correct_password_is_accepted(fake: FakeProjector) -> None:
    result = await probe(HOST, fake.port, timeout=2, password=PASSWORD)
    assert result.outcome is ProbeOutcome.AUTH_OK
    assert result.password_accepted is True
    assert result.detail is None
    await _assert_hung_up(fake)
    assert len(fake.handshake_received) == 1


async def test_wrong_password_is_adcp_not_unknown(fake: FakeProjector) -> None:
    result = await probe(HOST, fake.port, timeout=2, password="wrong")
    assert result.outcome is ProbeOutcome.AUTH_FAILED
    # The point of the outcome: this host IS ADCP, only the password is wrong.
    assert result.is_adcp is True
    assert result.auth_required is True
    assert result.password_accepted is False
    await _assert_hung_up(fake)


async def test_empty_password_is_checked(fake_factory: FakeFactory) -> None:
    fake = await _start(fake_factory, password="")
    result = await probe(HOST, fake.port, timeout=2, password="")
    assert result.outcome is ProbeOutcome.AUTH_OK


async def test_uppercase_hex_nonce_is_accepted(fake_factory: FakeFactory) -> None:
    fake = await _start(fake_factory, greeting_override="1A2B3C4D")
    result = await probe(HOST, fake.port, timeout=2, password=PASSWORD)
    assert result.outcome is ProbeOutcome.AUTH_OK
    assert result.greeting == "1A2B3C4D"


async def test_err_auth_greeting_is_host_denied(fake_factory: FakeFactory) -> None:
    fake = await _start(fake_factory, greeting_override="err_auth")
    result = await probe(HOST, fake.port, timeout=2, password=PASSWORD)
    assert result.outcome is ProbeOutcome.HOST_DENIED
    assert result.is_adcp is True
    assert fake.handshake_received == []
    await _assert_hung_up(fake)


async def test_slow_greeting_within_timeout(fake_factory: FakeFactory) -> None:
    fake = await _start(fake_factory, password=None, greeting_delay=0.3)
    result = await probe(HOST, fake.port, timeout=2)
    assert result.outcome is ProbeOutcome.NO_AUTH
    assert result.elapsed >= 0.3


# -- password verdict trouble --------------------------------------------------


async def test_unexpected_verdict_is_unconfirmed(fake_factory: FakeFactory) -> None:
    fake = await _start(fake_factory, handshake_verdict_override="WHAT")
    result = await probe(HOST, fake.port, timeout=2, password=PASSWORD)
    assert result.outcome is ProbeOutcome.AUTH_UNCONFIRMED
    assert result.is_adcp is True
    assert result.password_accepted is None
    assert result.detail is not None
    assert "WHAT" in result.detail
    await _assert_hung_up(fake)


async def test_silent_verdict_is_unconfirmed(fake_factory: FakeFactory) -> None:
    fake = await _start(fake_factory, verdict_behaviour="silent")
    result = await probe(HOST, fake.port, timeout=0.3, password=PASSWORD)
    assert result.outcome is ProbeOutcome.AUTH_UNCONFIRMED
    assert result.detail is not None
    assert "verdict" in result.detail
    await _assert_hung_up(fake)


async def test_closed_before_verdict_is_unconfirmed(fake_factory: FakeFactory) -> None:
    fake = await _start(fake_factory, verdict_behaviour="close")
    result = await probe(HOST, fake.port, timeout=2, password=PASSWORD)
    assert result.outcome is ProbeOutcome.AUTH_UNCONFIRMED
    assert result.is_adcp is True
    await _assert_hung_up(fake)


# -- not ADCP, or cannot tell --------------------------------------------------


@pytest.mark.parametrize(
    "greeting",
    ["SSH-2.0-OpenSSH_9.6", "", "nokey", "OK", "12 34", "0x1a2b"],
)
async def test_other_greeting_is_not_adcp(fake_factory: FakeFactory, greeting: str) -> None:
    fake = await _start(fake_factory, greeting_override=greeting)
    result = await probe(HOST, fake.port, timeout=2, password=PASSWORD)
    assert result.outcome is ProbeOutcome.NOT_ADCP
    assert result.is_adcp is False
    assert result.greeting == greeting
    assert fake.handshake_received == []  # the password is never offered to it
    await _assert_hung_up(fake)


async def test_non_ascii_greeting_is_not_adcp(fake_factory: FakeFactory) -> None:
    fake = await _start(fake_factory, raw_greeting=b"\xff\xfb\x01\xff\xfd\x03\r\n")
    result = await probe(HOST, fake.port, timeout=2)
    assert result.outcome is ProbeOutcome.NOT_ADCP
    assert result.greeting is None
    await _assert_hung_up(fake)


async def test_overlong_greeting_is_not_adcp(fake_factory: FakeFactory) -> None:
    fake = await _start(fake_factory, raw_greeting=b"x" * 4000)
    result = await probe(HOST, fake.port, timeout=2)
    assert result.outcome is ProbeOutcome.NOT_ADCP
    await _assert_hung_up(fake)


async def test_silent_peer_times_out(fake_factory: FakeFactory) -> None:
    fake = await _start(fake_factory, greeting_behaviour="silent")
    result = await probe(HOST, fake.port, timeout=0.3)
    assert result.outcome is ProbeOutcome.TIMEOUT
    assert result.is_adcp is None
    assert result.greeting is None
    assert 0.3 <= result.elapsed < 2
    await _assert_hung_up(fake)


async def test_greeting_later_than_timeout_times_out(fake_factory: FakeFactory) -> None:
    fake = await _start(fake_factory, password=None, greeting_delay=1.0)
    result = await probe(HOST, fake.port, timeout=0.2)
    assert result.outcome is ProbeOutcome.TIMEOUT


async def test_unterminated_greeting_times_out(fake_factory: FakeFactory) -> None:
    # Bytes without CR LF are not a greeting line yet; documented as TIMEOUT.
    fake = await _start(fake_factory, raw_greeting=b"login: ")
    result = await probe(HOST, fake.port, timeout=0.3)
    assert result.outcome is ProbeOutcome.TIMEOUT


async def test_immediate_close_is_closed(fake_factory: FakeFactory) -> None:
    fake = await _start(fake_factory, greeting_behaviour="close")
    result = await probe(HOST, fake.port, timeout=2)
    assert result.outcome is ProbeOutcome.CLOSED
    assert result.is_adcp is None
    await _assert_hung_up(fake)


async def test_partial_line_then_close_is_closed(fake_factory: FakeFactory) -> None:
    fake = await _start(fake_factory, raw_greeting=b"NOK", greeting_delay=0)
    task = asyncio.create_task(probe(HOST, fake.port, timeout=2))
    await _wait_until(lambda: fake.open_sessions == 1)
    await fake.stop()
    result = await task
    assert result.outcome is ProbeOutcome.CLOSED


async def test_refused_port() -> None:
    port = _free_port()
    result = await probe(HOST, port, timeout=2)
    assert result.outcome is ProbeOutcome.REFUSED
    assert result.is_adcp is False
    assert result.detail


async def test_refused_on_every_address_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    async def refuse_all(*args: object, **kwargs: object) -> None:
        assert kwargs.get("all_errors") is True
        raise ExceptionGroup(
            "create_connection failed",
            [ConnectionRefusedError(111, "refused ::1"), ConnectionRefusedError(111, "refused")],
        )

    monkeypatch.setattr(asyncio, "open_connection", refuse_all)
    result = await probe("projector.local", timeout=2)
    assert result.outcome is ProbeOutcome.REFUSED
    assert result.detail is not None
    assert "::1" in result.detail


async def test_mixed_connect_failures_are_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    async def mixed(*args: object, **kwargs: object) -> None:
        raise ExceptionGroup(
            "create_connection failed",
            [ConnectionRefusedError(111, "refused"), OSError(113, "no route to host")],
        )

    monkeypatch.setattr(asyncio, "open_connection", mixed)
    result = await probe("projector.local", timeout=2)
    assert result.outcome is ProbeOutcome.UNREACHABLE


async def test_name_resolution_failure_is_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_such_host(*args: object, **kwargs: object) -> None:
        raise socket.gaierror(-2, "Name or service not known")

    monkeypatch.setattr(asyncio, "open_connection", no_such_host)
    result = await probe("no-such-projector.invalid", timeout=2)
    assert result.outcome is ProbeOutcome.UNREACHABLE
    assert result.is_adcp is None
    assert result.detail is not None
    assert "not known" in result.detail


async def test_connect_timeout_is_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    async def black_hole(*args: object, **kwargs: object) -> None:
        await asyncio.sleep(60)

    monkeypatch.setattr(asyncio, "open_connection", black_hole)
    result = await probe("192.0.2.1", timeout=0.1)
    assert result.outcome is ProbeOutcome.UNREACHABLE
    assert result.detail is not None
    assert "TCP connection" in result.detail


# -- cancellation --------------------------------------------------------------


async def test_cancel_while_waiting_for_greeting_closes_socket(
    fake_factory: FakeFactory,
) -> None:
    fake = await _start(fake_factory, greeting_behaviour="silent")
    task = asyncio.create_task(probe(HOST, fake.port, timeout=30))
    await _wait_until(lambda: fake.open_sessions == 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await _assert_hung_up(fake)


async def test_cancel_while_waiting_for_verdict_closes_socket(
    fake_factory: FakeFactory,
) -> None:
    fake = await _start(fake_factory, verdict_behaviour="silent")
    task = asyncio.create_task(probe(HOST, fake.port, timeout=30, password=PASSWORD))
    await _wait_until(lambda: len(fake.handshake_received) == 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await _assert_hung_up(fake)


async def test_outer_timeout_cancels_cleanly(fake_factory: FakeFactory) -> None:
    fake = await _start(fake_factory, greeting_behaviour="silent")
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.2):
            await probe(HOST, fake.port, timeout=30)
    await _assert_hung_up(fake)


# -- never a command -----------------------------------------------------------


async def test_probe_leaves_the_projector_untouched(fake: FakeProjector) -> None:
    before = dict(fake.state)
    for password in (None, PASSWORD, "wrong"):
        await probe(HOST, fake.port, timeout=2, password=password)
    await _assert_hung_up(fake)
    assert fake.connections == 3
    assert fake.state == before
    assert fake.keys_pressed == []


async def test_connection_still_works_after_probe(fake: FakeProjector) -> None:
    assert (await probe(HOST, fake.port, timeout=2)).outcome is ProbeOutcome.AUTH_REQUIRED
    conn = make_connection(fake.port)
    try:
        assert await conn.send("power_status ?") == "standby"
    finally:
        await conn.close()
