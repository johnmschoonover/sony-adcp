"""Tests for the SDAP advertisement codec and listener."""

from __future__ import annotations

import asyncio
import contextlib
import gc
import struct
import warnings
from typing import Any

import pytest

from pyadcp.sdap import (
    POWER_STATUS_UNKNOWN,
    PROJECTOR_CATEGORY,
    SDAP_ID,
    SDAP_PACKET_SIZE,
    SdapError,
    SdapPacket,
    build_packet,
    discover,
    listen,
    parse_packet,
)

LOCALHOST = "127.0.0.1"


def _sample_packet() -> bytes:
    return build_packet(
        community="SONY",
        product_name="VPL-VW295ES",
        serial=0x01020304,
        power_status=0x0001,
        location="Living room",
    )


async def _free_udp_port() -> int:
    """Return a UDP port that is currently free on the loopback interface."""
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(
        asyncio.DatagramProtocol, local_addr=(LOCALHOST, 0)
    )
    port: int = transport.get_extra_info("sockname")[1]
    transport.close()
    # The transport closes its socket from a deferred callback; let it run so
    # the port is really free before the caller binds it.
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    return port


async def _send(port: int, *datagrams: bytes) -> None:
    """Send each datagram, in order, to ``port`` on the loopback interface."""
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(
        asyncio.DatagramProtocol, remote_addr=(LOCALHOST, port)
    )
    for datagram in datagrams:
        transport.sendto(datagram)
    transport.close()


async def _send_until_done(port: int, done: asyncio.Future[Any], *datagrams: bytes) -> None:
    """Re-send ``datagrams`` every 20 ms until ``done`` finishes.

    ``listen`` binds its socket lazily on first iteration, so a single send
    could race ahead of the bind; repeating keeps the tests deterministic.
    """
    while not done.done():
        await _send(port, *datagrams)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(asyncio.shield(done), 0.02)


# --- codec -----------------------------------------------------------------


def test_round_trip_every_field() -> None:
    data = build_packet(
        community="ABCD",
        product_name="VPL-VW295ES",
        serial=0xDEADBEEF,
        power_status=0x1234,
        location="Rack 3, row 2",
        version=2,
        category=0x0B,
    )
    packet = parse_packet(data)
    assert packet == SdapPacket(
        version=2,
        category=0x0B,
        community="ABCD",
        product_name="VPL-VW295ES",
        serial=0xDEADBEEF,
        serial_raw=b"\xde\xad\xbe\xef",
        power_status=0x1234,
        power_status_raw=b"\x12\x34",
        location="Rack 3, row 2",
        address=None,
    )
    assert packet.power_status_known is True


def test_build_defaults() -> None:
    packet = parse_packet(build_packet(product_name="X", serial=1, power_status=0))
    assert packet.version == 1
    assert packet.category == PROJECTOR_CATEGORY
    assert packet.community == "SONY"
    assert packet.location == ""


def test_exact_byte_layout() -> None:
    expected = struct.pack(
        ">2sBB4s12sIH24s",
        b"DA",
        1,
        0x0A,
        b"SONY",
        b"VPL-VW295ES\x00",
        0x01020304,
        0x0001,
        b"Living room".ljust(24, b"\x00"),
    )
    assert len(expected) == SDAP_PACKET_SIZE == 50
    data = _sample_packet()
    assert data == expected
    assert data[0:2] == SDAP_ID == b"\x44\x41"
    assert data[2] == 1
    assert data[3] == 0x0A
    assert data[4:8] == b"SONY"
    assert data[8:20] == b"VPL-VW295ES\x00"
    assert data[20:24] == b"\x01\x02\x03\x04"
    assert data[24:26] == b"\x00\x01"
    assert data[26:50] == b"Living room" + b"\x00" * 13

    packet = parse_packet(data)
    assert packet.serial == 0x01020304
    assert packet.serial_raw == b"\x01\x02\x03\x04"
    assert packet.power_status == 1
    assert packet.power_status_raw == b"\x00\x01"


@pytest.mark.parametrize("length", [0, 49, 51, 100])
def test_wrong_length_rejected(length: int) -> None:
    data = (_sample_packet() * 3)[:length]
    with pytest.raises(SdapError, match="50 bytes"):
        parse_packet(data)


def test_wrong_id_rejected() -> None:
    data = bytearray(_sample_packet())
    data[0:2] = b"AD"
    with pytest.raises(SdapError, match="DA"):
        parse_packet(bytes(data))


def test_sdap_error_is_value_error() -> None:
    assert issubclass(SdapError, ValueError)


def test_version_and_category_not_validated() -> None:
    data = bytearray(_sample_packet())
    data[2] = 7
    data[3] = 0x55
    packet = parse_packet(bytes(data))
    assert packet.version == 7
    assert packet.category == 0x55


def test_unknown_power_status() -> None:
    packet = parse_packet(build_packet(product_name="P", serial=1, power_status=0xFFFF))
    assert packet.power_status == POWER_STATUS_UNKNOWN
    assert packet.power_status_raw == b"\xff\xff"
    assert packet.power_status_known is False


