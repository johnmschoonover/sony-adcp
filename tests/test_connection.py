"""Tests for :mod:`pyadcp.connection` against the fake projector."""

from __future__ import annotations

import asyncio
import re
import socket
from collections.abc import Callable

import pytest

from pyadcp.connection import AdcpConnection, auth_response, is_query, parse_reply
from pyadcp.exceptions import (
    AdcpConnectionError,
    AdcpTimeoutError,
    AuthenticationError,
    CommandError,
    CommandFormatError,
    CommandInactiveError,
    CommandOptionError,
    CommandValueError,
    Internal1Error,
    Internal2Error,
    ProtocolError,
)
from tests.conftest import make_connection
from tests.fake_projector import MINIMAL, VW290ES, FakeCommand, FakeProjector, profile_with

type FakeFactory = Callable[..., FakeProjector]


async def _start(factory: FakeFactory, **kwargs: object) -> FakeProjector:
    projector = factory(**kwargs)
    await projector.start()
    return projector


def _state(conn: AdcpConnection) -> tuple[bool, bool | None]:
    """``(connected, authenticated)`` read through a function so mypy does not narrow them."""
    return conn.connected, conn.authenticated


def _closed_port() -> int:
    """A local port with nothing listening on it."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    assert isinstance(port, int)
    return port


async def _wait_for(condition: Callable[[], bool]) -> None:
    """Poll ``condition`` until it holds; fail the test if it never does within 1 s."""
    async with asyncio.timeout(1):
        while not condition():  # noqa: ASYNC110 - the fake exposes no event to wait on
            await asyncio.sleep(0.005)


# -- pure functions --------------------------------------------------------------


def test_auth_response_matches_manual_example() -> None:
    assert (
        auth_response("1a2b3c4d", "password1234")
        == "283121d4374034199c6f08f1a68d1639bfc8eae257fc2e0ac661d65c7e9f0607"
    )


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("ok", None),
        ('"standby"', "standby"),
        ("88", 88),
        ("-3", -3),
        ('["no_err"]', ["no_err"]),
        ('[{"operation":3400},{"light_src":2300}]', [{"operation": 3400}, {"light_src": 2300}]),
        ('{"min":0,"max":100}', {"min": 0, "max": 100}),
        (
            '{"type":"menu_sel","version":"1.0","range":["a"]}',
            {"type": "menu_sel", "version": "1.0", "range": ["a"]},
        ),
    ],
)
def test_parse_reply_values(line: str, expected: object) -> None:
    assert parse_reply("cmd ?", line) == expected


@pytest.mark.parametrize(
    ("token", "exc_type"),
    [
        ("err_cmd", CommandFormatError),
        ("err_option", CommandOptionError),
        ("err_inactive", CommandInactiveError),
        ("err_val", CommandValueError),
        ("err_internal1", Internal1Error),
        ("err_internal2", Internal2Error),
    ],
)
def test_parse_reply_errors(token: str, exc_type: type[CommandError]) -> None:
    with pytest.raises(exc_type) as info:
        parse_reply("contrast 5", token)
    assert type(info.value) is exc_type
    assert info.value.command == "contrast 5"
    assert info.value.reply == token


def test_parse_reply_unknown_error_is_plain_command_error() -> None:
    with pytest.raises(CommandError) as info:
        parse_reply("x", "err_whatever")
    assert type(info.value) is CommandError
    assert info.value.reply == "err_whatever"


def test_parse_reply_garbage_is_protocol_error() -> None:
    with pytest.raises(ProtocolError):
        parse_reply("x", "not json at all")


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("power_status ?", True),
        ("dynamic_range --hdmi1 ? --info", True),
        ("contrast ? --range", True),
        ('power "on"', False),
        ("picture_mode_reset", False),
        ("contrast --rel 1", False),
        ("contrast --reset", False),
        ("contrast 5", False),
    ],
)
def test_is_query(command: str, expected: bool) -> None:
    assert is_query(command) is expected


# -- handshake -------------------------------------------------------------------


async def test_nokey_handshake(fake_nokey: FakeProjector) -> None:
    conn = make_connection(fake_nokey.port)
    try:
        assert _state(conn) == (False, None)
        await conn.connect()
        assert _state(conn) == (True, False)
        assert await conn.send("power_status ?") == "standby"
    finally:
        await conn.close()
    assert _state(conn) == (False, False)


async def test_password_handshake(connection: AdcpConnection, fake: FakeProjector) -> None:
    await connection.connect()
    assert connection.connected
    assert connection.authenticated is True
    assert fake.connections == 1
    assert fake.received == []


async def test_wrong_password(fake: FakeProjector) -> None:
    conn = make_connection(fake.port, password="wrong")
    with pytest.raises(AuthenticationError):
        await conn.connect()
    assert not conn.connected
    assert conn.authenticated is None


async def test_fake_closes_socket_after_err_auth(fake: FakeProjector) -> None:
    reader, writer = await asyncio.open_connection("127.0.0.1", fake.port)
    try:
        nonce = (await reader.readline()).strip().decode()
        assert len(nonce) == 8
        writer.write(auth_response(nonce, "wrong").encode() + b"\r\n")
        await writer.drain()
        assert await reader.readline() == b"err_auth\r\n"
        assert await asyncio.wait_for(reader.read(), 1) == b""
    finally:
        writer.close()
        await writer.wait_closed()


async def test_err_auth_greeting_is_authentication_error(fake_factory: FakeFactory) -> None:
    # A host the projector's allow-list rejects gets err_auth instead of a nonce.
    fake = await _start(fake_factory, greeting_override="err_auth")
    conn = make_connection(fake.port)
    with pytest.raises(AuthenticationError, match="refused"):
        await conn.connect()
    assert _state(conn) == (False, None)
    # send() connects on demand and fails the same way, without writing anything.
    with pytest.raises(AuthenticationError):
        await conn.send("power_status ?")
    assert _state(conn) == (False, None)
    assert fake.received == []
    assert fake.connections == 2


async def test_unexpected_handshake_verdict_is_protocol_error(fake_factory: FakeFactory) -> None:
    fake = await _start(fake_factory, handshake_verdict_override="HELLO")
    conn = make_connection(fake.port)
    with pytest.raises(ProtocolError, match="HELLO"):
        await conn.connect()
    # The half-open socket is not kept.
    assert _state(conn) == (False, None)
    with pytest.raises(ProtocolError):
        await conn.send("power_status ?")
    assert _state(conn) == (False, None)
    assert fake.connections == 2


async def test_nokey_greeting_override_skips_authentication(fake_factory: FakeFactory) -> None:
    fake = await _start(fake_factory, password="secret", greeting_override="NOKEY")
    async with make_connection(fake.port, password="wrong") as conn:
        assert _state(conn) == (True, False)
        assert await conn.send("power_status ?") == "standby"


async def test_context_manager_connects_and_closes(fake: FakeProjector) -> None:
    async with make_connection(fake.port) as conn:
        assert conn.connected
        assert fake.connections == 1
    assert not conn.connected


async def test_connection_refused() -> None:
    conn = make_connection(_closed_port())
    with pytest.raises(AdcpConnectionError):
        await conn.send("power_status ?")
    assert not conn.connected


# -- commands --------------------------------------------------------------------


async def test_set_returns_none_and_changes_state(
    connection: AdcpConnection, fake: FakeProjector
) -> None:
    assert fake.power_status == "standby"
    assert await connection.send('power "on"') is None
    assert fake.power_status == "on"
    assert fake.received == ['power "on"']
    assert await connection.send('power "off"') is None
    assert fake.power_status == "standby"


async def test_query_string(connection: AdcpConnection) -> None:
    assert await connection.send("power_status ?") == "standby"


async def test_query_list(connection: AdcpConnection) -> None:
    timer = await connection.send("timer ?")
    assert isinstance(timer, list)
    assert timer == [{"operation": 3400}, {"light_src": 2300}, {"prev_light_src": 3000}]


async def test_query_number(connection: AdcpConnection) -> None:
    await connection.send('power "on"')
    assert await connection.send("contrast ?") == 0
    assert await connection.send("contrast 42") is None
    assert await connection.send("contrast ?") == 42


@pytest.mark.parametrize(
    ("command", "exc_type", "setup"),
    [
        ("no_such_command ?", CommandFormatError, None),
        ("power ?", CommandFormatError, None),
        ("dynamic_range ?", CommandOptionError, "on"),
        ("dynamic_range --hdmi9 ?", CommandOptionError, "on"),
        ("contrast --hdmi1 ?", CommandOptionError, "on"),
        ("contrast ?", CommandInactiveError, None),
        ('picture_mode "nope"', CommandValueError, "on"),
        ("contrast 101", CommandValueError, "on"),
    ],
)
async def test_error_replies_raise_typed_exceptions(
    connection: AdcpConnection,
    fake: FakeProjector,
    command: str,
    exc_type: type[CommandError],
    setup: str | None,
) -> None:
    if setup is not None:
        fake.power_status = setup
    with pytest.raises(exc_type) as info:
        await connection.send(command)
    assert type(info.value) is exc_type
    assert info.value.command == command
    assert info.value.reply == exc_type.reply_token


@pytest.mark.parametrize(
    ("token", "exc_type"), [("err_internal1", Internal1Error), ("err_internal2", Internal2Error)]
)
async def test_internal_errors_via_override(
    connection: AdcpConnection, fake: FakeProjector, token: str, exc_type: type[CommandError]
) -> None:
    fake.reply_overrides["power_status ?"] = token
    with pytest.raises(exc_type) as info:
        await connection.send("power_status ?")
    assert info.value.command == "power_status ?"
    assert info.value.reply == token
    # The session is still usable afterwards.
    del fake.reply_overrides["power_status ?"]
    assert await connection.send("power_status ?") == "standby"


async def test_unparseable_reply_is_protocol_error(
    connection: AdcpConnection, fake: FakeProjector
) -> None:
    fake.reply_overrides["power_status ?"] = '{"not": json'
    with pytest.raises(ProtocolError):
        await connection.send("power_status ?")


async def test_protocol_error_closes_session(
    connection: AdcpConnection, fake: FakeProjector
) -> None:
    assert await connection.send("modelname ?") == "VPL-VW295ES"
    fake.reply_overrides["power_status ?"] = "not json"
    with pytest.raises(ProtocolError):
        await connection.send("power_status ?")
    # The stream is in an unknown state, so the socket is dropped ...
    assert not connection.connected
    # ... and the next command transparently starts a new session.
    del fake.reply_overrides["power_status ?"]
    assert await connection.send("power_status ?") == "standby"
    assert _state(connection) == (True, True)
    assert fake.connections == 2


@pytest.mark.parametrize(
    "command",
    ["power_status ?\r\n", "power_status ?\n", "a" * 511, 'power "é"'],
)
async def test_command_validation_happens_before_io(fake: FakeProjector, command: str) -> None:
    conn = make_connection(fake.port)
    with pytest.raises(ValueError, match="command"):
        await conn.send(command)
    assert fake.connections == 0
    assert fake.received == []
    assert not conn.connected
    # 512 bytes including CR LF is still allowed by the client.
    assert len(("a" * 510).encode() + b"\r\n") == 512
    with pytest.raises(CommandFormatError):
        await conn.send("a" * 510)
    await conn.close()


# -- reconnect -------------------------------------------------------------------


async def test_reconnect_after_idle_disconnect(fake_factory: FakeFactory) -> None:
    fake = await _start(fake_factory, idle_timeout=0.2)
    conn = make_connection(fake.port)
    try:
        assert await conn.send("power_status ?") == "standby"
        await asyncio.sleep(0.4)
        assert await conn.send("power_status ?") == "standby"
        assert fake.connections == 2
        assert fake.received == ["power_status ?", "power_status ?"]
    finally:
        await conn.close()


async def test_set_after_idle_disconnect_reconnects_before_writing(
    fake_factory: FakeFactory,
) -> None:
    fake = await _start(fake_factory, idle_timeout=0.2)
    conn = make_connection(fake.port)
    try:
        assert await conn.send("power_status ?") == "standby"
        await asyncio.sleep(0.4)
        # The projector's close is already in the buffer, so even a set command
        # (which is never resent) goes out on a fresh session rather than failing.
        assert await conn.send('power "on"') is None
        assert fake.power_status == "on"
        assert fake.connections == 2
        assert fake.received == ["power_status ?", 'power "on"']
    finally:
        await conn.close()


async def test_set_is_not_resent_when_session_dies_mid_command(
    fake_factory: FakeFactory,
) -> None:
    fake = await _start(fake_factory)
    conn = make_connection(fake.port)
    try:
        assert await conn.send("modelname ?") == "VPL-VW295ES"
        fake.drop_after.add('power "on"')
        with pytest.raises(AdcpConnectionError):
            await conn.send('power "on"')
        # The projector may or may not have acted on it; the client must not guess.
        assert fake.received.count('power "on"') == 1
        assert not conn.connected
        assert fake.drop_after == set()
        assert fake.power_status == "standby"
        assert await conn.send("power_status ?") == "standby"
        assert fake.connections == 2
    finally:
        await conn.close()


async def test_set_dropped_on_fresh_session_is_not_resent(fake_factory: FakeFactory) -> None:
    # Same, but the set is the very first command after connecting.
    fake = await _start(fake_factory)
    fake.drop_after.add('power "on"')
    conn = make_connection(fake.port)
    try:
        with pytest.raises(AdcpConnectionError):
            await conn.send('power "on"')
        assert fake.received == ['power "on"']
        assert fake.power_status == "standby"
        assert await conn.send("power_status ?") == "standby"
        assert fake.connections == 2
        assert fake.received == ['power "on"', "power_status ?"]
    finally:
        await conn.close()


async def test_query_is_resent_once_when_session_dies_mid_command(
    fake_factory: FakeFactory,
) -> None:
    fake = await _start(fake_factory)
    conn = make_connection(fake.port)
    try:
        assert await conn.send("modelname ?") == "VPL-VW295ES"
        fake.drop_after.add("power_status ?")
        assert await conn.send("power_status ?") == "standby"
        assert fake.received.count("power_status ?") == 2
        assert fake.connections == 2
        assert conn.connected
    finally:
        await conn.close()


async def test_reconnect_after_explicit_close(
    connection: AdcpConnection, fake: FakeProjector
) -> None:
    assert await connection.send("power_status ?") == "standby"
    await connection.close()
    assert _state(connection) == (False, True)
    assert await connection.send("power_status ?") == "standby"
    assert _state(connection) == (True, True)
    assert fake.connections == 2


# -- wake-up quirks --------------------------------------------------------------


async def test_silent_wake_is_retried(fake_factory: FakeFactory) -> None:
    fake = await _start(fake_factory, wake_behaviour="silent")
    conn = make_connection(fake.port, wake_timeout=0.3)
    try:
        assert await conn.send("power_status ?") == "standby"
        assert fake.received == ["power_status ?", "power_status ?"]
        # The silent socket may still owe a reply, so the retry uses a new one.
        assert fake.connections == 2
        # Later commands on the same session are answered first time.
        assert await conn.send("power_status ?") == "standby"
        assert len(fake.received) == 3
    finally:
        await conn.close()


async def test_silent_wake_without_retries_times_out(fake_factory: FakeFactory) -> None:
    fake = await _start(fake_factory, wake_behaviour="silent")
    conn = make_connection(fake.port, wake_timeout=0.2, wake_retries=0)
    try:
        with pytest.raises(AdcpTimeoutError):
            await conn.send("power_status ?")
        assert not conn.connected
        assert fake.received == ["power_status ?"]
    finally:
        await conn.close()


async def test_err_cmd_wake_is_retried(fake_factory: FakeFactory) -> None:
    fake = await _start(fake_factory, wake_behaviour="err_cmd")
    conn = make_connection(fake.port)
    try:
        assert await conn.send("power_status ?") == "standby"
        assert fake.received == ["power_status ?", "power_status ?"]
    finally:
        await conn.close()


async def test_late_wake_reply_is_never_mistaken_for_next_answer(
    fake_factory: FakeFactory,
) -> None:
    # A VW/HW unit may answer the first command well after the client gave up.
    fake = await _start(fake_factory, wake_behaviour="late", wake_delay=0.4)
    conn = make_connection(fake.port, wake_timeout=0.15, wake_retry_delay=0.05)
    try:
        assert await conn.send("power_status ?") == "standby"
        assert fake.connections == 2
        assert fake.received == ["power_status ?", "power_status ?"]
        assert await conn.send("modelname ?") == "VPL-VW295ES"
        # Let the late "standby" land on the abandoned socket, then make sure
        # it cannot be read as the answer to something else.
        await asyncio.sleep(0.3)
        assert await conn.send("modelname ?") == "VPL-VW295ES"
        assert fake.connections == 2
    finally:
        await conn.close()


async def test_timeout_on_later_command_drops_session(fake_factory: FakeFactory) -> None:
    fake = await _start(fake_factory)
    fake.reply_delay["modelname ?"] = 0.5
    conn = make_connection(fake.port, command_timeout=0.1)
    try:
        assert await conn.send("power_status ?") == "standby"
        with pytest.raises(AdcpTimeoutError, match="modelname"):
            await conn.send("modelname ?")
        # Only the first command after a connect is retried; a later one is
        # not, and the socket that may still owe a reply is abandoned.
        assert fake.received == ["power_status ?", "modelname ?"]
        assert not conn.connected
        assert await conn.send("power_status ?") == "standby"
        assert fake.connections == 2
    finally:
        await conn.close()


async def test_wake_retries_bound_how_often_err_cmd_is_resent(fake_factory: FakeFactory) -> None:
    fake = await _start(fake_factory, wake_behaviour="err_cmd", wake_count=2)
    conn = make_connection(fake.port, wake_retries=2)
    try:
        assert await conn.send("power_status ?") == "standby"
        assert fake.received == ["power_status ?"] * 3
        # err_cmd retries reuse the socket; only a silent one is replaced.
        assert fake.connections == 1
    finally:
        await conn.close()


async def test_wake_retries_exhausted_raises_err_cmd(fake_factory: FakeFactory) -> None:
    fake = await _start(fake_factory, wake_behaviour="err_cmd", wake_count=3)
    conn = make_connection(fake.port, wake_retries=2)
    try:
        with pytest.raises(CommandFormatError):
            await conn.send("power_status ?")
        assert fake.received == ["power_status ?"] * 3
        # The session itself is fine and no longer counts as fresh.
        assert conn.connected
        assert await conn.send("power_status ?") == "standby"
        assert len(fake.received) == 4
        assert fake.connections == 1
    finally:
        await conn.close()


async def test_err_cmd_on_later_command_is_not_retried(
    connection: AdcpConnection, fake: FakeProjector
) -> None:
    assert await connection.send("power_status ?") == "standby"
    with pytest.raises(CommandFormatError):
        await connection.send("bogus ?")
    assert fake.received == ["power_status ?", "bogus ?"]


async def test_unsupported_first_command_is_retried_once_then_raised(
    connection: AdcpConnection, fake: FakeProjector
) -> None:
    # A genuine err_cmd on the first command costs one retry, then surfaces.
    with pytest.raises(CommandFormatError):
        await connection.send("bogus ?")
    assert fake.received == ["bogus ?", "bogus ?"]


# -- concurrency -----------------------------------------------------------------


async def test_concurrent_sends_are_serialised(
    connection: AdcpConnection, fake: FakeProjector
) -> None:
    commands = [f"contrast {i}" for i in range(20)]
    fake.power_status = "on"
    results = await asyncio.gather(*(connection.send(c) for c in commands))
    assert results == [None] * 20
    assert fake.received == commands
    assert fake.connections == 1
    assert fake.state["contrast"] == 19


async def test_cancelled_send_drops_session_so_late_reply_is_discarded(
    fake_factory: FakeFactory,
) -> None:
    fake = await _start(fake_factory)
    fake.reply_delay["power_status ?"] = 0.3
    conn = make_connection(fake.port)
    try:
        task = asyncio.create_task(conn.send("power_status ?"))
        await _wait_for(lambda: "power_status ?" in fake.received)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        # The socket that still owes a reply is dropped synchronously ...
        assert not conn.connected
        # ... so the next command cannot receive the late "standby".
        assert await conn.send("modelname ?") == "VPL-VW295ES"
        assert fake.connections == 2
        assert fake.received == ["power_status ?", "modelname ?"]
    finally:
        await conn.close()


async def test_cancelling_one_concurrent_send_leaves_the_others_intact(
    fake_factory: FakeFactory,
) -> None:
    fake = await _start(fake_factory)
    fake.power_status = "on"
    fake.reply_delay["serialnum ?"] = 0.3
    expected: dict[str, object] = {
        "modelname ?": "VPL-VW295ES",
        "power_status ?": "on",
        "serialnum ?": "1234567",
        "input ?": "hdmi1",
        "contrast ?": 0,
    }
    commands = list(expected)
    conn = make_connection(fake.port)
    try:
        tasks = [asyncio.create_task(conn.send(command)) for command in commands]
        # The lock hands out turns in order; cancel the third while it waits.
        await _wait_for(lambda: "serialnum ?" in fake.received)
        tasks[2].cancel()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        assert isinstance(results[2], asyncio.CancelledError)
        for index, command in enumerate(commands):
            if index != 2:
                assert results[index] == expected[command], command
        # Each command went out exactly once, in order, across two sessions.
        assert fake.received == commands
        assert fake.connections == 2
    finally:
        await conn.close()


async def test_two_clients_on_one_fake(fake: FakeProjector) -> None:
    async with make_connection(fake.port) as a, make_connection(fake.port) as b:
        assert fake.connections == 2
        assert await a.send('power "on"') is None
        assert await b.send("power_status ?") == "on"


# -- the fake's protocol behaviour (what the rest of the suite relies on) -------


async def test_info_replies(connection: AdcpConnection) -> None:
    assert await connection.send("picture_mode ? --info") == {
        "type": "menu_sel",
        "version": "1.0",
        "range": list(VW290ES.commands["picture_mode"].options or ()),
    }
    assert await connection.send("contrast ? --info") == {
        "type": "menu_num",
        "version": "1.0",
        "range": {"min": 0, "max": 100},
    }
    assert await connection.send("power_status ? --info") == {"type": "sys_stat", "version": "1.0"}
    assert await connection.send("picture_mode_reset ? --info") == {
        "type": "menu_exec",
        "version": "1.0",
    }
    assert await connection.send("power ? --info") == {
        "type": "sys_sel",
        "version": "1.0",
        "range": ["on", "off"],
    }
    assert await connection.send("blanking --top ? --info") == {
        "type": "menu_num",
        "version": "1.0",
        "range": {"min": 0, "max": 100},
    }


async def test_range_queries(connection: AdcpConnection, fake: FakeProjector) -> None:
    with pytest.raises(CommandInactiveError):
        await connection.send("contrast ? --range")
    fake.power_status = "on"
    assert await connection.send("contrast ? --range") == {"min": 0, "max": 100}
    assert await connection.send("input ? --range") == ["hdmi1", "hdmi2"]
    assert await connection.send("ipv4_ip_address ? --range") == {
        "min": "0.0.0.0",
        "max": "255.255.255.255",
    }
    with pytest.raises(CommandFormatError):
        await connection.send("power_status ? --range")
    with pytest.raises(CommandFormatError):
        await connection.send("picture_mode_reset ? --range")


async def test_step_is_only_reported_when_not_one(fake_factory: FakeFactory) -> None:
    model = profile_with(
        MINIMAL, add={"zoom": FakeCommand("menu_num", range=(-10, 10, 5))}, remove=("contrast",)
    )
    fake = await _start(fake_factory, password=None, model=model, power_gating=False)
    async with make_connection(fake.port) as conn:
        assert await conn.send("zoom ? --range") == {"min": -10, "max": 10, "step": 5}
        with pytest.raises(CommandValueError):
            await conn.send("zoom 3")
        assert await conn.send("zoom -10") is None
        with pytest.raises(CommandFormatError):
            await conn.send("contrast ?")
        assert await conn.send("modelname ?") == "VPL-FAKE"


async def test_form_rules(connection: AdcpConnection, fake: FakeProjector) -> None:
    fake.power_status = "on"
    for bad in (
        "picture_mode_reset ?",
        'power_status "x"',
        "power_status --rel 1",
        "power_status --reset",
        "picture_mode --rel 1",
        "contrast",
        "input",
        "contrast ? --bogus",
        "contrast --rel",
        "contrast abc",
        "",
    ):
        with pytest.raises(CommandFormatError, match=re.escape(repr(bad))):
            await connection.send(bad)


async def test_relative_and_reset(connection: AdcpConnection, fake: FakeProjector) -> None:
    fake.power_status = "on"
    assert await connection.send("contrast 99") is None
    assert await connection.send("contrast --rel 1") is None
    assert await connection.send("contrast ?") == 100
    with pytest.raises(CommandValueError):
        await connection.send("contrast --rel 1")
    assert fake.state["contrast"] == 100
    assert await connection.send("contrast --rel -5") is None
    assert await connection.send("contrast ?") == 95
    assert await connection.send("contrast --reset") is None
    assert await connection.send("contrast ?") == 0


async def test_suffixed_commands_keep_separate_values(
    connection: AdcpConnection, fake: FakeProjector
) -> None:
    fake.power_status = "on"
    assert await connection.send('dynamic_range --hdmi2 "full"') is None
    assert await connection.send("dynamic_range --hdmi1 ?") == "auto"
    assert await connection.send("dynamic_range --hdmi2 ?") == "full"
    assert fake.state["dynamic_range"] == {"hdmi1": "auto", "hdmi2": "full"}
    assert await connection.send("blanking --left 7") is None
    assert await connection.send("blanking --left ?") == 7
    assert await connection.send("blanking --right ?") == 0


async def test_power_gating(connection: AdcpConnection, fake: FakeProjector) -> None:
    for status in ("standby", "startup", "cooling1", "cooling2"):
        fake.power_status = status
        for command in ('input "hdmi1"', "contrast --rel 1", "input --reset", "picture_mode_reset"):
            with pytest.raises(CommandInactiveError):
                await connection.send(command)
        assert await connection.send("power_status ?") == status
        assert await connection.send("signal ?") == "Invalid"
        assert await connection.send("contrast ? --info") == {
            "type": "menu_num",
            "version": "1.0",
            "range": {"min": 0, "max": 100},
        }
    fake.power_status = "on"
    assert await connection.send('input "hdmi2"') is None
    assert await connection.send("signal ?") == "3840x2160/60p"
    assert await connection.send("picture_mode_reset") is None


async def test_power_gating_can_be_switched_off(fake_factory: FakeFactory) -> None:
    fake = await _start(fake_factory, power_gating=False)
    async with make_connection(fake.port) as conn:
        assert await conn.send("contrast ?") == 0


async def test_sys_stat_values(connection: AdcpConnection) -> None:
    assert await connection.send("error ?") == ["no_err"]
    assert await connection.send("warning ?") == ["no_warn"]
    assert await connection.send("modelname ?") == "VPL-VW295ES"
    assert await connection.send("serialnum ?") == "1234567"
    assert await connection.send("version ?") == [{"main": "1.000"}]
    assert await connection.send("mac_address ?") == "08-00-46-12-34-56"
    assert isinstance(await connection.send("ipv6_ip_address ?"), str)
    assert await connection.send("ipv6_prefix ?") == 64
    with pytest.raises(CommandFormatError):
        await connection.send("temperature ?")


async def test_sys_var(connection: AdcpConnection) -> None:
    assert await connection.send('ipv4_ip_address "10.0.0.5"') is None
    assert await connection.send("ipv4_ip_address ?") == "10.0.0.5"
    with pytest.raises(CommandValueError):
        await connection.send("ipv4_ip_address 5")


async def test_key_presses(connection: AdcpConnection, fake: FakeProjector) -> None:
    assert await connection.send('key "menu"') is None
    assert await connection.send('key "aspect_1.85_1_zoom"') is None
    with pytest.raises(CommandValueError):
        await connection.send('key "vol+"')
    with pytest.raises(CommandFormatError):
        await connection.send("key ?")
    assert fake.keys_pressed == ["menu", "aspect_1.85_1_zoom"]


async def test_bare_lf_is_tolerated(fake_nokey: FakeProjector) -> None:
    reader, writer = await asyncio.open_connection("127.0.0.1", fake_nokey.port)
    try:
        assert await reader.readline() == b"NOKEY\r\n"
        writer.write(b"power_status ?\n")
        await writer.drain()
        assert await reader.readline() == b'"standby"\r\n'
    finally:
        writer.close()
        await writer.wait_closed()
