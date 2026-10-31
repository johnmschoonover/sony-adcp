"""An asyncio TCP server that behaves like a Sony projector speaking ADCP.

The fake implements the wire protocol described in ``docs/protocol.md`` (our
own notes derived from Sony's COMMON and Supported Command List manuals):
the ``NOKEY`` / nonce handshake, one CR LF terminated reply per command line,
the ``sys_*`` / ``menu_*`` command types with their allowed forms, suffixes,
``? --range`` and ``? --info`` queries, power gating with ``err_inactive``,
remote-control ``key`` emulation, the idle connection time-out and the
"first command after waking" quirks. It is the only thing the test-suite
talks to, so it tries to be strict rather than forgiving.

The fake is self-contained on purpose: it must not import anything from
``pyadcp`` so that it stays an independent check of the client.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import hashlib
import json
import re
import secrets
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Literal, Self

__all__ = [
    "MINIMAL",
    "VW290ES",
    "FakeCommand",
    "FakeProjector",
    "ModelProfile",
    "PowerStatus",
    "WakeBehaviour",
    "profile_with",
]

LINE_END = b"\r\n"

type CommandType = Literal["sys_sel", "sys_stat", "sys_var", "menu_sel", "menu_num", "menu_exec"]
type PowerStatus = Literal["standby", "startup", "on", "cooling1", "cooling2"]
type WakeBehaviour = Literal["normal", "silent", "late", "err_cmd"]
type Form = Literal["set", "rel", "reset", "exec", "query", "range", "info"]

POWER_STATUSES: frozenset[str] = frozenset({"standby", "startup", "on", "cooling1", "cooling2"})


# -- model description ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FakeCommand:
    """One command the fake projector understands.

    Attributes:
        type: The ADCP command type reported by ``? --info``.
        options: Valid quoted values for the ``*_sel`` types.
        range: ``(min, max, step)`` for ``menu_num``.
        suffixes: When set, a ``--suffix`` is mandatory and must be one of these.
        default: Initial value. For a suffixed command this may be a mapping
            of suffix to value, otherwise the same value is used for every
            suffix. ``None`` picks a sensible default for the type.
    """

    type: CommandType
    options: tuple[str, ...] | None = None
    range: tuple[int, int, int] | None = None
    suffixes: tuple[str, ...] | None = None
    default: object = None


@dataclass(frozen=True, slots=True)
class ModelProfile:
    """Everything that differs between projector models."""

    modelname: str
    serialnum: str
    mac_address: str
    commands: Mapping[str, FakeCommand]
    key_codes: frozenset[str]
    version: list[dict[str, str]]


def profile_with(
    base: ModelProfile,
    *,
    modelname: str | None = None,
    serialnum: str | None = None,
    mac_address: str | None = None,
    version: list[dict[str, str]] | None = None,
    add: Mapping[str, FakeCommand] | None = None,
    remove: Iterable[str] = (),
    add_keys: Iterable[str] = (),
    remove_keys: Iterable[str] = (),
) -> ModelProfile:
    """Return a copy of ``base`` with commands or key codes added or removed."""
    commands = {name: cmd for name, cmd in base.commands.items() if name not in set(remove)}
    if add:
        commands.update(add)
    key_codes = (base.key_codes | frozenset(add_keys)) - frozenset(remove_keys)
    return dataclasses.replace(
        base,
        modelname=base.modelname if modelname is None else modelname,
        serialnum=base.serialnum if serialnum is None else serialnum,
        mac_address=base.mac_address if mac_address is None else mac_address,
        version=list(base.version) if version is None else version,
        commands=commands,
        key_codes=key_codes,
    )


def _sel(*options: str, suffixes: tuple[str, ...] | None = None) -> FakeCommand:
    return FakeCommand("menu_sel", options=options, suffixes=suffixes)


def _num(
    lo: int = 0, hi: int = 100, step: int = 1, *, suffixes: tuple[str, ...] | None = None
) -> FakeCommand:
    return FakeCommand("menu_num", range=(lo, hi, step), suffixes=suffixes)


_ON_OFF = ("on", "off")
_HDMI = ("hdmi1", "hdmi2")
_RGB = ("r", "g", "b")
_RGBCYM = ("r", "g", "b", "c", "y", "m")

_SYSTEM_COMMANDS: dict[str, FakeCommand] = {
    # sys_sel
    "power": FakeCommand("sys_sel", options=("on", "off")),
    "ipv4_network_setting": FakeCommand("sys_sel", options=("start", "apply")),
    "ipv4_set_method": FakeCommand("sys_sel", options=("auto", "manual")),
    # sys_stat
    "power_status": FakeCommand("sys_stat"),
    "error": FakeCommand("sys_stat"),
    "warning": FakeCommand("sys_stat"),
    "timer": FakeCommand("sys_stat"),
    "modelname": FakeCommand("sys_stat"),
    "serialnum": FakeCommand("sys_stat"),
    "signal": FakeCommand("sys_stat"),
    "version": FakeCommand("sys_stat"),
    "mac_address": FakeCommand("sys_stat"),
    "ipv6_ip_address": FakeCommand("sys_stat"),
    "ipv6_default_gateway": FakeCommand("sys_stat"),
    "ipv6_prefix": FakeCommand("sys_stat"),
    # sys_var
    "ipv4_ip_address": FakeCommand("sys_var", default="192.168.0.10"),
    "ipv4_sub_net_mask": FakeCommand("sys_var", default="255.255.255.0"),
    "ipv4_default_gateway": FakeCommand("sys_var", default="192.168.0.1"),
}

_VW290ES_MENU_COMMANDS: dict[str, FakeCommand] = {
    # Remote control function commands
    "input": _sel(*_HDMI),
    "blank": _sel(*_ON_OFF),
    # Image quality
    "picture_mode": _sel(
        "cinema_film1",
        "cinema_film2",
        "reference",
        "tv",
        "photo",
        "brt_cinema",
        "brt_tv",
        "user",
        "game",
    ),
    "picture_mode_reset": FakeCommand("menu_exec"),
    "contrast": _num(0, 100),
    "brightness": _num(0, 100),
    "color": _num(0, 100),
    "hue": _num(0, 100),
    "sharpness": _num(0, 100),
    "color_temp": _sel(
        "custom1", "custom2", "custom3", "custom4", "custom5", "d93", "d75", "d65", "d55"
    ),
    "coltemp_gain_r": _num(-50, 50),
    "coltemp_gain_g": _num(-50, 50),
    "coltemp_gain_b": _num(-50, 50),
    "coltemp_bias_r": _num(-50, 50),
    "coltemp_bias_g": _num(-50, 50),
    "coltemp_bias_b": _num(-50, 50),
    "color_space": _sel(
        "bt709", "bt2020", "color_space1", "color_space2", "color_space3", "custom"
    ),
    "col_space_x": _num(0, 100, suffixes=_RGB),
    "col_space_y": _num(0, 100, suffixes=_RGB),
    "gamma_correction": _sel(
        "1.8", "2.0", "2.1", "2.2", "2.4", "2.6", "gamma7", "gamma8", "gamma9", "gamma10", "off"
    ),
    "film_mode": _sel("auto", "off"),
    "real_cre": _sel(*_ON_OFF),
    "real_cre_db": _sel("mi4k", "normal"),
    "real_cre_reso": _num(0, 100),
    "real_cre_noise": _num(0, 100),
    "motionflow": _sel("smooth_high", "smooth_low", "true_cinema", "off"),
    "nr": _sel("auto", "high", "mid", "low", "off"),
    "mnr": _sel("auto", "high", "mid", "low", "off"),
    "smooth_grd": _sel("high", "mid", "low", "off"),
    "contrast_enh": _sel("high", "mid", "low", "off"),
    "col_correction": _sel(*_ON_OFF),
    "col_corr_hue": _num(0, 100, suffixes=_RGBCYM),
    "col_corr_color": _num(0, 100, suffixes=_RGBCYM),
    "col_corr_brt": _num(0, 100, suffixes=_RGBCYM),
    "clear_white": _sel("high", "low", "off"),
    "xvcolor": _sel(*_ON_OFF),
    "input_lag_red": _sel(*_ON_OFF),
    "lamp_control": _sel("high", "low"),
    "hdr": _sel("auto", "hdr10", "hdr_reference", "hlg", "off"),
    "hdr_auto": _sel("hdr10_hlg", "hdr_reference_hlg"),
    # Screen
    "aspect": _sel("normal", "v_stretch", "squeeze", "1.85_1_zoom", "2.35_1_zoom", "stretch"),
    # Function
    "2d3d_sel": _sel("auto", "3d"),
    "3d_format": _sel("sim3d", "sidebyside", "overunder"),
    "3d_depth": _num(0, 100),
    "sim3d_effect": _sel("high", "mid", "low"),
    "test_pattern": _sel(*_ON_OFF),
    "setting_lock": _sel("off", "level_a", "level_b"),
    "hdmi_signal_format": _sel("standard", "enhanced", suffixes=_HDMI),
    # Operation
    "language": _sel(
        "english",
        "dutch",
        "french",
        "italian",
        "german",
        "spanish",
        "portuguese",
        "turkish",
        "polish",
        "russian",
        "swedish",
        "norwegian",
        "japanese",
        "chinese_s",
        "chinese_t",
        "korean",
        "thai",
        "arabic",
    ),
    "menu_pos": _sel("bottom_left", "center"),
    "status_disp": _sel(*_ON_OFF),
    "ir_receiver": _sel("front_rear", "front", "rear"),
    "lens_lock": _sel(*_ON_OFF),
    # Connection / power
    "powsave_nosig": _sel("standby", "off"),
    "dynamic_range": _sel("auto", "limited", "full", suffixes=_HDMI),
    # Installation
    "image_flip": _sel("hv", "h", "v", "off"),
    "blanking": _num(0, 100, suffixes=("top", "bottom", "left", "right")),
    "panel_align_shift_adj_r": _num(0, 100, suffixes=("h", "v")),
    "panel_align_shift_adj_b": _num(0, 100, suffixes=("h", "v")),
    "panel_align_pattern": _sel("rgb", "rg", "bg"),
    "panel_alignment": _sel(*_ON_OFF),
    "panel_align_reset": FakeCommand("menu_exec"),
    "high_alt_mode": _sel(*_ON_OFF),
    "remote_start": _sel(*_ON_OFF),
    "network_mgmt": _sel(*_ON_OFF),
    "anamorphic_lens": _sel("1.24x", "1.32x"),
    "trigger": _sel("off", "power", "v_stretch", "2.35_1_zoom", suffixes=("port1", "port2")),
    "blanking_enable": _sel(*_ON_OFF),
}

_VW290ES_KEY_CODES: frozenset[str] = frozenset(
    {
        "power_on",
        "power_off",
        "power",
        "input_a",
        "input_b",
        "input",
        "blank",
        "menu",
        "right",
        "left",
        "up",
        "down",
        "enter",
        "reset",
        "picmode1",
        "picmode2",
        "picmode3",
        "picmode4",
        "picmode5",
        "picmode6",
        "picmode7",
        "picmode8",
        "picmode9",
        "picmode",
        "picture+",
        "picture-",
        "color+",
        "color-",
        "bright+",
        "bright-",
        "hue+",
        "hue-",
        "sharpness+",
        "sharpness-",
        "color_temp",
        "color_mode",
        "black_level",
        "reality_creation",
        "motionflow",
        "gamma_correction",
        "color_correction",
        "aspect",
        "aspect_normal",
        "aspect_v_stretch",
        "aspect_1.85_1_zoom",
        "aspect_2.35_1_zoom",
        "aspect_stretch",
        "aspect_squeeze",
        "status_on",
        "status_off",
        "lens_control",
        "lens_focus",
        "lens_focus_far",
        "lens_focus_near",
        "lens_zoom",
        "lens_zoom_up",
        "lens_zoom_down",
        "lens_shift",
        "lens_shift_up",
        "lens_shift_down",
        "lens_shift_left",
        "lens_shift_right",
        "3d",
        "2d_3d_display_select",
        "2d_3d_display_select_auto",
        "2d_3d_display_select_3d",
        "3d_format",
        "3d_format_simulated_3d",
        "3d_format_side_by_side",
        "3d_format_over_under",
        "simulated_3d_effect",
        "simulated_3d_effect_high",
        "simulated_3d_effect_middle",
        "simulated_3d_effect_low",
        "3d_depth+",
        "3d_depth-",
    }
)

VW290ES = ModelProfile(
    modelname="VPL-VW295ES",
    serialnum="1234567",
    mac_address="08-00-46-12-34-56",
    commands={**_SYSTEM_COMMANDS, **_VW290ES_MENU_COMMANDS},
    key_codes=_VW290ES_KEY_CODES,
    version=[{"main": "1.000"}],
)
"""The VPL-VW290ES / VW270ES column of Sony's Supported Command List."""

