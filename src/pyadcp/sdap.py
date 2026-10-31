"""SDAP (Simple Display Advertisement Protocol) listener and codec.

Sony projectors announce themselves on the local network by broadcasting one
UDP datagram to port 53862 every 30 seconds (port, interval, community and
extra unicast destinations are all configurable on the projector's web page).
The service is only available over the network; a unit driven through RS-232C
does not advertise, and neither does one that is still in its initial setup.

Every advertisement is exactly 50 bytes, laid out as fixed-width fields with
no separators:

================  ====  ======================================================
Offset (bytes)    Size  Field
================  ====  ======================================================
0                 2     Identifier, always ``b"DA"`` (``0x44 0x41``)
2                 1     Protocol version, ``1`` on every unit documented so far
3                 1     Equipment category, ``0x0A`` for a projector
4                 4     Community string (the SNMP-style name set on the unit)
8                 12    Product name, ASCII, padded on the right with ``0x00``
20                4     Serial number
24                2     Power status; ``0xFFFF`` when the projector's network
                        board could not read it from the main unit
26                24    Installation location, ASCII, padded with ``0x00``
================  ====  ======================================================

The manual does not say in which byte order the serial number and the power
status are stored, nor what the individual power status values mean beyond
``0xFFFF``. A VPL-VW295ES confirmed big-endian for the serial number and
reported ``0`` in standby, ``3`` when on, ``4`` and ``5`` while cooling; this module
decodes both fields as big-endian unsigned integers, names the values seen so
far and keeps the raw bytes next to the decoded value.

The networking half is asyncio-native: :func:`listen` yields packets as they
arrive and :func:`discover` collects them for a fixed window, long enough to
catch at least one advertisement from every unit at the factory interval.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
import struct
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Mapping

__all__ = [
    "POWER_STATUS_NAMES",
    "POWER_STATUS_UNKNOWN",
    "PROJECTOR_CATEGORY",
    "SDAP_ID",
    "SDAP_PACKET_SIZE",
    "SDAP_PORT",
    "SDAP_VERSION",
    "SdapError",
    "SdapPacket",
    "build_packet",
    "discover",
    "listen",
    "parse_packet",
]

logger = logging.getLogger(__name__)

SDAP_PORT = 53862
"""Factory-default UDP port the advertisement is broadcast to."""

SDAP_ID = b"DA"
"""Fixed two-byte identifier at the start of every packet (``0x44 0x41``)."""

SDAP_VERSION = 1
"""The only protocol version the manual defines."""

SDAP_PACKET_SIZE = 50
"""Total length of an advertisement datagram in bytes."""

PROJECTOR_CATEGORY = 0x0A
"""Equipment category code used by projectors."""

POWER_STATUS_UNKNOWN = 0xFFFF
"""Power status value sent when the projector could not read its own state."""

POWER_STATUS_NAMES: Mapping[int, str] = MappingProxyType(
    {0: "standby", 3: "on", 4: "cooling1", 5: "cooling2"}
)
"""Power status values seen on hardware and what ``power_status ?`` said at the time.

