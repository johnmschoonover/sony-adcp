"""High-level facade over an ADCP connection.

:class:`Projector` turns the string protocol into typed coroutines that a Home
Assistant integration (or any other caller) can use directly: ``power_on()``,
``set_picture_mode("reference")``, ``poll_status()`` and so on. It also
discovers, once, what the connected unit supports via ``? --info`` so callers
can build their option lists and numeric ranges without any per-model tables.

The facade never assumes a model. Every setter takes the wire value as a
plain string or number and lets the projector validate it; every getter
returns what the projector said.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Self

from pyadcp.commands import COMMANDS, INFO_TYPE_KINDS, Command, CommandKind, get_command
from pyadcp.connection import DEFAULT_PASSWORD, DEFAULT_PORT, AdcpConnection, JsonValue
from pyadcp.exceptions import (
    CommandError,
    CommandFormatError,
    CommandInactiveError,
    CommandOptionError,
    ProtocolError,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable, Iterable, Mapping
    from types import TracebackType

__all__ = [
    "CAPABILITIES_FORMAT",
    "Capabilities",
    "CapabilityChanges",
    "CommandCapability",
    "DiscoveryEvent",
    "NumericRange",
    "PowerStatus",
    "Projector",
    "ProjectorIdentity",
    "ProjectorStatus",
    "SuffixCapability",
]

logger = logging.getLogger(__name__)

NO_ERROR = "no_err"
NO_WARNING = "no_warn"
ON = "on"
OFF = "off"

CAPABILITIES_FORMAT = 1
"""Version of the dictionary layout produced by :meth:`Capabilities.to_dict`."""

_EMPTY: Mapping[str, Any] = MappingProxyType({})


class PowerStatus(StrEnum):
    """The five power states every ADCP projector reports."""

    STANDBY = "standby"
    STARTUP = "startup"
    ON = "on"
    COOLING1 = "cooling1"
    COOLING2 = "cooling2"


@dataclass(frozen=True, slots=True)
class NumericRange:
    """Bounds of a numeric setting as reported by the projector."""

    min: int | float
    max: int | float
    step: int | float = 1


@dataclass(frozen=True, slots=True)
class SuffixCapability:
    """What the projector reported for one suffix of a suffixed command.

    Attributes:
        options: Valid choices for this suffix, ``None`` if not a selection
            or not yet known.
        range: Bounds for this suffix, ``None`` if not numeric or unknown.
        incomplete: ``True`` when the projector answered ``err_inactive``
            for this suffix, so its details are still to be asked for.
    """

    options: tuple[str, ...] | None = None
    range: NumericRange | None = None
    incomplete: bool = False


@dataclass(frozen=True, slots=True)
class CommandCapability:
    """What the projector reported about one command via ``? --info``.

    Attributes:
        name: Command name.
        kind: Kind derived from the reported type (falls back to the table).
        type: Raw type string from the projector, e.g. ``"menu_sel"``.
        version: Command version string from the projector, e.g. ``"1.0"``.
        options: Valid choices for selection commands, ``None`` otherwise.
            For a suffixed command these are the first answering suffix's;
            use :meth:`options_for` for a specific suffix.
        range: Bounds for numeric commands, ``None`` otherwise. For a
            suffixed command see :meth:`range_for`.
        suffixes: Suffixes the projector accepted. Empty when the command
            takes no suffix.
        by_suffix: Per-suffix detail for suffixed commands, keyed by suffix.
    """

    name: str
    kind: CommandKind
    type: str
    version: str
    options: tuple[str, ...] | None = None
    range: NumericRange | None = None
    suffixes: tuple[str, ...] = ()
    by_suffix: Mapping[str, SuffixCapability] = field(default_factory=lambda: _EMPTY)

    def options_for(self, suffix: str | None = None) -> tuple[str, ...] | None:
        """Choices for ``suffix`` (or the command itself when it has none)."""
        if suffix is None:
            return self.options
        detail = self.by_suffix.get(suffix)
        return detail.options if detail is not None else None

    def range_for(self, suffix: str | None = None) -> NumericRange | None:
        """Bounds for ``suffix`` (or the command itself when it has none)."""
        if suffix is None:
            return self.range
        detail = self.by_suffix.get(suffix)
        return detail.range if detail is not None else None

    def to_dict(self) -> dict[str, Any]:
        """A JSON-compatible representation; see :meth:`Capabilities.to_dict`."""
        return {
            "name": self.name,
            "kind": self.kind.value,
            "type": self.type,
            "version": self.version,
            "options": list(self.options) if self.options is not None else None,
            "range": _range_to_dict(self.range),
            "suffixes": list(self.suffixes),
            "by_suffix": {
                suffix: {
                    "options": list(d.options) if d.options is not None else None,
                    "range": _range_to_dict(d.range),
                    "incomplete": d.incomplete,
                }
                for suffix, d in self.by_suffix.items()
            },
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CommandCapability:
        """Inverse of :meth:`to_dict`. Raises ``ValueError`` on malformed input."""
        try:
            by_suffix = {
                str(suffix): SuffixCapability(
                    options=_tuple_or_none(d.get("options")),
                    range=_range_from_dict(d.get("range")),
                    incomplete=bool(d.get("incomplete", False)),
                )
                for suffix, d in dict(data.get("by_suffix") or {}).items()
            }
            return cls(
                name=str(data["name"]),
                kind=CommandKind(data["kind"]),
                type=str(data.get("type", "")),
                version=str(data.get("version", "")),
                options=_tuple_or_none(data.get("options")),
                range=_range_from_dict(data.get("range")),
                suffixes=tuple(str(x) for x in data.get("suffixes", ())),
                by_suffix=MappingProxyType(by_suffix),
            )
        except (KeyError, TypeError, AttributeError, ValueError) as exc:
            raise ValueError(f"malformed command capability: {exc}") from exc


@dataclass(frozen=True, slots=True)
class CapabilityChanges:
    """What differs between two :class:`Capabilities` snapshots.

    Produced by :meth:`Capabilities.changes_since`. All four sets hold
    command names.

    Attributes:
        added: Supported now but not before.
        removed: Supported before but not now.
        changed: Supported in both, but options, range or suffixes differ.
        completed: Listed as incomplete before and fully described now.
            These usually also appear in ``changed``.
    """

    added: frozenset[str] = frozenset()
    removed: frozenset[str] = frozenset()
    changed: frozenset[str] = frozenset()
    completed: frozenset[str] = frozenset()

    def __bool__(self) -> bool:
        return bool(self.added or self.removed or self.changed or self.completed)

    @property
    def affected(self) -> frozenset[str]:
        """Every command whose entity would need creating, removing or updating."""
        return self.added | self.removed | self.changed


@dataclass(frozen=True)
class Capabilities:
    """The set of commands a connected projector accepts.

    ``commands`` maps each supported command name to its capability;
    ``unsupported`` lists the names the projector answered ``err_cmd`` to;
    ``incomplete`` lists supported commands for which at least one
    ``? --info`` answered ``err_inactive`` (typically because the projector
    was in standby), so their options or range are still unknown.
    :meth:`Projector.capabilities` re-probes those on its next call.
    ``key`` is probed like any other command; its ``options`` are the remote
    key codes the unit accepts.

    Instances are plain data: :meth:`to_dict` and :meth:`from_dict` round
    trip them through JSON so a client can persist discovery across
    restarts, and :meth:`changes_since` tells a client what to update after
    a later discovery.
    """

    commands: Mapping[str, CommandCapability] = field(default_factory=dict)
    unsupported: frozenset[str] = frozenset()
    incomplete: frozenset[str] = frozenset()

    def __contains__(self, name: object) -> bool:
        return name in self.commands

    def __getitem__(self, name: str) -> CommandCapability:
        return self.commands[name]

    @property
    def probed(self) -> frozenset[str]:
        """Every command that has been classified, supported or not."""
        return frozenset(self.commands) | self.unsupported

    def supports(self, name: str, suffix: str | None = None) -> bool:
        """Whether ``name`` (and ``suffix`` if given) is accepted by the projector."""
        cap = self.commands.get(name)
        if cap is None:
            return False
        return suffix is None or suffix in cap.suffixes

    def options(self, name: str, suffix: str | None = None) -> tuple[str, ...] | None:
        """Choices for a selection command, or ``None`` if unknown or unsupported."""
        cap = self.commands.get(name)
        return cap.options_for(suffix) if cap is not None else None

    def range(self, name: str, suffix: str | None = None) -> NumericRange | None:
        """Bounds for a numeric command, or ``None`` if unknown or unsupported."""
        cap = self.commands.get(name)
        return cap.range_for(suffix) if cap is not None else None

    def changes_since(self, previous: Capabilities) -> CapabilityChanges:
        """Compare this (newer) snapshot with ``previous`` (older)."""
        now = set(self.commands)
        before = set(previous.commands)
        changed = frozenset(
            name
            for name in now & before
            if _details(self.commands[name]) != _details(previous.commands[name])
        )
        completed = frozenset(previous.incomplete - self.incomplete) & frozenset(now)
        return CapabilityChanges(
            added=frozenset(now - before),
            removed=frozenset(before - now),
            changed=changed,
            completed=completed,
        )

    def to_dict(self) -> dict[str, Any]:
        """A JSON-compatible dictionary (lists, dicts, strings, numbers, bools).

        The layout is versioned by the ``"format"`` key so a client can
        store it and read it back with :meth:`from_dict` after an upgrade.
        """
        return {
            "format": CAPABILITIES_FORMAT,
            "commands": {name: cap.to_dict() for name, cap in self.commands.items()},
            "unsupported": sorted(self.unsupported),
            "incomplete": sorted(self.incomplete),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Capabilities:
        """Rebuild a snapshot written by :meth:`to_dict`.

        Raises:
            ValueError: if ``data`` is malformed or was written by a newer
                library whose format this version does not understand. A
                client should treat that as "discover again".
        """
        try:
            fmt = data["format"]
        except (KeyError, TypeError) as exc:
            raise ValueError("capabilities data has no format marker") from exc
        if fmt != CAPABILITIES_FORMAT:
            raise ValueError(
                f"capabilities format {fmt!r} is not supported "
                f"(this library writes format {CAPABILITIES_FORMAT})"
            )
        try:
            commands = {
                str(name): CommandCapability.from_dict(cap)
                for name, cap in dict(data.get("commands") or {}).items()
            }
            unsupported = frozenset(str(x) for x in data.get("unsupported", ()))
            incomplete = frozenset(str(x) for x in data.get("incomplete", ()))
        except (TypeError, AttributeError, ValueError) as exc:
            raise ValueError(f"malformed capabilities data: {exc}") from exc
        return cls(MappingProxyType(commands), unsupported, incomplete)


@dataclass(frozen=True, slots=True)
class DiscoveryEvent:
    """One step of capability discovery, as yielded by
    :meth:`Projector.discover_capabilities` or passed to the ``progress``
    callback of :meth:`Projector.capabilities`.

    Attributes:
        name: The command just probed.
        index: 1-based position within this discovery run.
        total: Number of commands this run will probe.
        capability: What the projector reported, or ``None`` if unsupported.
        incomplete: Whether the projector withheld details (``err_inactive``).
        snapshot: The cumulative :class:`Capabilities` after this step.
    """

    name: str
    index: int
    total: int
    capability: CommandCapability | None
    incomplete: bool
    snapshot: Capabilities

    @property
    def supported(self) -> bool:
        """``True`` unless the projector answered ``err_cmd``."""
        return self.capability is not None


@dataclass(frozen=True, slots=True)
class ProjectorIdentity:
    """Static identity fields read once by :meth:`Projector.identify`."""

    modelname: str | None = None
    serialnum: str | None = None
    mac_address: str | None = None
    version: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True, slots=True)
class ProjectorStatus:
    """Everything :meth:`Projector.poll_status` reads in one go.

    Fields a projector does not support, or that it refuses while in standby,
    are ``None`` (or empty for the collections) rather than raising.
    """

    power_status: PowerStatus
    error: tuple[str, ...] = ()
    warning: tuple[str, ...] = ()
    timer: Mapping[str, int] = field(default_factory=lambda: MappingProxyType({}))
    signal: str | None = None
    version: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    mac_address: str | None = None
    modelname: str | None = None
    serialnum: str | None = None

    @property
    def has_error(self) -> bool:
        """``True`` when the projector reports at least one error condition."""
        return any(token != NO_ERROR for token in self.error)

    @property
    def has_warning(self) -> bool:
        """``True`` when the projector reports at least one warning."""
        return any(token != NO_WARNING for token in self.warning)


class Projector:
    """Typed, asyncio-native control of one Sony projector over ADCP.

    Args:
        host: Projector address.
        port: ADCP port.
        password: ADCP password (ignored when the projector has auth off).
        connection: An existing :class:`AdcpConnection` to use instead of
            building one from the other arguments.
        connect_timeout: See :class:`AdcpConnection`.
        command_timeout: See :class:`AdcpConnection`.
        wake_timeout: See :class:`AdcpConnection`.
        wake_retry_delay: See :class:`AdcpConnection`.
        wake_retries: See :class:`AdcpConnection`.
    """

    def __init__(
        self,
        host: str = "",
        port: int = DEFAULT_PORT,
        password: str = DEFAULT_PASSWORD,
        *,
        connection: AdcpConnection | None = None,
        connect_timeout: float = 5.0,
        command_timeout: float = 5.0,
        wake_timeout: float = 10.0,
        wake_retry_delay: float = 1.0,
        wake_retries: int = 1,
    ) -> None:
        if connection is None:
            if not host:
                raise ValueError("host is required when no connection is given")
            connection = AdcpConnection(
                host,
                port,
                password,
                connect_timeout=connect_timeout,
                command_timeout=command_timeout,
                wake_timeout=wake_timeout,
                wake_retry_delay=wake_retry_delay,
                wake_retries=wake_retries,
            )
        self._connection = connection
        self._capabilities: Capabilities | None = None
        self._capabilities_lock = asyncio.Lock()
        self._identity: ProjectorIdentity | None = None

    # -- lifecycle -----------------------------------------------------------

    @property
    def connection(self) -> AdcpConnection:
        """The underlying connection, for callers that need raw access."""
        return self._connection

    async def connect(self) -> None:
        """Open the connection now instead of on first use."""
        await self._connection.connect()

    async def close(self) -> None:
        """Close the connection. It reopens on the next command."""
        await self._connection.close()

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

    # -- generic access ------------------------------------------------------

    async def send(self, line: str) -> JsonValue:
        """Send a raw command line and return the parsed reply."""
        return await self._connection.send(line)

    async def get_select(self, name: str, suffix: str | None = None) -> str:
        """Query a selection command and return its current choice."""
        reply = await self.send(get_command(name).query(suffix))
        if not isinstance(reply, str):
            raise ProtocolError(f"{name} returned {reply!r}, expected a string")
        return reply

    async def set_select(self, name: str, value: str, suffix: str | None = None) -> None:
        """Set a selection command to ``value``."""
        await self.send(get_command(name).set(value, suffix))

    async def get_number(self, name: str, suffix: str | None = None) -> int | float:
        """Query a numeric command and return its current value."""
        reply = await self.send(get_command(name).query(suffix))
        if isinstance(reply, bool) or not isinstance(reply, int | float):
            raise ProtocolError(f"{name} returned {reply!r}, expected a number")
        return reply

    async def set_number(self, name: str, value: int | float, suffix: str | None = None) -> None:
        """Set a numeric command to ``value``."""
        await self.send(get_command(name).set(value, suffix))

    async def adjust_number(self, name: str, delta: int, suffix: str | None = None) -> None:
        """Change a numeric command by ``delta`` using the ``--rel`` form."""
        await self.send(get_command(name).set_relative(delta, suffix))

    async def reset(self, name: str, suffix: str | None = None) -> None:
        """Reset a setting to its factory default (``--reset``)."""
        await self.send(get_command(name).reset(suffix))

    async def execute(self, name: str) -> None:
        """Run an action command such as ``picture_mode_reset``."""
        await self.send(get_command(name).execute())

    async def get_range(
        self, name: str, suffix: str | None = None
    ) -> tuple[str, ...] | NumericRange:
        """Ask what is valid *right now* for a command (``? --range``)."""
        reply = await self.send(get_command(name).query_range(suffix))
        parsed = _parse_range(reply)
        if parsed is None:
            raise ProtocolError(f"{name} range reply {reply!r} not understood")
        return parsed

    async def get_info(self, name: str, suffix: str | None = None) -> CommandCapability:
        """Ask the projector to describe one command (``? --info``)."""
        command = get_command(name)
        reply = await self.send(command.query_info(suffix))
        return _parse_info(command, reply, (suffix,) if suffix else ())

    # -- power ---------------------------------------------------------------

    async def power_on(self) -> None:
        """Turn the projector on."""
        await self.set_select("power", ON)

    async def power_off(self) -> None:
        """Put the projector into standby."""
        await self.set_select("power", OFF)

    async def power_status(self) -> PowerStatus:
        """Read the power state."""
        return _power_status(await self.get_select("power_status"))

    # -- input and picture ---------------------------------------------------

    async def get_input(self) -> str:
        """Current input terminal, e.g. ``"hdmi1"``."""
        return await self.get_select("input")

    async def set_input(self, terminal: str) -> None:
        """Select an input terminal, e.g. ``"hdmi2"``."""
        await self.set_select("input", terminal)

    async def get_blank(self) -> bool:
        """Whether picture muting is active."""
        return _on_off(await self.get_select("blank"))

    async def set_blank(self, muted: bool) -> None:
        """Mute or unmute the picture."""
        await self.set_select("blank", ON if muted else OFF)

    async def get_picture_mode(self) -> str:
        """Current picture preset."""
        return await self.get_select("picture_mode")

    async def set_picture_mode(self, mode: str) -> None:
        """Select a picture preset such as ``"reference"``."""
        await self.set_select("picture_mode", mode)

    async def reset_picture_mode(self) -> None:
        """Reset the current picture preset's adjustments."""
        await self.execute("picture_mode_reset")

    async def get_aspect(self) -> str:
        """Current aspect ratio mode."""
        return await self.get_select("aspect")

    async def set_aspect(self, aspect: str) -> None:
        """Select an aspect ratio mode such as ``"normal"``."""
        await self.set_select("aspect", aspect)

    async def get_hdr(self) -> str:
        """Current HDR mode."""
        return await self.get_select("hdr")

    async def set_hdr(self, mode: str) -> None:
        """Select an HDR mode such as ``"auto"``."""
        await self.set_select("hdr", mode)

    async def get_lamp_control(self) -> str:
        """Lamp output mode on lamp models (``"high"`` or ``"low"``)."""
        return await self.get_select("lamp_control")

    async def set_lamp_control(self, mode: str) -> None:
        """Set lamp output mode on lamp models."""
        await self.set_select("lamp_control", mode)

    async def get_dynamic_range(self, hdmi: int | str) -> str:
        """Dynamic range setting (``auto``/``limited``/``full``) of one HDMI input."""
        return await self.get_select("dynamic_range", _hdmi(hdmi))

    async def set_dynamic_range(self, hdmi: int | str, mode: str) -> None:
        """Set the dynamic range of one HDMI input."""
        await self.set_select("dynamic_range", mode, _hdmi(hdmi))

    async def get_hdmi_signal_format(self, hdmi: int | str) -> str:
        """HDMI signal format (``standard``/``enhanced``) of one input."""
        return await self.get_select("hdmi_signal_format", _hdmi(hdmi))

    async def set_hdmi_signal_format(self, hdmi: int | str, mode: str) -> None:
        """Set the HDMI signal format of one input."""
        await self.set_select("hdmi_signal_format", mode, _hdmi(hdmi))

    async def get_contrast(self) -> int | float:
        """Contrast adjustment."""
        return await self.get_number("contrast")

    async def set_contrast(self, value: int) -> None:
        """Set contrast."""
        await self.set_number("contrast", value)

    async def get_brightness(self) -> int | float:
        """Brightness adjustment."""
        return await self.get_number("brightness")

    async def set_brightness(self, value: int) -> None:
        """Set brightness."""
        await self.set_number("brightness", value)

    async def get_color(self) -> int | float:
        """Colour depth adjustment."""
        return await self.get_number("color")

    async def set_color(self, value: int) -> None:
        """Set colour depth."""
        await self.set_number("color", value)

    async def get_hue(self) -> int | float:
        """Hue adjustment."""
        return await self.get_number("hue")

    async def set_hue(self, value: int) -> None:
        """Set hue."""
        await self.set_number("hue", value)

    async def get_sharpness(self) -> int | float:
        """Sharpness adjustment."""
        return await self.get_number("sharpness")

    async def set_sharpness(self, value: int) -> None:
        """Set sharpness."""
        await self.set_number("sharpness", value)

    async def get_color_temp(self) -> str:
        """Current colour temperature preset."""
        return await self.get_select("color_temp")

    async def set_color_temp(self, preset: str) -> None:
        """Select a colour temperature preset such as ``"d65"``."""
        await self.set_select("color_temp", preset)

    async def get_gamma_correction(self) -> str:
        """Current gamma correction mode."""
        return await self.get_select("gamma_correction")

    async def set_gamma_correction(self, mode: str) -> None:
        """Select a gamma correction mode such as ``"2.2"``."""
        await self.set_select("gamma_correction", mode)

    async def get_motionflow(self) -> str:
        """Current Motionflow mode."""
        return await self.get_select("motionflow")

    async def set_motionflow(self, mode: str) -> None:
        """Select a Motionflow mode such as ``"true_cinema"``."""
        await self.set_select("motionflow", mode)

    async def get_input_lag_reduction(self) -> bool:
        """Whether input lag reduction is on."""
        return _on_off(await self.get_select("input_lag_red"))

    async def set_input_lag_reduction(self, enabled: bool) -> None:
        """Switch input lag reduction on or off."""
        await self.set_select("input_lag_red", ON if enabled else OFF)

    async def get_menu_position(self) -> str:
        """Where the on-screen menu is drawn."""
        return await self.get_select("menu_pos")

    async def set_menu_position(self, position: str) -> None:
        """Move the on-screen menu (``"bottom_left"`` or ``"center"``)."""
        await self.set_select("menu_pos", position)

    async def send_key(self, code: str) -> None:
        """Emulate a remote control key press, e.g. ``"menu"`` or ``"picmode1"``."""
        await self.set_select("key", code)

    # -- status --------------------------------------------------------------

    async def identify(self, *, refresh: bool = False) -> ProjectorIdentity:
        """Read model name, serial number, MAC address and firmware versions.

        These never change while connected, so the result is cached and
        reused by :meth:`poll_status`. Fields a model lacks are ``None``.
        """
        if self._identity is None or refresh:
            self._identity = ProjectorIdentity(
                modelname=_optional_string(await self._optional("modelname")),
                serialnum=_optional_string(await self._optional("serialnum")),
                mac_address=_optional_string(await self._optional("mac_address")),
                version=_named_strings(await self._optional("version")),
            )
        return self._identity

    async def poll_status(self, *, refresh_identity: bool = False) -> ProjectorStatus:
        """Read the health and identity fields in one call.

        ``power_status`` must succeed; every other field degrades to ``None``
        or empty when the projector answers ``err_cmd`` (unsupported) or
        ``err_inactive`` (not available in the current power state). The
        identity fields come from :meth:`identify` and are only read from
        the projector once unless ``refresh_identity`` is set, so a routine
        poll costs five round trips.
        """
        power = _power_status(await self.get_select("power_status"))
        error = _string_tuple(await self._optional("error"))
        warning = _string_tuple(await self._optional("warning"))
        timer = _named_numbers(await self._optional("timer"))
        signal = _optional_string(await self._optional("signal"))
        identity = await self.identify(refresh=refresh_identity)
        return ProjectorStatus(
            power_status=power,
            error=error,
            warning=warning,
            timer=timer,
            signal=signal,
            version=identity.version,
            mac_address=identity.mac_address,
            modelname=identity.modelname,
            serialnum=identity.serialnum,
        )

    async def _optional(self, name: str) -> JsonValue:
        try:
            return await self.send(get_command(name).query())
        except (CommandFormatError, CommandInactiveError) as exc:
            logger.debug("%s not available: %s", name, exc.reply)
            return None

    # -- capabilities --------------------------------------------------------

    async def capabilities(
        self,
        *,
        refresh: bool = False,
        names: Iterable[str] | None = None,
        progress: Callable[[DiscoveryEvent], None] | None = None,
    ) -> Capabilities:
        """Discover which commands this projector supports.

        Sends ``? --info`` for every command in the table (and every suffix
        of suffixed commands) once and caches the result. A command the
        projector answers ``err_cmd`` to is recorded in
        :attr:`Capabilities.unsupported`; a suffix it answers ``err_cmd`` or
        ``err_option`` to is simply left out of that command's ``suffixes``
        (a VW295ES answers ``err_cmd`` to an HDMI input it does not have);
        a command that answers ``err_inactive`` is kept as supported but listed in
        :attr:`Capabilities.incomplete` and probed again on the next call,
        so calling this once more after the projector is on fills in what
        standby withheld.

        Args:
            refresh: Query the projector again even if a cached result exists.
            names: Probe only these commands and merge them into the cached
                result. Useful to limit the roughly 200 round trips a full
                discovery costs when only a few entities are wanted.
            progress: Called after every probe with a :class:`DiscoveryEvent`.
                For an async consumer, iterate :meth:`discover_capabilities`
                instead.
        """
        result = self._capabilities
        async for event in self.discover_capabilities(refresh=refresh, names=names):
            result = event.snapshot
            if progress is not None:
                progress(event)
        if result is None:
            # Nothing to probe (an empty names list on a fresh object).
            result = Capabilities()
            self._capabilities = result
        return result

    def restore_capabilities(self, capabilities: Capabilities) -> None:
        """Seed the cache from a snapshot persisted earlier with :meth:`Capabilities.to_dict`.

        The next :meth:`capabilities` call then only probes what the
        snapshot lists as incomplete or has never classified, instead of
        running a full discovery. Pass ``refresh=True`` to that call to
        discard the snapshot and start over.
        """
        self._capabilities = capabilities

    async def discover_capabilities(
        self, *, refresh: bool = False, names: Iterable[str] | None = None
    ) -> AsyncGenerator[DiscoveryEvent, None]:
        """Run capability discovery one command at a time.

        Yields a :class:`DiscoveryEvent` after each probe, so a caller can
        show progress or create entities as they are found. The cache is
        updated after every probe, so stopping early keeps what was learned
        and a later call continues from there. Arguments are as for
        :meth:`capabilities`. When nothing needs probing the iterator ends
        without yielding; read :meth:`capabilities` for the cached result.
        """
        async with self._capabilities_lock:
            targets = self._targets(refresh, names)
            if refresh and names is None:
                self._capabilities = Capabilities()
        total = len(targets)
        for index, name in enumerate(targets, start=1):
            async with self._capabilities_lock:
                base = self._capabilities or Capabilities()
                cap, inactive = await self._probe_into(COMMANDS[name], base)
                snapshot = _merge(base, name, cap, inactive)
                self._capabilities = snapshot
            yield DiscoveryEvent(name, index, total, cap, inactive, snapshot)
        logger.debug("capabilities: probed %d commands", total)

    def _targets(self, refresh: bool, names: Iterable[str] | None) -> list[str]:
        if names is not None:
            return [get_command(name).name for name in names]
        cached = self._capabilities
        if cached is None or refresh:
            return list(COMMANDS)
        # Complete a cache that an earlier names= probe left partial, and
        # retry what the projector withheld in standby.
        return [name for name in COMMANDS if name not in cached.probed or name in cached.incomplete]

    async def _probe_into(
        self, command: Command, base: Capabilities
    ) -> tuple[CommandCapability | None, bool]:
        """Probe ``command``, re-asking only the suffixes still incomplete in ``base``."""
        previous = base.commands.get(command.name)
        if previous is None or command.name not in base.incomplete or not command.suffixes:
            return await self._probe(command)
        pending = tuple(s for s in previous.suffixes if previous.by_suffix[s].incomplete)
        cap, _ = await self._probe(command, pending or previous.suffixes)
        if cap is None:
            return None, False
        # Keep the suffixes that were already complete.
        merged = dict(previous.by_suffix)
        merged.update(cap.by_suffix)
        return _assemble(command, cap, merged), any(d.incomplete for d in merged.values())

    async def _probe(
        self, command: Command, suffixes: tuple[str, ...] | None = None
    ) -> tuple[CommandCapability | None, bool]:
        """Describe one command from the projector's point of view.

        Returns the capability (``None`` if the projector does not know the
        command) and whether the answer was ``err_inactive``, meaning the
        details should be asked for again later. Any other error reply or
        an unexpected reply shape counts as "supported, details unknown"
        rather than aborting discovery, so one odd command cannot stop the
        other hundred from being classified.
        """
        if not command.suffixes:
            return await self._probe_one(command, None)

        first: CommandCapability | None = None
        details: dict[str, SuffixCapability] = {}
        for suffix in suffixes if suffixes is not None else command.suffixes:
            cap, cap_inactive = await self._probe_one(command, suffix)
            if cap is None or cap.suffixes == ():
                # err_cmd or err_option: this suffix does not exist on this
                # unit. The command itself is unsupported only when every
                # suffix answers that way.
                continue
            details[suffix] = SuffixCapability(cap.options, cap.range, cap_inactive)
            if first is None and not cap_inactive:
                first = cap
        if not details:
            return None, False
        template = first if first is not None else _unknown_capability(command)
        return _assemble(command, template, details), any(d.incomplete for d in details.values())

    async def _probe_one(
        self, command: Command, suffix: str | None
    ) -> tuple[CommandCapability | None, bool]:
        """Probe one command (and suffix). ``suffixes=()`` in the result flags err_option."""
        found = (suffix,) if suffix else ()
        try:
            reply = await self.send(command.query_info(suffix))
        except CommandFormatError:
            return None, False
        except CommandOptionError:
            # With a suffix: that suffix does not exist here. Without one: the
            # unit wants an option the table does not know; keep the command.
            return _unknown_capability(command, ()), False
        except CommandInactiveError:
            return _unknown_capability(command, found), True
        except CommandError as exc:
            logger.debug("%s answered %s to a probe; details unknown", command.name, exc.reply)
            return _unknown_capability(command, found), False
        try:
            return _parse_info(command, reply, found), False
        except ProtocolError as exc:
            logger.debug("%s info reply not understood: %s", command.name, exc)
            return _unknown_capability(command, found), False


