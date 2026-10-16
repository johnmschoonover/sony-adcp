"""Declarative table of ADCP commands.

Every command Sony documents in the "Supported Command List" manual's system,
menu and remote-key sections is listed here once, with the kind of value it
takes and the ``--suffix`` tokens it accepts. The table is deliberately not
per model: whether a given projector actually supports a command, a value or
a suffix is something the projector itself reports (``err_cmd``,
``err_option``, ``? --info``), so nothing here claims to know that.

A :class:`Command` renders the wire form of every request the protocol
defines (set, relative set, reset, execute and the three queries) and refuses
forms that its kind does not allow, so a caller cannot build a line the
projector would reject on syntax alone. The returned strings carry no CR LF;
the connection adds the terminator.

The expert-only command types (``panel_align_zone``, ``user_gamma``,
``pattern_sel`` / ``pattern_pos``) have their own option syntax and are not
modelled.
"""

from __future__ import annotations

import difflib
import math
import re
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING

from pyadcp.exceptions import AdcpError

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

__all__ = [
    "COMMANDS",
    "INFO_TYPE_KINDS",
    "KEY_CODES",
    "Command",
    "CommandFormError",
    "CommandKind",
    "UnknownCommandError",
    "get_command",
]


class CommandKind(StrEnum):
    """How a command carries its value, which decides the forms it supports.

    * ``SELECT``: the value is a quoted string (``sys_sel``, ``sys_var``,
      ``menu_sel`` and the ``key`` command).
    * ``NUMBER``: the value is a bare number (``menu_num``); also supports
      ``--rel``.
    * ``EXEC``: an action with no value (``menu_exec``).
    * ``STATUS``: read-only (``sys_stat``); query and info only.
    """

    SELECT = "select"
    NUMBER = "number"
    EXEC = "exec"
    STATUS = "status"


class CommandFormError(AdcpError, ValueError):
    """A request form, value or suffix is not valid for this command.

    Raised before anything is sent: the kind does not support the form, a
    required suffix is missing or unknown, or the value cannot be rendered.
    """


_NAME_RE = re.compile(r"[a-z0-9][a-z0-9_]*")
"""Wire names: lowercase ASCII letters, digits and underscores, no dots.

A leading digit is allowed because the manual has ``3d_status``, ``3d_format``
and friends.
"""


def _check_identifier(what: str, value: str) -> None:
    if not _NAME_RE.fullmatch(value):
        raise ValueError(f"{what} {value!r} is not a plain lowercase identifier")


def _check_text(name: str, value: str) -> None:
    """Reject strings that cannot be quoted verbatim on the wire."""
    for ch in value:
        code = ord(ch)
        if ch in '"\\' or code < 0x20 or code > 0x7E:
            raise CommandFormError(
                f"{name}: value {value!r} contains a quote, backslash, "
                "control or non-ASCII character"
            )