MINIMAL = ModelProfile(
    modelname="VPL-FAKE",
    serialnum="0000001",
    mac_address="08-00-46-00-00-01",
    commands={
        "power": _SYSTEM_COMMANDS["power"],
        "power_status": _SYSTEM_COMMANDS["power_status"],
        "modelname": _SYSTEM_COMMANDS["modelname"],
        "serialnum": _SYSTEM_COMMANDS["serialnum"],
        "input": _sel(*_HDMI),
        "contrast": _num(0, 100),
    },
    key_codes=frozenset({"power_on", "power_off", "menu"}),
    version=[{"main": "0.100"}],
)
"""A tiny profile that shows the fake is not tied to one model."""


# -- request parsing -----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Request:
    name: str
    suffix: str | None
    form: Form
    value: str | int | float | None = None


_NAME_RE = re.compile(r"^[A-Za-z0-9_.]+$")
_SUFFIX_RE = re.compile(r"^--([A-Za-z0-9_]+)$")
_QUOTED_RE = re.compile(r'^"([^"]*)"$')
_INT_RE = re.compile(r"^-?\d+$")
_NUMBER_RE = re.compile(r"^-?\d+(\.\d+)?$")
_RESERVED_OPTIONS = frozenset({"--rel", "--reset", "--range", "--info"})