# -- helpers -----------------------------------------------------------------


def _hdmi(terminal: int | str) -> str:
    if isinstance(terminal, int):
        return f"hdmi{terminal}"
    return terminal


def _on_off(value: str) -> bool:
    if value == ON:
        return True
    if value == OFF:
        return False
    raise ProtocolError(f"expected 'on' or 'off', got {value!r}")


def _power_status(value: str) -> PowerStatus:
    try:
        return PowerStatus(value)
    except ValueError as exc:
        raise ProtocolError(f"unknown power status {value!r}") from exc


def _string_tuple(value: JsonValue) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list):
        return tuple(item for item in value if isinstance(item, str))
    return ()


def _optional_string(value: JsonValue) -> str | None:
    return value if isinstance(value, str) else None


def _named_numbers(value: JsonValue) -> Mapping[str, int]:
    """Flatten ``[{"operation":3400},{"light_src":2300}]`` into a mapping."""
    out: dict[str, int] = {}
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                for key, number in item.items():
                    if isinstance(number, int) and not isinstance(number, bool):
                        out[key] = number
    return MappingProxyType(out)


def _named_strings(value: JsonValue) -> Mapping[str, str]:
    """Flatten ``[{"main":"1.000"},{"laser":"1.000"}]`` into a mapping."""
    out: dict[str, str] = {}
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                for key, text in item.items():
                    if isinstance(text, str):
                        out[key] = text
    return MappingProxyType(out)


