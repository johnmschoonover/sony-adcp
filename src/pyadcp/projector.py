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
from typing import TYPE_CHECKING, Self

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
    from collections.abc import Iterable, Mapping
    from types import TracebackType

__all__ = [
    "Capabilities",
    "CommandCapability",
    "NumericRange",
    "PowerStatus",
    "Projector",
    "ProjectorIdentity",
    "ProjectorStatus",
]

logger = logging.getLogger(__name__)

NO_ERROR = "no_err"
NO_WARNING = "no_warn"
ON = "on"
OFF = "off"

PROBED_COMMANDS: tuple[str, ...] = tuple(name for name in COMMANDS if name != "key")
"""Commands that capability discovery asks about. ``key`` has no info form."""


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
class CommandCapability:
    """What the projector reported about one command via ``? --info``.

    Attributes:
        name: Command name.
        kind: Kind derived from the reported type (falls back to the table).
        type: Raw type string from the projector, e.g. ``"menu_sel"``.
        version: Command version string from the projector, e.g. ``"1.0"``.
        options: Valid choices for selection commands, ``None`` otherwise.
        range: Bounds for numeric commands, ``None`` otherwise.
        suffixes: Suffixes the projector accepted. Empty when the command
            takes no suffix.
    """

    name: str
    kind: CommandKind
    type: str
    version: str
    options: tuple[str, ...] | None = None
    range: NumericRange | None = None
    suffixes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Capabilities:
    """The set of commands a connected projector accepts.

    ``commands`` maps each supported command name to its capability;
    ``unsupported`` lists the names the projector answered ``err_cmd`` to;
    ``incomplete`` lists supported commands whose ``? --info`` answered
    ``err_inactive`` (typically because the projector was in standby), so
    their options or range are still unknown. :meth:`Projector.capabilities`
    re-probes those on its next call. Commands that were never probed
    (currently only ``key``) appear in none of the three.
    """

    commands: Mapping[str, CommandCapability] = field(default_factory=dict)
    unsupported: frozenset[str] = frozenset()
    incomplete: frozenset[str] = frozenset()

    def __contains__(self, name: object) -> bool:
        return name in self.commands

    def __getitem__(self, name: str) -> CommandCapability:
        return self.commands[name]

    def supports(self, name: str, suffix: str | None = None) -> bool:
        """Whether ``name`` (and ``suffix`` if given) is accepted by the projector."""
        cap = self.commands.get(name)
        if cap is None:
            return False
        return suffix is None or suffix in cap.suffixes

    def options(self, name: str) -> tuple[str, ...] | None:
        """Choices for a selection command, or ``None`` if unknown or unsupported."""
        cap = self.commands.get(name)
        return cap.options if cap is not None else None

    def range(self, name: str) -> NumericRange | None:
        """Bounds for a numeric command, or ``None`` if unknown or unsupported."""
        cap = self.commands.get(name)
        return cap.range if cap is not None else None


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
        self, *, refresh: bool = False, names: Iterable[str] | None = None
    ) -> Capabilities:
        """Discover which commands this projector supports.

        Sends ``? --info`` for every command in the table (and every suffix
        of suffixed commands) once and caches the result. A command the
        projector answers ``err_cmd`` to is recorded in
        :attr:`Capabilities.unsupported`; a suffix it answers ``err_option``
        to is simply left out of that command's ``suffixes``; a command that
        answers ``err_inactive`` is kept as supported but listed in
        :attr:`Capabilities.incomplete` and probed again on the next call,
        so calling this once more after the projector is on fills in what
        standby withheld.

        Args:
            refresh: Query the projector again even if a cached result exists.
            names: Probe only these commands and merge them into the cached
                result. Useful to limit the roughly 200 round trips a full
                discovery costs when only a few entities are wanted.
        """
        async with self._capabilities_lock:
            cached = self._capabilities
            if names is not None:
                targets: list[str] = [get_command(name).name for name in names]
                result = await self._discover(targets, cached)
            elif cached is None or refresh:
                result = await self._discover(list(PROBED_COMMANDS), None)
            else:
                # Complete a cache that an earlier names= probe left partial,
                # and retry what the projector withheld in standby.
                targets = [
                    name
                    for name in PROBED_COMMANDS
                    if (name not in cached.commands and name not in cached.unsupported)
                    or name in cached.incomplete
                ]
                result = await self._discover(targets, cached) if targets else cached
            self._capabilities = result
            return result

    async def _discover(self, targets: list[str], base: Capabilities | None) -> Capabilities:
        commands: dict[str, CommandCapability] = dict(base.commands) if base else {}
        unsupported = set(base.unsupported) if base else set()
        incomplete = set(base.incomplete) if base else set()
        result = base if base is not None else Capabilities()
        for name in targets:
            command = COMMANDS[name]
            if name not in PROBED_COMMANDS:
                continue
            previous = commands.pop(name, None)
            unsupported.discard(name)
            # When re-probing an incomplete command, only its known suffixes
            # need asking again; the rest already answered err_option.
            suffixes = previous.suffixes if previous and name in incomplete else None
            incomplete.discard(name)
            cap, inactive = await self._probe(command, suffixes)
            if cap is None:
                unsupported.add(name)
            else:
                commands[name] = cap
                if inactive:
                    incomplete.add(name)
            # Publish progress after every probe so a cancelled or failed
            # discovery keeps what it learned; the next call completes it.
            result = Capabilities(
                MappingProxyType(dict(commands)), frozenset(unsupported), frozenset(incomplete)
            )
            self._capabilities = result
        logger.debug(
            "capabilities: %d supported, %d unsupported, %d incomplete",
            len(commands),
            len(unsupported),
            len(incomplete),
        )
        return result

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

        info: CommandCapability | None = None
        found: list[str] = []
        inactive = False
        for suffix in suffixes if suffixes is not None else command.suffixes:
            cap, cap_inactive = await self._probe_one(command, suffix)
            if cap is None:
                if found:
                    # err_cmd for a later suffix: keep what the earlier ones said.
                    continue
                return None, False
            if cap.suffixes == ():
                # err_option: this suffix does not exist on this unit.
                continue
            found.append(suffix)
            inactive = inactive or cap_inactive
            if info is None and not cap_inactive:
                info = cap
        if not found:
            return None, False
        if info is None:
            return _unknown_capability(command, tuple(found)), True
        return dataclasses.replace(info, suffixes=tuple(found)), inactive

    async def _probe_one(
        self, command: Command, suffix: str | None
    ) -> tuple[CommandCapability | None, bool]:
        """Probe one command (and suffix). ``suffixes=()`` in the result flags err_option."""
        found = (suffix,) if suffix else ()
        line = command.query_info(suffix) if command.has_info else command.query_range(suffix)
        try:
            reply = await self.send(line)
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
        if not command.has_info:
            return _range_capability(command, reply, found), False
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
    options = parsed if isinstance(parsed, tuple) else None
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


def _range_capability(
    command: Command, reply: JsonValue, suffixes: tuple[str, ...]
) -> CommandCapability:
    """Capability for a command probed with ``? --range`` because it has no info form."""
    parsed = _parse_range(reply)
    return CommandCapability(
        name=command.name,
        kind=command.kind,
        type="",
        version="",
        options=parsed if isinstance(parsed, tuple) else None,
        range=parsed if isinstance(parsed, NumericRange) else None,
        suffixes=suffixes,
    )


def _unknown_capability(command: Command, suffixes: tuple[str, ...] = ()) -> CommandCapability:
    """A command the projector knows but did not describe (e.g. ``err_inactive`` in standby)."""
    return CommandCapability(
        name=command.name,
        kind=command.kind,
        type="",
        version="",
        suffixes=suffixes,
    )