def _parse(line: str) -> _Request | None:
    """Split a command line into its parts. ``None`` means the line is malformed."""
    tokens = line.split(" ")
    name = tokens[0]
    if not _NAME_RE.match(name):
        return None
    rest = tokens[1:]
    suffix: str | None = None
    if rest and rest[0].startswith("--") and rest[0] not in _RESERVED_OPTIONS:
        match = _SUFFIX_RE.match(rest[0])
        if match is None:
            return None
        suffix = match.group(1)
        rest = rest[1:]
    if not rest:
        return _Request(name, suffix, "exec")
    if rest == ["--reset"]:
        return _Request(name, suffix, "reset")
    if rest == ["?"]:
        return _Request(name, suffix, "query")
    if rest == ["?", "--range"]:
        return _Request(name, suffix, "range")
    if rest == ["?", "--info"]:
        return _Request(name, suffix, "info")
    if len(rest) == 2 and rest[0] == "--rel" and _INT_RE.match(rest[1]):
        return _Request(name, suffix, "rel", int(rest[1]))
    if len(rest) == 1 and _NUMBER_RE.match(rest[0]):
        return _Request(name, suffix, "set", _number(rest[0]))
    quoted = _QUOTED_RE.match(" ".join(rest))
    if quoted is not None:
        return _Request(name, suffix, "set", quoted.group(1))
    return None