def _parse_range(value: JsonValue) -> tuple[str, ...] | NumericRange | None:
    if isinstance(value, list):
        return tuple(item for item in value if isinstance(item, str))
    if isinstance(value, dict):
        low = _number(value.get("min"))
        high = _number(value.get("max"))
        step = _number(value.get("step", 1))
        if low is not None and high is not None and step is not None:
            return NumericRange(low, high, step)
    return None


def _number(value: JsonValue) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return value


def _parse_info(command: Command, reply: JsonValue, suffixes: tuple[str, ...]) -> CommandCapability:
    if not isinstance(reply, dict):
        raise ProtocolError(f"{command.name} info reply {reply!r} is not an object")
    type_ = reply.get("type")
    version = reply.get("version")
    type_name = type_ if isinstance(type_, str) else ""
    kind = INFO_TYPE_KINDS.get(type_name, command.kind)
    parsed = _parse_range(reply.get("range")) if "range" in reply else None
    # A sys_stat info reply may list the "--field" sub-queries the status
    # supports (timer, version); those are not choices a caller can set.
    options = parsed if isinstance(parsed, tuple) and kind is not CommandKind.STATUS else None
    numeric = parsed if isinstance(parsed, NumericRange) else None
    return CommandCapability(
        name=command.name,
        kind=kind,
        type=type_name,
        version=version if isinstance(version, str) else "",
        options=options,
        range=numeric,
        suffixes=suffixes,
    )


