"""Tests for the declarative ADCP command table."""

from __future__ import annotations

import pytest

from pyadcp.commands import (
    COMMANDS,
    INFO_TYPE_KINDS,
    KEY_CODES,
    Command,
    CommandFormError,
    CommandKind,
    get_command,
)

SELECT = CommandKind.SELECT
NUMBER = CommandKind.NUMBER
EXEC = CommandKind.EXEC
STATUS = CommandKind.STATUS


# -- rendering, no suffix -----------------------------------------------------


def test_select_forms() -> None:
    cmd = get_command("picture_mode")
    assert cmd.set("cinema_film1") == 'picture_mode "cinema_film1"'
    assert cmd.reset() == "picture_mode --reset"
    assert cmd.query() == "picture_mode ?"
    assert cmd.query_range() == "picture_mode ? --range"
    assert cmd.query_info() == "picture_mode ? --info"


def test_number_forms() -> None:
    cmd = get_command("contrast")
    assert cmd.set(50) == "contrast 50"
    assert cmd.set(-3) == "contrast -3"
    assert cmd.set(1.5) == "contrast 1.5"
    assert cmd.set_relative(1) == "contrast --rel 1"
    assert cmd.set_relative(-1) == "contrast --rel -1"
    assert cmd.reset() == "contrast --reset"
    assert cmd.query() == "contrast ?"
    assert cmd.query_range() == "contrast ? --range"
    assert cmd.query_info() == "contrast ? --info"


def test_exec_forms() -> None:
    cmd = get_command("picture_mode_reset")
    assert cmd.execute() == "picture_mode_reset"
    assert cmd.query_info() == "picture_mode_reset ? --info"


def test_status_forms() -> None:
    cmd = get_command("power_status")
    assert cmd.query() == "power_status ?"
    assert cmd.query_info() == "power_status ? --info"


def test_key_forms() -> None:
    cmd = get_command("key")
    assert cmd.kind is SELECT
    assert cmd.set("menu") == 'key "menu"'
    assert cmd.set("3d_depth+") == 'key "3d_depth+"'
    assert cmd.set("aspect_1.85_1_zoom") == 'key "aspect_1.85_1_zoom"'


# -- rendering, with suffix ---------------------------------------------------


def test_select_forms_with_suffix() -> None:
    cmd = get_command("dynamic_range")
    assert cmd.set("full", "hdmi1") == 'dynamic_range --hdmi1 "full"'
    assert cmd.reset("hdmi2") == "dynamic_range --hdmi2 --reset"
    assert cmd.query("hdmi3") == "dynamic_range --hdmi3 ?"
    assert cmd.query_range("hdmi4") == "dynamic_range --hdmi4 ? --range"
    assert cmd.query_info("hdmi1") == "dynamic_range --hdmi1 ? --info"


def test_number_forms_with_suffix() -> None:
    cmd = get_command("blanking")
    assert cmd.set(10, "top") == "blanking --top 10"
    assert cmd.set_relative(1, "left") == "blanking --left --rel 1"
    assert cmd.set_relative(-2, "right") == "blanking --right --rel -2"
    assert cmd.reset("bottom") == "blanking --bottom --reset"
    assert cmd.query("top") == "blanking --top ?"
    assert cmd.query_range("top") == "blanking --top ? --range"
    assert cmd.query_info("top") == "blanking --top ? --info"


# -- suffix errors ------------------------------------------------------------


@pytest.mark.parametrize(
    "form",
    ["set", "set_relative", "reset", "query", "query_range", "query_info"],
)
def test_suffix_required(form: str) -> None:
    cmd = get_command("blanking")
    args: tuple[object, ...] = (1,) if form in {"set", "set_relative"} else ()
    with pytest.raises(CommandFormError, match="requires a suffix"):
        getattr(cmd, form)(*args)


def test_suffix_unknown() -> None:
    cmd = get_command("dynamic_range")
    with pytest.raises(CommandFormError, match="no suffix 'hdmi5'"):
        cmd.set("full", "hdmi5")
    with pytest.raises(CommandFormError):
        cmd.query("top")