def _number(token: str) -> int | float:
    """Parse a bare number; a decimal with nothing after the point collapses to an int."""
    if "." not in token:
        return int(token)
    value = float(token)
    return int(value) if value.is_integer() else value


_ALLOWED_FORMS: dict[str, frozenset[str]] = {
    "sys_sel": frozenset({"set", "query", "range", "info"}),
    "sys_stat": frozenset({"query", "info"}),
    # The manual documents no "? --info" form for sys_var, but a VW295ES answers
    # one with string bounds at the top level, so the fake does too.
    "sys_var": frozenset({"set", "query", "range", "info"}),
    "menu_sel": frozenset({"set", "reset", "query", "range", "info"}),
    "menu_num": frozenset({"set", "rel", "reset", "query", "range", "info"}),
    "menu_exec": frozenset({"exec", "info"}),
}

_SYS_STAT_DEFAULTS: dict[str, object] = {
    "power_status": "standby",
    "error": ["no_err"],
    "warning": ["no_warn"],
    "timer": [{"operation": 3400}, {"light_src": 2300}, {"prev_light_src": 3000}],
    "signal": "3840x2160/60p",
    "ipv6_ip_address": "fe80::a00:46ff:fe12:3456",
    "ipv6_default_gateway": "fe80::1",
    "ipv6_prefix": 64,
    "temperature": 30.0,
    "3d_status": "2d",
    "filter_status": "normal",
    "panel_resolution": [1920, 1080],
    "licenses": [],
}