def _render_number(name: str, value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise CommandFormError(f"{name} takes an int or float value, not {type(value).__name__}")
    if isinstance(value, float) and not math.isfinite(value):
        raise CommandFormError(f"{name}: value must be finite, not {value!r}")
    return str(value)


@dataclass(frozen=True, slots=True)
class Command:
    """One ADCP command and the request lines it can produce.

    Attributes:
        name: The wire name, exactly as the projector expects it.
        kind: The :class:`CommandKind` that decides which forms are legal.
        suffixes: The ``--suffix`` tokens the command addresses (without the
            dashes). When non-empty, every form except :meth:`execute`
            requires one of them.
        description: A short phrase saying what the command does.
    """

    name: str
    kind: CommandKind
    suffixes: tuple[str, ...] = ()
    description: str = ""

    def __post_init__(self) -> None:
        _check_identifier("command name", self.name)
        for suffix in self.suffixes:
            _check_identifier(f"suffix of {self.name}", suffix)
        if len(set(self.suffixes)) != len(self.suffixes):
            raise ValueError(f"duplicate suffix in {self.name}: {self.suffixes!r}")

    # -- helpers -----------------------------------------------------------

    def _head(self, suffix: str | None) -> list[str]:
        """Return the leading tokens: the name and, when applicable, the suffix."""
        if self.suffixes:
            if suffix is None:
                raise CommandFormError(
                    f"{self.name} requires a suffix, one of {', '.join(self.suffixes)}"
                )
            if suffix not in self.suffixes:
                raise CommandFormError(
                    f"{self.name} has no suffix {suffix!r}; expected one of "
                    f"{', '.join(self.suffixes)}"
                )
            return [self.name, f"--{suffix}"]
        if suffix is not None:
            raise CommandFormError(f"{self.name} takes no suffix (got {suffix!r})")
        return [self.name]

    def _require(self, form: str, *allowed: CommandKind) -> None:
        if self.kind not in allowed:
            raise CommandFormError(
                f"{self.name} is a {self.kind.value} command and does not support {form}"
            )

    # -- request forms -----------------------------------------------------

    def set(self, value: str | int | float, suffix: str | None = None) -> str:
        """Render ``name "value"`` (SELECT) or ``name 42`` (NUMBER)."""
        self._require("set", CommandKind.SELECT, CommandKind.NUMBER)
        if self.kind is CommandKind.SELECT:
            if not isinstance(value, str):
                raise CommandFormError(
                    f"{self.name} takes a string value, not {type(value).__name__}"
                )
            _check_text(self.name, value)
            rendered = f'"{value}"'
        else:
            rendered = _render_number(self.name, value)
        return " ".join([*self._head(suffix), rendered])

    def set_relative(self, delta: int, suffix: str | None = None) -> str:
        """Render ``name --rel delta``; NUMBER commands only."""
        self._require("relative set", CommandKind.NUMBER)
        if isinstance(delta, bool) or not isinstance(delta, int):
            raise CommandFormError(f"{self.name}: --rel takes an int, not {type(delta).__name__}")
        return " ".join([*self._head(suffix), "--rel", str(delta)])

    def reset(self, suffix: str | None = None) -> str:
        """Render ``name --reset``; SELECT and NUMBER commands only."""
        self._require("reset", CommandKind.SELECT, CommandKind.NUMBER)
        return " ".join([*self._head(suffix), "--reset"])

    def execute(self) -> str:
        """Render the bare ``name``; EXEC commands only."""
        self._require("execute", CommandKind.EXEC)
        return self.name

    def query(self, suffix: str | None = None) -> str:
        """Render ``name ?``; every kind except EXEC."""
        self._require("query", CommandKind.SELECT, CommandKind.NUMBER, CommandKind.STATUS)
        return " ".join([*self._head(suffix), "?"])

    def query_range(self, suffix: str | None = None) -> str:
        """Render ``name ? --range``; SELECT and NUMBER commands only."""
        self._require("range query", CommandKind.SELECT, CommandKind.NUMBER)
        return " ".join([*self._head(suffix), "?", "--range"])

    def query_info(self, suffix: str | None = None) -> str:
        """Render ``name ? --info``; every kind."""
        return " ".join([*self._head(suffix), "?", "--info"])


INFO_TYPE_KINDS: Mapping[str, CommandKind] = MappingProxyType(
    {
        "sys_sel": CommandKind.SELECT,
        "sys_stat": CommandKind.STATUS,
        "sys_var": CommandKind.SELECT,
        "menu_sel": CommandKind.SELECT,
        "menu_num": CommandKind.NUMBER,
        "menu_exec": CommandKind.EXEC,
        "key": CommandKind.SELECT,
    }
)
"""Maps the ``type`` reported by ``? --info`` to the :class:`CommandKind` it implies."""


# --------------------------------------------------------------------------
# Suffix groups named in the manual's remarks columns.
# --------------------------------------------------------------------------

_HDMI = ("hdmi1", "hdmi2", "hdmi3", "hdmi4")
_DISPLAYPORT = ("dp1", "dp2", "dp3", "dp4", "dp1_2", "dp1_2_3_4")
_EDGES = ("top", "bottom", "left", "right")
_TRIGGER_PORTS = ("port1", "port2")
_SIX_COLOURS = ("r", "g", "b", "c", "y", "m")
_PRIMARIES = ("r", "g", "b")
_AXES = ("h", "v")
_LEVELS = ("lev1", "lev2", "lev3", "lev4", "lev5", "lev6")
_CORNERS = ("top_left", "top_right", "bottom_left", "bottom_right")
_MARKERS = ("start", "end")


def _sel(name: str, description: str, suffixes: tuple[str, ...] = ()) -> Command:
    return Command(name, CommandKind.SELECT, suffixes, description)


def _num(name: str, description: str, suffixes: tuple[str, ...] = ()) -> Command:
    return Command(name, CommandKind.NUMBER, suffixes, description)


def _exec(name: str, description: str) -> Command:
    return Command(name, CommandKind.EXEC, (), description)


def _stat(name: str, description: str) -> Command:
    return Command(name, CommandKind.STATUS, (), description)


_TABLE: tuple[Command, ...] = (
    # ---- System commands: sys_sel ------------------------------------------
    _sel("power", "turn the projector on or off"),
    _sel("ipv4_network_setting", "begin or apply a change to the IPv4 settings"),
    _sel("ipv4_set_method", "IPv4 address assignment, automatic or manual"),
    _sel("complete_maintenance", "acknowledge a lamp or filter replacement"),
    # ---- System commands: sys_stat -----------------------------------------
    _stat("power_status", "current power state"),
    _stat("error", "active error tokens"),
    _stat("warning", "active warning tokens"),
    _stat("timer", "operating and light-source hour counters"),
    _stat("filter_status", "whether the air filter needs cleaning or replacing"),
    _stat("modelname", "model name"),
    _stat("serialnum", "serial number"),
    _stat("signal", "format of the current input signal"),
    _stat("version", "firmware versions"),
    _stat("mac_address", "MAC address of the network port"),
    _stat("licenses", "names of the activated licences"),
    _stat("panel_resolution", "native resolution of one display panel"),
    _stat("ipv6_ip_address", "IPv6 address"),
    _stat("ipv6_default_gateway", "IPv6 default gateway"),
    _stat("ipv6_prefix", "IPv6 prefix length"),
    _stat("temperature", "internal temperature"),
    _stat("3d_status", "whether a 2D or a 3D picture is being shown"),
    # ---- System commands: sys_var ------------------------------------------
    _sel("ipv4_ip_address", "IPv4 address"),
    _sel("ipv4_sub_net_mask", "IPv4 subnet mask"),
    _sel("ipv4_default_gateway", "IPv4 default gateway"),
    # ---- Menu commands: remote control function ----------------------------
    _sel("input", "input terminal"),
    _sel("blank", "video mute"),
    _sel("muting", "audio mute"),
    # ---- Menu commands: image quality --------------------------------------
    _sel("picture_mode", "picture preset"),
    _exec("picture_mode_reset", "restore the defaults of the current picture preset"),
    _num("contrast", "contrast"),
    _num("brightness", "brightness"),
    _num("color", "colour depth"),
    _num("hue", "hue"),
    _num("sharpness", "sharpness"),
    _sel("color_temp", "colour temperature preset"),
    _num("coltemp_gain_r", "red gain of the custom colour temperature"),
    _num("coltemp_gain_g", "green gain of the custom colour temperature"),
    _num("coltemp_gain_b", "blue gain of the custom colour temperature"),
    _num("coltemp_bias_r", "red bias of the custom colour temperature"),
    _num("coltemp_bias_g", "green bias of the custom colour temperature"),
    _num("coltemp_bias_b", "blue bias of the custom colour temperature"),
    _sel("constant_brt", "constant brightness mode"),
    _num("constant_brt_output", "light output used in constant brightness mode"),
    _num("coltemp_x", "white point x coordinate (cyan to red)"),
    _num("coltemp_y", "white point y coordinate (magenta to green)"),
    _sel("color_space", "colour space preset"),
    _num("col_space_x", "x coordinate of one primary in the custom colour space", _PRIMARIES),
    _num("col_space_y", "y coordinate of one primary in the custom colour space", _PRIMARIES),
    _sel("gamma_correction", "gamma curve"),
    _sel("film_mode", "film mode"),
    _sel("real_cre", "Reality Creation on or off"),
    _sel("real_cre_db", "Reality Creation database"),
    _num("real_cre_reso", "Reality Creation resolution"),
    _num("real_cre_noise", "Reality Creation noise filtering"),
    _sel("real_cre_dfopt", "4K HDR detail optimisation"),
    _sel("iris_dyn_cont", "dynamic iris behaviour"),
    _num("iris_brightness", "manual iris aperture"),
    _sel("light_output_dyn", "dynamic light-source control"),
    _num("light_output_val", "light output level in custom light mode"),
    _sel("motionflow", "Motionflow mode"),
    _sel("nr", "noise reduction level"),
    _sel("mnr", "MPEG noise reduction level"),
    _sel("smooth_grd", "smooth gradation level"),
    _sel("contrast_enh", "contrast enhancer level"),
    _sel("col_correction", "colour correction on or off"),
    _num("col_corr_hue", "hue of one colour correction axis", _SIX_COLOURS),
    _num("col_corr_color", "saturation of one colour correction axis", _SIX_COLOURS),
    _num("col_corr_brt", "brightness of one colour correction axis", _SIX_COLOURS),
    _sel("clear_white", "clear white level"),
    _sel("live_color_enh", "live colour enhancer level"),
    _sel("xvcolor", "x.v.Color on or off"),
    _sel("input_lag_red", "input lag reduction"),
    _sel("lamp_control", "lamp power level"),
    _sel("hdr", "HDR mode"),
    _sel("hdr_auto", "HDR format assumed when HDR is set to auto"),
    _sel("periodic_ld_calibration", "periodic laser calibration"),
    _num("light_output_coeff", "light-source output coefficient per channel"),
    _num("light_output_iris_val", "custom output for combined iris and laser control"),
    _sel("light_output_iris_dyn", "dynamic control of combined iris and laser output"),
    _sel("hdr_tone_mapping", "HDR tone mapping mode"),
    # ---- Menu commands: screen setting -------------------------------------
    _sel("aspect", "aspect ratio"),
    # The manual tags pic_pos_save and pic_pos_del as menu_exec yet lists the
    # slot names ("1.85_1", "custom1", ...) as their values, and a bare
    # menu_exec cannot carry one. They are modelled as SELECT so the slot can
    # be given: pic_pos_save "custom1".
    _sel("pic_pos_save", "store the current picture position in a named slot"),
    _sel("pic_pos_del", "delete a stored picture position"),
    _sel("pic_pos_sel", "recall a stored picture position"),
    _num("image_shift", "picture shift along one axis", _AXES),
    _sel("image_shift_enable", "picture shift on or off"),
    # ---- Menu commands: function setting -----------------------------------
    _sel("illumination", "indicator lighting"),
    _sel("2d3d_sel", "2D or 3D display selection"),
    _sel("3d_format", "3D signal format"),
    _sel("3d_brt", "3D picture brightness"),
    _sel("3d_glass_brt", "3D glasses brightness"),
    _num("3d_depth", "3D depth"),
    _sel("sim3d_effect", "simulated 3D strength"),
    _sel("test_pattern", "test pattern shown during adjustment"),
    _sel("setting_lock", "menu setting lock level"),
    _sel("infrared", "night-vision infrared mode"),
    _num("infrared_output", "infrared output level"),
    _sel("dimming_filter", "neutral density filter"),
    _num("infrared_light_bank", "infrared laser bank"),
    _sel("hdmi_signal_format", "signal format of one HDMI input", _HDMI),
    _sel("stream_format", "stream format of one DisplayPort input", _DISPLAYPORT),
    # ---- Menu commands: operation setting ----------------------------------
    _sel("language", "menu language"),
    _sel("menu_pos", "menu position on screen"),
    _sel("status_disp", "on-screen status display"),
    _sel("ir_receiver", "which remote control sensor is active"),
    _sel("remote_id", "remote control ID"),
    _sel("lens_lock", "lens control lock"),
    _sel("web_control_ui", "web control page on or off"),
    # ---- Menu commands: connection and power setting -----------------------
    _sel("powsave_nosig", "automatic standby when no signal is present"),
    _sel("dynamic_range", "dynamic range of one HDMI input", _HDMI),
    # ---- Menu commands: installation setting -------------------------------
    _sel("image_flip", "image flip"),
    _num("blanking", "blanking of one screen edge", _EDGES),
    _num("color_matching_brt", "colour matching brightness at one level", _LEVELS),
    _num("color_matching_r", "colour matching red at one level", _LEVELS),
    _num("color_matching_b", "colour matching blue at one level", _LEVELS),
    _exec("color_matching_reset", "reset all colour matching adjustments"),
    _num("panel_align_shift_adj_r", "red panel shift along one axis", _AXES),
    _num("panel_align_shift_adj_b", "blue panel shift along one axis", _AXES),
    _sel("panel_align_pattern", "pattern colour used while aligning panels"),
    _sel("panel_alignment", "panel alignment on or off"),
    _exec("panel_align_reset", "reset all panel alignment adjustments"),
    _sel("high_alt_mode", "high altitude mode"),
    _sel("blend_sw", "edge blending of one edge", _EDGES),
    _sel("blend_gamma", "blending gamma curve of one edge", _EDGES),
    _num("blend_start", "blending start position of one edge", _EDGES),
    _num("blend_width", "blending width of one edge", _EDGES),
    _exec("blend_bk_level_reset", "reset the blending black level"),
    _exec("blend_reset", "reset all blending adjustments"),
    _sel("blend_cursor", "blending marker on or off"),
    _sel("blend_cursor_color", "colour of the start or end blending marker", _MARKERS),
    _sel("remote_start", "wake on LAN"),
    _sel("network_mgmt", "network management"),
    _sel("anamorphic_lens", "anamorphic lens ratio"),
    _sel("trigger", "function of one trigger output", _TRIGGER_PORTS),
    _sel("blanking_enable", "blanking on or off"),
    _exec("activate_licenses", "activate the installed licences"),
    _num("corner_keystone_h", "horizontal corner keystone of one corner", _CORNERS),
    _num("corner_keystone_v", "vertical corner keystone of one corner", _CORNERS),
    _exec("screen_fitting_reset", "reset the corner keystone adjustments"),
    # ---- Remote controller key command -------------------------------------
    _sel("key", "press a remote control key"),
)


def _build_table(commands: Iterable[Command]) -> Mapping[str, Command]:
    table: dict[str, Command] = {}
    for command in commands:
        if command.name in table:
            raise ValueError(f"duplicate command {command.name!r}")
        table[command.name] = command
    return MappingProxyType(table)


COMMANDS: Mapping[str, Command] = _build_table(_TABLE)
"""Every modelled command keyed by its wire name; read-only."""


class UnknownCommandError(AdcpError, KeyError):
    """Raised by :func:`get_command` for a name the table does not model."""

    def __str__(self) -> str:
        # KeyError quotes its argument; keep the plain message.
        return str(self.args[0]) if self.args else ""


def get_command(name: str) -> Command:
    """Return the :class:`Command` called ``name``.

    Raises:
        UnknownCommandError: If no such command is modelled (also a
            ``KeyError``). The message names close matches when there are
            any.
    """
    try:
        return COMMANDS[name]
    except KeyError:
        hint = ""
        close = difflib.get_close_matches(name, COMMANDS, n=3)
        if close:
            hint = f"; did you mean {', '.join(close)}?"
        raise UnknownCommandError(f"unknown ADCP command {name!r}{hint}") from None


# --------------------------------------------------------------------------
# Remote control key codes, the values accepted by ``key "<code>"``.
# --------------------------------------------------------------------------

KEY_CODES: frozenset[str] = frozenset(
    (
        # power, inputs, muting, navigation, picture presets
        "power_on",
        "power_off",
        "power",
        "video",
        "input_a",
        "input_b",
        "input_c",
        "input_d",
        "input_e",
        "input_f",
        "input",
        "blank",
        "muting",
        "vol+",
        "vol-",
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
        # picture adjustments and toggles
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
        "laser_brightness+",
        "laser_brightness-",
        "iris_mode",
        "motionflow",
        "black_insertion",
        "noise_reduction",
        "gamma_correction",
        "color_correction",
        # aspect ratio
        "aspect",
        "aspect_widezoom",
        "aspect_full1",
        "aspect_full2",
        "aspect_normal",
        "aspect_full",
        "aspect_zoom",
        "aspect_v_stretch",
        "aspect_1.85_1_zoom",
        "aspect_2.35_1_zoom",
        "aspect_stretch",
        "aspect_squeeze",
        "aspect_h_stretch",
        "aspect_ratio_scaling",
        "video_size",
        "video_shift",
        "status_on",
        "status_off",
        # lens
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
        "lens_position",
        "lens_position1",
        "lens_position2",
        "lens_position3",
        "lens_position4",
        "lens_position5",
        "lens_position6",
        "lens_position7",
        "lens_precise_adj",
        "lens_precise_adj_far",
        "lens_precise_adj_near",
        "pattern",
        # 3D
        "3d",
        "2d_3d_display_select",
        "2d_3d_display_select_auto",
        "2d_3d_display_select_3d",
        "2d_3d_display_select_2d",
        "3d_format",
        "3d_format_simulated_3d",
        "3d_format_side_by_side",
        "3d_format_over_under",
        "3d_glasses_brightness",
        "3d_glasses_brightness_high",
        "3d_glasses_brightness_middle",
        "3d_glasses_brightness_low",
        "3d_glass_brightness+",
        "3d_glass_brightness-",
        "3d_brightness",
        "3d_brightness_high",
        "3d_brightness_standard",
        "simulated_3d_effect",
        "simulated_3d_effect_high",
        "simulated_3d_effect_middle",
        "simulated_3d_effect_low",
        "3d_depth+",
        "3d_depth-",
    )
)
"""Every key code in the manual's key code list, across all models."""