def test_suffix_unexpected() -> None:
    cmd = get_command("contrast")
    with pytest.raises(CommandFormError, match="takes no suffix"):
        cmd.set(1, "top")
    with pytest.raises(CommandFormError):
        get_command("picture_mode").query("hdmi1")


def test_command_form_error_is_value_error() -> None:
    with pytest.raises(ValueError, match="requires a suffix"):
        get_command("blanking").query()


# -- kind violations ----------------------------------------------------------


def test_set_on_status() -> None:
    with pytest.raises(CommandFormError, match="does not support set"):
        get_command("power_status").set("on")


def test_set_relative_on_select() -> None:
    with pytest.raises(CommandFormError, match="relative set"):
        get_command("picture_mode").set_relative(1)


def test_execute_on_number() -> None:
    with pytest.raises(CommandFormError, match="execute"):
        get_command("contrast").execute()


def test_execute_on_select_and_status() -> None:
    with pytest.raises(CommandFormError):
        get_command("power").execute()
    with pytest.raises(CommandFormError):
        get_command("power_status").execute()


def test_query_on_exec() -> None:
    with pytest.raises(CommandFormError, match="query"):
        get_command("picture_mode_reset").query()


def test_query_range_on_status_and_exec() -> None:
    with pytest.raises(CommandFormError, match="range query"):
        get_command("power_status").query_range()
    with pytest.raises(CommandFormError):
        get_command("picture_mode_reset").query_range()


def test_reset_on_status_and_exec() -> None:
    with pytest.raises(CommandFormError, match="reset"):
        get_command("power_status").reset()
    with pytest.raises(CommandFormError):
        get_command("picture_mode_reset").reset()


# -- value validation ---------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    ['say "hi"', "back\\slash", "line\nbreak", "tab\there", "café", "del\x7f"],
)
def test_select_rejects_unsafe_strings(bad: str) -> None:
    with pytest.raises(CommandFormError):
        get_command("picture_mode").set(bad)


def test_select_rejects_non_string() -> None:
    with pytest.raises(CommandFormError, match="string value"):
        get_command("picture_mode").set(1)
    with pytest.raises(CommandFormError):
        get_command("picture_mode").set(True)


def test_number_rejects_non_number() -> None:
    with pytest.raises(CommandFormError, match="int or float"):
        get_command("contrast").set("50")
    with pytest.raises(CommandFormError):
        get_command("contrast").set(True)
    with pytest.raises(CommandFormError):
        get_command("contrast").set(float("nan"))


def test_set_relative_rejects_non_int() -> None:
    with pytest.raises(CommandFormError):
        get_command("contrast").set_relative(True)
    with pytest.raises(CommandFormError):
        get_command("contrast").set_relative("1")  # type: ignore[arg-type]


def test_select_allows_ordinary_values() -> None:
    assert get_command("ipv4_ip_address").set("192.168.0.1") == 'ipv4_ip_address "192.168.0.1"'
    assert get_command("aspect").set("2.35_1_zoom") == 'aspect "2.35_1_zoom"'
    assert get_command("picture_mode").set("") == 'picture_mode ""'


# -- construction -------------------------------------------------------------


@pytest.mark.parametrize("name", ["Contrast", "con-trast", "con.trast", "con trast", "", "_x"])
def test_bad_names_rejected(name: str) -> None:
    with pytest.raises(ValueError, match="identifier"):
        Command(name, SELECT)


def test_leading_digit_allowed() -> None:
    assert Command("3d_status", STATUS).name == "3d_status"


def test_bad_suffix_rejected() -> None:
    with pytest.raises(ValueError, match="identifier"):
        Command("x", SELECT, ("Top",))
    with pytest.raises(ValueError, match="duplicate"):
        Command("x", SELECT, ("top", "top"))