def _dumps(value: object) -> str:
    return json.dumps(value, separators=(",", ":"))


# -- the server ----------------------------------------------------------------


class FakeProjector:
    """A fake ADCP projector listening on a local TCP port.

    Args:
        password: Password for the handshake, or ``None`` to answer ``NOKEY``.
        model: Which commands, values and key codes the projector supports.
        idle_timeout: Seconds of silence after which the projector closes the
            session (its "connection time-out"). ``None`` never times out.
        wake_behaviour: How the projector treats the first command after
            waking from standby: ``"normal"`` answers it, ``"silent"``
            swallows it, ``"late"`` answers it after ``wake_delay`` seconds,
            ``"err_cmd"`` answers ``err_cmd`` whatever it was. The quirk fires
            ``wake_count`` times in total (once by default), regardless of
            how many TCP connections are involved, like a real unit waking
            once.
        wake_delay: Seconds the ``"late"`` behaviour holds the reply back.
        wake_count: How many commands the wake quirk applies to.
        power_gating: When true, ``menu_*`` commands answer ``err_inactive``
            unless the power status is ``on``.
        info_gated: When true, ``? --info`` on ``menu_*`` commands is also
            gated by power (the manual does not say either way).
        greeting_override: Line sent instead of ``NOKEY`` or the random
            nonce. ``"err_auth"`` ends the session at once (a host that the
            allow-list rejects), ``"NOKEY"`` skips authentication and
            anything else is used as the nonce.
        handshake_verdict_override: Line sent instead of ``OK`` after a
            correct password answer. The session only continues when it is
            ``OK``.
        host: Address to bind.
        port: Port to bind; ``0`` picks a free one, see :attr:`port`.
    """

    def __init__(
        self,
        password: str | None = "Projector",
        *,
        model: ModelProfile = VW290ES,
        idle_timeout: float | None = None,
        wake_behaviour: WakeBehaviour = "normal",
        wake_delay: float = 0.0,
        wake_count: int = 1,
        power_gating: bool = True,
        info_gated: bool = False,
        greeting_override: str | None = None,
        handshake_verdict_override: str | None = None,
        host: str = "127.0.0.1",
        port: int = 0,
    ) -> None:
        self.password = password
        self.model = model
        self.idle_timeout = idle_timeout
        self.wake_behaviour: WakeBehaviour = wake_behaviour
        self.wake_delay = wake_delay
        self.wake_count = wake_count
        self.power_gating = power_gating
        self.info_gated = info_gated
        self.greeting_override = greeting_override
        self.handshake_verdict_override = handshake_verdict_override
        self.reply_delay: dict[str, float] = {}
        """Seconds to hold back the reply to a given command line."""
        self.drop_after: set[str] = set()
        """Command lines the fake answers by closing the session instead, before
        acting on them. Each entry fires once and is then removed."""
        self.host = host
        self.port = port
        self.state: dict[str, object] = {}
        self.received: list[str] = []
        self.keys_pressed: list[str] = []
        self.connections = 0
        self.reply_overrides: dict[str, str] = {}
        self._server: asyncio.Server | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._writers: set[asyncio.StreamWriter] = set()
        self.reset_state()

    # -- state ---------------------------------------------------------------

    def reset_state(self) -> None:
        """Put every setting back to its default and the projector into standby."""
        self.state.clear()
        for name, cmd in self.model.commands.items():
            if cmd.type == "menu_exec":
                continue
            if cmd.type == "sys_stat":
                self.state[name] = self._sys_stat_default(name, cmd)
            elif cmd.suffixes is not None:
                self.state[name] = {s: self._default_for(cmd, s) for s in cmd.suffixes}
            else:
                self.state[name] = self._default_for(cmd, None)
        self.state["power_status"] = "standby"

    def _sys_stat_default(self, name: str, cmd: FakeCommand) -> object:
        if cmd.default is not None:
            return cmd.default
        match name:
            case "modelname":
                return self.model.modelname
            case "serialnum":
                return self.model.serialnum
            case "mac_address":
                return self.model.mac_address
            case "version":
                return self.model.version
            case _:
                return _SYS_STAT_DEFAULTS.get(name, "")

    @staticmethod
    def _default_for(cmd: FakeCommand, suffix: str | None) -> object:
        default = cmd.default
        if suffix is not None and isinstance(default, Mapping):
            default = default.get(suffix)
        if default is not None:
            return default
        if cmd.options is not None:
            return cmd.options[0]
        if cmd.range is not None:
            lo, hi, _step = cmd.range
            return 0 if lo <= 0 <= hi else lo
        return ""

    @property
    def power_status(self) -> str:
        """Current power status: ``standby``, ``startup``, ``on``, ``cooling1`` or ``cooling2``."""
        status = self.state["power_status"]
        assert isinstance(status, str)
        return status

    @power_status.setter
    def power_status(self, value: str) -> None:
        if value not in POWER_STATUSES:
            raise ValueError(f"not a power status: {value!r}")
        self.state["power_status"] = value

    def _get(self, name: str, suffix: str | None) -> object:
        value = self.state[name]
        if suffix is not None:
            assert isinstance(value, dict)
            return value[suffix]
        return value

    def _set(self, name: str, suffix: str | None, value: object) -> None:
        if suffix is None:
            self.state[name] = value
            return
        per_suffix = self.state[name]
        assert isinstance(per_suffix, dict)
        per_suffix[suffix] = value

    # -- lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        """Bind the listening socket. :attr:`port` is valid afterwards."""
        if self._server is not None:
            raise RuntimeError("already started")
        self._server = await asyncio.start_server(
            self._on_connect, self.host, self.port, limit=4096
        )
        sockname = self._server.sockets[0].getsockname()
        self.port = int(sockname[1])

    async def stop(self) -> None:
        """Close the listener and every client session."""
        server = self._server
        if server is None:
            return
        self._server = None
        server.close()
        for writer in list(self._writers):
            writer.close()
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        with contextlib.suppress(OSError):
            await server.wait_closed()

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.stop()

    # -- connection handling -------------------------------------------------

    def _on_connect(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.create_task(self._serve(reader, writer))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.connections += 1
        self._writers.add(writer)
        try:
            if not await self._handshake(reader, writer):
                return
            while True:
                line = await self._readline(reader)
                if line is None:
                    return
                self.received.append(line)
                if line in self.drop_after:
                    self.drop_after.discard(line)
                    return
                waking = self.wake_behaviour != "normal" and self.wake_count > 0
                if waking:
                    self.wake_count -= 1
                if waking and self.wake_behaviour == "silent":
                    continue
                if waking and self.wake_behaviour == "late":
                    await asyncio.sleep(self.wake_delay)
                delay = self.reply_delay.get(line)
                if delay:
                    await asyncio.sleep(delay)
                if waking and self.wake_behaviour == "err_cmd":
                    reply = "err_cmd"
                else:
                    reply = self._reply(line)
                await self._send(writer, reply)
        except (OSError, asyncio.IncompleteReadError):
            return
        finally:
            self._writers.discard(writer)
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()

    async def _handshake(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> bool:
        greeting = self.greeting_override
        if greeting is None:
            greeting = "NOKEY" if self.password is None else secrets.token_hex(4)
        await self._send(writer, greeting)
        if greeting == "NOKEY":
            return True
        if greeting == "err_auth":
            return False
        password = self.password or ""
        expected = hashlib.sha256((greeting + password).encode("ascii")).hexdigest()
        answer = await self._readline(reader)
        if answer != expected:
            await self._send(writer, "err_auth")
            return False
        verdict = self.handshake_verdict_override
        if verdict is None:
            verdict = "OK"
        await self._send(writer, verdict)
        return verdict == "OK"

    async def _readline(self, reader: asyncio.StreamReader) -> str | None:
        """Read one line without its terminator. ``None`` means the session is over."""
        try:
            if self.idle_timeout is None:
                raw = await reader.readline()
            else:
                async with asyncio.timeout(self.idle_timeout):
                    raw = await reader.readline()
        except TimeoutError:
            return None
        except ValueError:
            # Line longer than the stream limit: the projector would discard it.
            return ""
        if not raw.endswith(b"\n"):
            return None
        return raw.rstrip(b"\r\n").decode("ascii", errors="replace")

    @staticmethod
    async def _send(writer: asyncio.StreamWriter, line: str) -> None:
        writer.write(line.encode("ascii") + LINE_END)
        await writer.drain()

    # -- command evaluation --------------------------------------------------

    def _reply(self, line: str) -> str:
        """Compute the reply line for one received command line."""
        if line in self.reply_overrides:
            return self.reply_overrides[line]
        request = _parse(line)
        if request is None:
            return "err_cmd"
        if request.name == "key":
            return self._key(request)
        cmd = self.model.commands.get(request.name)
        if cmd is None:
            return "err_cmd"
        if cmd.type == "sys_stat" and request.suffix is not None:
            return self._sys_stat_field(request, cmd)
        if cmd.suffixes is None:
            if request.suffix is not None:
                return "err_option"
        elif request.suffix is None or request.suffix not in cmd.suffixes:
            # A VW295ES treats a missing or unknown suffix as an unknown
            # command (err_cmd), not as a bad option.
            return "err_cmd"
        if request.form not in _ALLOWED_FORMS[cmd.type]:
            # A VW295ES answers err_option to a form the type lacks ("timer ?
            # --range"), except that querying a set-only sys_sel is err_cmd.
            return "err_cmd" if cmd.type == "sys_sel" and request.form == "query" else "err_option"
        if request.name == "power" and request.form == "query":
            return "err_cmd"
        gated = cmd.type.startswith("menu_") and self.power_gating and self.power_status != "on"
        if request.form == "info":
            return "err_inactive" if gated and self.info_gated else self._info(request.name, cmd)
        if gated:
            return "err_inactive"
        match request.form:
            case "range":
                return self._range(cmd)
            case "query":
                return self._query(request, cmd)
            case "exec":
                return "ok"
            case "reset":
                self._set(request.name, request.suffix, self._default_for(cmd, request.suffix))
                return "ok"
            case "rel":
                return self._rel(request, cmd)
            case _:
                return self._assign(request, cmd)

    def _key(self, request: _Request) -> str:
        """``key`` is set-only; a VW295ES describes it with type ``ir_remote``."""
        if request.suffix is not None:
            return "err_option"
        if request.form == "info":
            info = {"type": "ir_remote", "version": "1.0", "range": sorted(self.model.key_codes)}
            return _dumps(info)
        if request.form != "set" or not isinstance(request.value, str):
            return "err_option"
        if request.value not in self.model.key_codes:
            return "err_option"
        self.keys_pressed.append(request.value)
        return "ok"

    def _sys_stat_field(self, request: _Request, cmd: FakeCommand) -> str:
        """``timer --operation ?`` returns the one named element of the array."""
        fields = self._sys_stat_fields(request.name)
        if request.form != "query" or request.suffix not in fields:
            return "err_cmd"
        value = self.state[request.name]
        assert isinstance(value, list)
        return _dumps(next(item for item in value if request.suffix in item))

    def _sys_stat_fields(self, name: str) -> list[str]:
        """Keys of a status whose value is an array of single-key objects."""
        value = self.state.get(name)
        if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
            return []
        return [key for item in value for key in item]

    @staticmethod
    def _range_value(cmd: FakeCommand) -> object:
        if cmd.options is not None:
            return list(cmd.options)
        if cmd.range is not None:
            lo, hi, step = cmd.range
            bounds: dict[str, int] = {"min": lo, "max": hi}
            if step != 1:
                bounds["step"] = step
            return bounds
        if cmd.type == "sys_var":
            return {"min": "1.0.0.0", "max": "223.255.255.255"}
        return None

    def _info(self, name: str, cmd: FakeCommand) -> str:
        info: dict[str, object] = {"type": cmd.type, "version": "1.0"}
        if cmd.type in ("sys_sel", "menu_sel", "menu_num"):
            info["range"] = self._range_value(cmd)
        elif cmd.type == "sys_var":
            # String bounds at the top level, as a VW295ES reports them.
            bounds = self._range_value(cmd)
            assert isinstance(bounds, dict)
            info.update(bounds)
        elif cmd.type == "sys_stat" and (fields := self._sys_stat_fields(name)):
            info["range"] = [f"--{field}" for field in fields]
        return _dumps(info)

    def _range(self, cmd: FakeCommand) -> str:
        return _dumps(self._range_value(cmd))

    def _query(self, request: _Request, cmd: FakeCommand) -> str:
        if cmd.type == "sys_stat":
            if request.name == "signal" and self.power_status != "on":
                return _dumps("Invalid")
            return _dumps(self.state[request.name])
        return _dumps(self._get(request.name, request.suffix))

    def _rel(self, request: _Request, cmd: FakeCommand) -> str:
        assert cmd.range is not None
        assert isinstance(request.value, int)
        current = self._get(request.name, request.suffix)
        assert isinstance(current, int)
        target = current + request.value
        if not self._in_range(cmd.range, target):
            return "err_val"
        self._set(request.name, request.suffix, target)
        return "ok"

    def _assign(self, request: _Request, cmd: FakeCommand) -> str:
        value = request.value
        if cmd.options is not None:
            # A VW295ES answers err_option, not err_val, to a choice it does
            # not know; err_val is for numbers outside their range.
            if not isinstance(value, str) or value not in cmd.options:
                return "err_option"
        elif cmd.range is not None:
            if not isinstance(value, int | float) or not self._in_range(cmd.range, value):
                return "err_val"
        elif not isinstance(value, str):
            return "err_val"
        self._set(request.name, request.suffix, value)
        if request.name == "power":
            self.power_status = "on" if value == "on" else "standby"
        return "ok"

    @staticmethod
    def _in_range(bounds: tuple[int, int, int], value: float) -> bool:
        """Whether ``value`` lies within ``bounds`` and sits on the step grid."""
        lo, hi, step = bounds
        return lo <= value <= hi and (value - lo) % step == 0