A VPL-VW295ES advertised ``0`` in standby, ``3`` when on, ``4`` while
``power_status ?`` reported ``cooling1`` and ``5`` during ``cooling2``. The
value during ``startup`` has not been observed; it lasts about ten seconds
and never coincided with an advertisement. The names are the ADCP
``power_status`` values so the two sources can be compared directly.
"""

DEFAULT_COMMUNITY = "SONY"
"""Factory-default community string."""

ADVERTISEMENT_INTERVAL = 30.0
"""Factory-default seconds between advertisements."""

_COMMUNITY_SIZE = 4
_PRODUCT_NAME_SIZE = 12
_SERIAL_SIZE = 4
_POWER_STATUS_SIZE = 2
_LOCATION_SIZE = 24

_LAYOUT = struct.Struct(
    ">"  # big-endian for the single-byte fields; multi-byte ints are handled by hand
    "2s"  # ID
    "B"  # version
    "B"  # category
    f"{_COMMUNITY_SIZE}s"
    f"{_PRODUCT_NAME_SIZE}s"
    f"{_SERIAL_SIZE}s"
    f"{_POWER_STATUS_SIZE}s"
    f"{_LOCATION_SIZE}s"
)
assert _LAYOUT.size == SDAP_PACKET_SIZE  # layout sanity check at import time

type _Datagram = tuple[bytes, tuple[str, int]]


class SdapError(ValueError):
    """A datagram is not a well-formed SDAP advertisement."""


@dataclass(frozen=True, slots=True)
class SdapPacket:
    """One decoded SDAP advertisement.

    Attributes:
        version: Protocol version byte. The manual only defines ``1``, but any
            value is accepted and exposed so a future revision is not dropped.
        category: Equipment category byte; :data:`PROJECTOR_CATEGORY` for a
            projector. Any value is accepted.
        community: Community string with trailing NUL and space padding removed.
        product_name: Product name with trailing NUL padding removed.
        serial: Serial number decoded as a big-endian unsigned integer. The
            manual does not state the byte order, so this is a best guess until
            confirmed against hardware; ``serial_raw`` holds the untouched bytes.
        serial_raw: The four serial number bytes exactly as received.
        power_status: Power status decoded as a big-endian unsigned integer.
            :data:`POWER_STATUS_UNKNOWN` (``0xFFFF``) means the projector could
            not read it. The manual documents no other value; see
            :data:`POWER_STATUS_NAMES` for the ones seen on hardware and
            :attr:`power_state` for a name. ``power_status_raw`` holds the
            untouched bytes.
        power_status_raw: The two power status bytes exactly as received.
        location: Installation location with trailing NUL padding removed.
        address: ``(ip, port)`` of the sender when the packet came off the
            network, ``None`` when it was parsed from bytes supplied directly.
    """

    version: int
    category: int
    community: str
    product_name: str
    serial: int
    serial_raw: bytes
    power_status: int
    power_status_raw: bytes
    location: str
    address: tuple[str, int] | None = None

    @property
    def power_status_known(self) -> bool:
        """``True`` unless the projector reported it could not read its power state."""
        return self.power_status != POWER_STATUS_UNKNOWN

    @property
    def power_state(self) -> str | None:
        """The ADCP ``power_status`` name for a value seen on hardware, else ``None``.

        ``"standby"``, ``"on"``, ``"cooling1"`` or ``"cooling2"``; ``startup``
        has no known value yet.
        """
        return POWER_STATUS_NAMES.get(self.power_status)


def _decode(field: bytes, *, strip: str = "\x00") -> str:
    """Decode a fixed-width ASCII field, replacing bad bytes and dropping padding."""
    return field.decode("ascii", errors="replace").rstrip(strip)


def _encode(value: str, size: int, *, name: str) -> bytes:
    """Encode ``value`` as ASCII and NUL-pad it to exactly ``size`` bytes."""
    try:
        raw = value.encode("ascii")
    except UnicodeEncodeError as exc:
        msg = f"{name} must be ASCII: {value!r}"
        raise ValueError(msg) from exc
    if len(raw) > size:
        msg = f"{name} must be at most {size} bytes, got {len(raw)}: {value!r}"
        raise ValueError(msg)
    return raw.ljust(size, b"\x00")


def _check_unsigned(value: int, bits: int, *, name: str) -> int:
    """Return ``value`` if it fits in an unsigned integer of ``bits`` bits."""
    if not 0 <= value < (1 << bits):
        msg = f"{name} must be an unsigned {bits}-bit integer, got {value!r}"
        raise ValueError(msg)
    return value


def parse_packet(data: bytes, address: tuple[str, int] | None = None) -> SdapPacket:
    """Decode a 50-byte SDAP datagram into an :class:`SdapPacket`.

    Args:
        data: The raw datagram.
        address: The sender's ``(ip, port)``, stored on the packet as-is.

    Raises:
        SdapError: If ``data`` is not exactly :data:`SDAP_PACKET_SIZE` bytes
            long or does not start with :data:`SDAP_ID`. The version and
            category bytes are not validated.
    """
    if len(data) != SDAP_PACKET_SIZE:
        msg = f"SDAP packet must be {SDAP_PACKET_SIZE} bytes, got {len(data)}"
        raise SdapError(msg)
    (
        packet_id,
        version,
        category,
        community,
        product_name,
        serial_raw,
        power_status_raw,
        location,
    ) = _LAYOUT.unpack(data)
    if packet_id != SDAP_ID:
        msg = f"SDAP packet must start with {SDAP_ID!r}, got {bytes(packet_id)!r}"
        raise SdapError(msg)
    return SdapPacket(
        version=version,
        category=category,
        community=_decode(community, strip="\x00 "),
        product_name=_decode(product_name),
        serial=int.from_bytes(serial_raw, "big"),
        serial_raw=bytes(serial_raw),
        power_status=int.from_bytes(power_status_raw, "big"),
        power_status_raw=bytes(power_status_raw),
        location=_decode(location),
        address=address,
    )


def build_packet(
    *,
    community: str = DEFAULT_COMMUNITY,
    product_name: str,
    serial: int,
    power_status: int,
    location: str = "",
    version: int = SDAP_VERSION,
    category: int = PROJECTOR_CATEGORY,
) -> bytes:
    """Encode the fields of an advertisement into a 50-byte datagram.

    This is the exact inverse of :func:`parse_packet` and exists mainly so a
    fake projector can advertise itself in tests. Strings are ASCII-encoded and
    NUL-padded to their field width; integers are stored big-endian.

    Raises:
        ValueError: If a string is not ASCII or longer than its field, or an
            integer does not fit its field.
    """
    return _LAYOUT.pack(
        SDAP_ID,
        _check_unsigned(version, 8, name="version"),
        _check_unsigned(category, 8, name="category"),
        _encode(community, _COMMUNITY_SIZE, name="community"),
        _encode(product_name, _PRODUCT_NAME_SIZE, name="product_name"),
        _check_unsigned(serial, 8 * _SERIAL_SIZE, name="serial").to_bytes(_SERIAL_SIZE, "big"),
        _check_unsigned(power_status, 8 * _POWER_STATUS_SIZE, name="power_status").to_bytes(
            _POWER_STATUS_SIZE, "big"
        ),
        _encode(location, _LOCATION_SIZE, name="location"),
    )


class _SdapProtocol(asyncio.DatagramProtocol):
    """Hands every received datagram to a queue; ``None`` marks the end of the stream."""

    def __init__(self, queue: asyncio.Queue[_Datagram | None]) -> None:
        self._queue = queue

    def datagram_received(self, data: bytes, addr: Any) -> None:
        # IPv6 sockets report a 4-tuple; keep only what callers need.
        self._queue.put_nowait((bytes(data), (str(addr[0]), int(addr[1]))))

    def error_received(self, exc: Exception) -> None:
        logger.debug("SDAP socket error: %r", exc)

    def connection_lost(self, exc: Exception | None) -> None:
        if exc is not None:
            logger.debug("SDAP socket closed with error: %r", exc)
        self._queue.put_nowait(None)


def _open_socket(local_addr: str, port: int) -> socket.socket:
    """Create a non-blocking UDP socket bound to ``(local_addr, port)``.

    asyncio refuses ``reuse_address=True`` for datagram endpoints, so the
    socket options are set here instead: ``SO_REUSEADDR`` (and ``SO_REUSEPORT``
    where the platform has it) so several listeners can share the
    advertisement port, and ``SO_BROADCAST`` so broadcast datagrams are
    accepted. ``bind`` does not block, so this is safe to call from the loop.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setblocking(False)
        sock.bind((local_addr, port))
    except OSError:
        sock.close()
        raise
    return sock