def test_strings_padded_and_stripped() -> None:
    data = build_packet(
        community="AB", product_name="Short", serial=0, power_status=0, location="L"
    )
    assert data[4:8] == b"AB\x00\x00"
    assert data[8:20] == b"Short" + b"\x00" * 7
    assert data[26:50] == b"L" + b"\x00" * 23
    packet = parse_packet(data)
    assert packet.community == "AB"
    assert packet.product_name == "Short"
    assert packet.location == "L"


def test_full_width_strings_round_trip() -> None:
    data = build_packet(
        community="WXYZ",
        product_name="A" * 12,
        serial=0xFFFFFFFF,
        power_status=0xFFFE,
        location="B" * 24,
    )
    packet = parse_packet(data)
    assert packet.community == "WXYZ"
    assert packet.product_name == "A" * 12
    assert packet.location == "B" * 24
    assert packet.serial == 0xFFFFFFFF


def test_community_space_padding_stripped() -> None:
    data = bytearray(_sample_packet())
    data[4:8] = b"AB  "
    assert parse_packet(bytes(data)).community == "AB"


def test_non_ascii_bytes_replaced_not_raised() -> None:
    data = bytearray(_sample_packet())
    data[8:20] = b"VPL\xff" + b"\x00" * 8
    assert parse_packet(bytes(data)).product_name == "VPL�"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"product_name": "A" * 13},
        {"location": "B" * 25},
        {"community": "ABCDE"},
        {"product_name": "café"},
        {"serial": -1},
        {"serial": 1 << 32},
        {"power_status": 0x10000},
        {"version": 256},
        {"category": -1},
    ],
)
def test_build_rejects_bad_fields(kwargs: dict[str, object]) -> None:
    base: dict[str, object] = {"product_name": "P", "serial": 1, "power_status": 0}
    with pytest.raises(ValueError, match=next(iter(kwargs))):
        build_packet(**{**base, **kwargs})  # type: ignore[arg-type]


# --- network ---------------------------------------------------------------


async def test_listen_receives_packet() -> None:
    port = await _free_udp_port()

    async def first() -> SdapPacket:
        async with contextlib.aclosing(listen(port=port, local_addr=LOCALHOST, timeout=1.0)) as it:
            async for packet in it:
                return packet
        pytest.fail("listener finished without a packet")

    receiver = asyncio.ensure_future(first())
    await _send_until_done(port, receiver, _sample_packet())
    packet = await receiver

    assert packet.product_name == "VPL-VW295ES"
    assert packet.serial == 0x01020304
    assert packet.address is not None
    assert packet.address[0] == LOCALHOST


async def test_discover_returns_packet_with_address_and_deduplicates() -> None:
    port = await _free_udp_port()
    finder = asyncio.ensure_future(discover(timeout=0.5, port=port, local_addr=LOCALHOST))
    # Every round sends the same packet twice; only one copy may come back.
    await _send_until_done(port, finder, _sample_packet(), _sample_packet())
    found = await finder

    assert len(found) == 1
    (packet,) = found
    assert packet.serial == 0x01020304
    assert packet.address is not None
    assert packet.address[0] == LOCALHOST
    assert packet.address[1] > 0


async def test_discover_keeps_distinct_serials_in_arrival_order() -> None:
    port = await _free_udp_port()
    first = build_packet(product_name="ONE", serial=1, power_status=0)
    second = build_packet(product_name="TWO", serial=2, power_status=0)
    finder = asyncio.ensure_future(discover(timeout=0.5, port=port, local_addr=LOCALHOST))
    await _send_until_done(port, finder, first, second, first)
    found = await finder

    assert [p.product_name for p in found] == ["ONE", "TWO"]


async def test_malformed_datagram_skipped(caplog: pytest.LogCaptureFixture) -> None:
    port = await _free_udp_port()
    caplog.set_level("DEBUG", logger="pyadcp.sdap")
    finder = asyncio.ensure_future(discover(timeout=0.5, port=port, local_addr=LOCALHOST))
    await _send_until_done(port, finder, b"garbage", b"XX" + _sample_packet()[2:], _sample_packet())
    found = await finder

    assert len(found) == 1
    assert found[0].product_name == "VPL-VW295ES"
    assert any("Ignoring datagram" in record.message for record in caplog.records)


async def test_discover_nothing_on_the_wire() -> None:
    port = await _free_udp_port()
    loop = asyncio.get_running_loop()
    started = loop.time()
    assert await discover(timeout=0.2, port=port, local_addr=LOCALHOST) == []
    assert 0.15 <= loop.time() - started < 0.9


async def test_listen_timeout_stops_iteration() -> None:
    port = await _free_udp_port()
    packets = [p async for p in listen(port=port, local_addr=LOCALHOST, timeout=0.1)]
    assert packets == []


async def test_listen_cancellation_closes_transport() -> None:
    port = await _free_udp_port()

    async def run() -> None:
        async with contextlib.aclosing(listen(port=port, local_addr=LOCALHOST)) as it:
            async for _ in it:
                pass

    task = asyncio.ensure_future(run())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # Let the transport's deferred connection_lost callback run.
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        gc.collect()
    assert not [w for w in caught if issubclass(w.category, ResourceWarning)]

    # The port must be free again: bind it without any reuse flags.
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(
        asyncio.DatagramProtocol, local_addr=(LOCALHOST, port)
    )
    transport.close()