# -- table spot checks --------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "kind", "suffixes"),
    [
        ("power", SELECT, ()),
        ("ipv4_network_setting", SELECT, ()),
        ("complete_maintenance", SELECT, ()),
        ("power_status", STATUS, ()),
        ("error", STATUS, ()),
        ("warning", STATUS, ()),
        ("timer", STATUS, ()),
        ("mac_address", STATUS, ()),
        ("3d_status", STATUS, ()),
        ("ipv4_ip_address", SELECT, ()),
        ("input", SELECT, ()),
        ("muting", SELECT, ()),
        ("picture_mode", SELECT, ()),
        ("picture_mode_reset", EXEC, ()),
        ("contrast", NUMBER, ()),
        ("col_space_x", NUMBER, ("r", "g", "b")),
        ("light_output_val", NUMBER, ()),
        ("lamp_control", SELECT, ()),
        ("col_corr_hue", NUMBER, ("r", "g", "b", "c", "y", "m")),
        ("col_corr_brt", NUMBER, ("r", "g", "b", "c", "y", "m")),
        ("hdr", SELECT, ()),
        ("aspect", SELECT, ()),
        ("image_shift", NUMBER, ("h", "v")),
        ("2d3d_sel", SELECT, ()),
        ("3d_depth", NUMBER, ()),
        ("infrared", SELECT, ()),
        ("hdmi_signal_format", SELECT, ("hdmi1", "hdmi2", "hdmi3", "hdmi4")),
        ("stream_format", SELECT, ("dp1", "dp2", "dp3", "dp4", "dp1_2", "dp1_2_3_4")),
        ("dynamic_range", SELECT, ("hdmi1", "hdmi2", "hdmi3", "hdmi4")),
        ("blanking", NUMBER, ("top", "bottom", "left", "right")),
        ("color_matching_brt", NUMBER, ("lev1", "lev2", "lev3", "lev4", "lev5", "lev6")),
        ("color_matching_reset", EXEC, ()),
        ("panel_align_shift_adj_r", NUMBER, ("h", "v")),
        ("blend_sw", SELECT, ("top", "bottom", "left", "right")),
        ("blend_start", NUMBER, ("top", "bottom", "left", "right")),
        ("blend_cursor_color", SELECT, ("start", "end")),
        ("trigger", SELECT, ("port1", "port2")),
        ("activate_licenses", EXEC, ()),
        ("corner_keystone_h", NUMBER, ("top_left", "top_right", "bottom_left", "bottom_right")),
        ("screen_fitting_reset", EXEC, ()),
        ("key", SELECT, ()),
    ],
)
def test_known_commands(name: str, kind: CommandKind, suffixes: tuple[str, ...]) -> None:
    cmd = get_command(name)
    assert cmd.kind is kind
    assert cmd.suffixes == suffixes


def test_table_is_immutable() -> None:
    with pytest.raises(TypeError):
        COMMANDS["x"] = Command("x", SELECT)  # type: ignore[index]
    with pytest.raises(TypeError):
        del COMMANDS["power"]  # type: ignore[attr-defined]


def test_keys_match_names() -> None:
    assert COMMANDS
    for key, cmd in COMMANDS.items():
        assert key == cmd.name
        assert cmd.description


def test_commands_are_frozen() -> None:
    cmd = get_command("power")
    with pytest.raises(AttributeError):
        cmd.name = "other"  # type: ignore[misc]


def test_info_type_kinds() -> None:
    assert dict(INFO_TYPE_KINDS) == {
        "sys_sel": SELECT,
        "sys_stat": STATUS,
        "sys_var": SELECT,
        "menu_sel": SELECT,
        "menu_num": NUMBER,
        "menu_exec": EXEC,
        "key": SELECT,
    }
    with pytest.raises(TypeError):
        INFO_TYPE_KINDS["x"] = SELECT  # type: ignore[index]


def test_key_codes() -> None:
    for code in (
        "menu",
        "power_on",
        "picmode1",
        "aspect_2.35_1_zoom",
        "lens_focus_far",
        "3d_depth+",
    ):
        assert code in KEY_CODES
    assert len(KEY_CODES) >= 100
    assert all(code.isascii() for code in KEY_CODES)


def test_get_command_unknown() -> None:
    with pytest.raises(KeyError, match="unknown ADCP command 'contrsat'"):
        get_command("contrsat")
    with pytest.raises(KeyError, match="did you mean"):
        get_command("contrsat")
    with pytest.raises(KeyError):
        get_command("")