async def listen(
    *,
    port: int = SDAP_PORT,
    local_addr: str = "0.0.0.0",
    timeout: float | None = None,  # noqa: ASYNC109 - the deadline bounds the stream itself
) -> AsyncGenerator[SdapPacket, None]:
    """Yield every valid SDAP advertisement received on ``port``.

    The socket is bound when iteration starts and closed when the generator
    finishes, is cancelled, or is closed early, so wrap it in
    :func:`contextlib.aclosing` if you may break out of the loop.

    Args:
        port: UDP port to listen on.
        local_addr: Local IPv4 address (not a host name) to bind. Keep the
            default: on Linux and macOS a socket bound to one interface's
            unicast address does not receive broadcast datagrams, so a
            specific address only works for unicast advertisements aimed at
            this host.
        timeout: Stop after this many seconds in total. ``None`` listens forever.

    Datagrams that are not valid advertisements are logged at ``DEBUG`` level
    and skipped.
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[_Datagram | None] = asyncio.Queue()
    sock = _open_socket(local_addr, port)
    transport, _ = await loop.create_datagram_endpoint(lambda: _SdapProtocol(queue), sock=sock)
    logger.debug("Listening for SDAP advertisements on %s", transport.get_extra_info("sockname"))
    deadline = None if timeout is None else loop.time() + timeout
    try:
        while True:
            if deadline is None:
                item = await queue.get()
            else:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    return
                try:
                    item = await asyncio.wait_for(queue.get(), remaining)
                except TimeoutError:
                    return
            if item is None:
                logger.debug("SDAP transport closed; stopping listener")
                return
            data, address = item
            try:
                packet = parse_packet(data, address)
            except SdapError as exc:
                logger.debug("Ignoring datagram from %s: %s", address, exc)
                continue
            yield packet
    finally:
        transport.close()


async def discover(
    timeout: float = ADVERTISEMENT_INTERVAL + 5.0,  # noqa: ASYNC109 - the window is the point
    *,
    port: int = SDAP_PORT,
    local_addr: str = "0.0.0.0",
) -> list[SdapPacket]:
    """Collect advertisements for ``timeout`` seconds and return one per projector.

    Projectors advertise every 30 seconds by default, so the default window of
    35 seconds sees each of them at least once. Packets are de-duplicated by
    ``(serial, sender ip)``; the first one seen wins and the result keeps
    arrival order.

    An empty list is not an error: it means no projector on this subnet has
    its Advertisement service switched on (PJ Talk and DDDP do not matter),
    so the caller should fall back to asking for an address. A projector on
    another subnet can still be found if its Advertisement page lists this
    host as an extra destination: it then sends a unicast copy here, which
    the default wildcard socket receives and the de-duplication folds into
    the broadcast copies.
    """
    found: dict[tuple[int, str], SdapPacket] = {}
    async with contextlib.aclosing(
        listen(port=port, local_addr=local_addr, timeout=timeout)
    ) as packets:
        async for packet in packets:
            sender_ip = packet.address[0] if packet.address is not None else ""
            found.setdefault((packet.serial, sender_ip), packet)
    return list(found.values())