def _merge(
    base: Capabilities, name: str, cap: CommandCapability | None, inactive: bool
) -> Capabilities:
    """Return ``base`` with one command's probe result applied."""
    commands = dict(base.commands)
    unsupported = set(base.unsupported)
    incomplete = set(base.incomplete)
    commands.pop(name, None)
    unsupported.discard(name)
    incomplete.discard(name)
    if cap is None:
        unsupported.add(name)
    else:
        commands[name] = cap
        if inactive:
            incomplete.add(name)
    return Capabilities(MappingProxyType(commands), frozenset(unsupported), frozenset(incomplete))


def _assemble(
    command: Command, template: CommandCapability, details: Mapping[str, SuffixCapability]
) -> CommandCapability:
    """Build a suffixed command's capability from per-suffix detail.

    The top-level options and range are those of the first suffix that is
    fully described, in the table's suffix order, for callers that do not
    care which suffix they look at.
    """
    ordered = {s: details[s] for s in command.suffixes if s in details}
    head = next((d for d in ordered.values() if not d.incomplete), None)
    return dataclasses.replace(
        template,
        options=head.options if head else None,
        range=head.range if head else None,
        suffixes=tuple(ordered),
        by_suffix=MappingProxyType(ordered),
    )


def _details(cap: CommandCapability) -> tuple[object, ...]:
    """The parts of a capability that matter for entity creation."""
    return (cap.kind, cap.options, cap.range, cap.suffixes, dict(cap.by_suffix))


def _tuple_or_none(value: object) -> tuple[str, ...] | None:
    if value is None:
        return None
    if isinstance(value, str) or not isinstance(value, list | tuple):
        raise TypeError(f"options must be a list, got {value!r}")
    return tuple(str(x) for x in value)


def _range_to_dict(value: NumericRange | None) -> dict[str, int | float] | None:
    if value is None:
        return None
    return {"min": value.min, "max": value.max, "step": value.step}


def _range_from_dict(value: object) -> NumericRange | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise TypeError(f"range must be an object, got {value!r}")
    low, high, step = value["min"], value["max"], value.get("step", 1)
    for item in (low, high, step):
        if isinstance(item, bool) or not isinstance(item, int | float):
            raise TypeError(f"range bounds must be numbers, got {item!r}")
    return NumericRange(low, high, step)


def _unknown_capability(command: Command, suffixes: tuple[str, ...] = ()) -> CommandCapability:
    """A command the projector knows but did not describe (e.g. ``err_inactive`` in standby)."""
    return CommandCapability(
        name=command.name,
        kind=command.kind,
        type="",
        version="",
        suffixes=suffixes,
    )
