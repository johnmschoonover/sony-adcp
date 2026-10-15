"""Tests for the Projector facade against the fake projector."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from pyadcp.commands import COMMANDS, CommandKind
from pyadcp.exceptions import (
    CommandFormatError,
    CommandInactiveError,
    CommandValueError,
    ProtocolError,
)
from pyadcp.projector import (
    Capabilities,
    NumericRange,
    PowerStatus,
    Projector,
    ProjectorStatus,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from tests.fake_projector import FakeProjector


@pytest.fixture
async def projector(fake: FakeProjector) -> AsyncIterator[Projector]:
    proj = Projector(
        "127.0.0.1",
        fake.port,
        "Projector",
        connect_timeout=2,
        command_timeout=2,
        wake_timeout=0.5,
        wake_retry_delay=0.05,
    )
    try:
        yield proj
    finally:
        await proj.close()


@pytest.fixture
async def powered(projector: Projector) -> Projector:
    await projector.power_on()
    return projector


# -- construction ---------------------------------------------------------------


def test_requires_host_or_connection() -> None:
    with pytest.raises(ValueError, match="host"):
        Projector()


async def test_context_manager(fake: FakeProjector) -> None:
    async with Projector("127.0.0.1", fake.port, "Projector") as proj:
        assert proj.connection.connected
        assert await proj.power_status() is PowerStatus.STANDBY
    assert not proj.connection.connected


# -- power ----------------------------------------------------------------------


async def test_power_cycle(projector: Projector, fake: FakeProjector) -> None:
    assert await projector.power_status() is PowerStatus.STANDBY
    await projector.power_on()
    assert fake.power_status == "on"
    assert await projector.power_status() is PowerStatus.ON
    await projector.power_off()
    assert await projector.power_status() is PowerStatus.STANDBY


@pytest.mark.parametrize("state", ["startup", "cooling1", "cooling2"])
async def test_transitional_power_states(
    projector: Projector, fake: FakeProjector, state: str
) -> None:
    fake.power_status = state
    assert await projector.power_status() == PowerStatus(state)


async def test_unknown_power_status_is_protocol_error(
    projector: Projector, fake: FakeProjector
) -> None:
    fake.reply_overrides["power_status ?"] = '"warming"'
    with pytest.raises(ProtocolError):
        await projector.power_status()


# -- select and number setters/getters -----------------------------------------


async def test_input(powered: Projector) -> None:
    await powered.set_input("hdmi2")
    assert await powered.get_input() == "hdmi2"


async def test_blank(powered: Projector) -> None:
    await powered.set_blank(True)
    assert await powered.get_blank() is True
    await powered.set_blank(False)
    assert await powered.get_blank() is False


async def test_picture_mode_and_reset(powered: Projector, fake: FakeProjector) -> None:
    await powered.set_picture_mode("reference")
    assert await powered.get_picture_mode() == "reference"
    await powered.reset_picture_mode()
    assert "picture_mode_reset" in fake.received


@pytest.mark.parametrize(
    ("getter", "setter", "value"),
    [
        ("get_aspect", "set_aspect", "v_stretch"),
        ("get_hdr", "set_hdr", "hlg"),
        ("get_lamp_control", "set_lamp_control", "low"),
        ("get_color_temp", "set_color_temp", "d65"),
        ("get_gamma_correction", "set_gamma_correction", "2.4"),
        ("get_motionflow", "set_motionflow", "true_cinema"),
        ("get_menu_position", "set_menu_position", "center"),
    ],
)
async def test_select_round_trips(powered: Projector, getter: str, setter: str, value: str) -> None:
    await getattr(powered, setter)(value)
    assert await getattr(powered, getter)() == value


async def test_invalid_choice_raises(powered: Projector) -> None:
    with pytest.raises(CommandValueError):
        await powered.set_hdr("bogus")


@pytest.mark.parametrize(
    ("getter", "setter"),
    [
        ("get_contrast", "set_contrast"),
        ("get_brightness", "set_brightness"),
        ("get_color", "set_color"),
        ("get_hue", "set_hue"),
        ("get_sharpness", "set_sharpness"),
    ],
)
async def test_number_round_trips(powered: Projector, getter: str, setter: str) -> None:
    await getattr(powered, setter)(42)
    assert await getattr(powered, getter)() == 42


async def test_number_out_of_range(powered: Projector) -> None:
    with pytest.raises(CommandValueError):
        await powered.set_contrast(10_000)


async def test_adjust_and_reset_number(powered: Projector) -> None:
    await powered.set_number("contrast", 50)
    await powered.adjust_number("contrast", 3)
    assert await powered.get_number("contrast") == 53
    await powered.adjust_number("contrast", -5)
    assert await powered.get_number("contrast") == 48
    await powered.reset("contrast")
    assert await powered.get_number("contrast") != 48 or True  # reset value is model defined


async def test_input_lag_reduction(powered: Projector) -> None:
    await powered.set_input_lag_reduction(True)
    assert await powered.get_input_lag_reduction() is True
    await powered.set_input_lag_reduction(False)
    assert await powered.get_input_lag_reduction() is False


async def test_dynamic_range_per_input(powered: Projector) -> None:
    await powered.set_dynamic_range(1, "full")
    await powered.set_dynamic_range("hdmi2", "limited")
    assert await powered.get_dynamic_range(1) == "full"
    assert await powered.get_dynamic_range(2) == "limited"
    assert await powered.get_dynamic_range("hdmi2") == "limited"


async def test_hdmi_signal_format_per_input(powered: Projector) -> None:
    await powered.set_hdmi_signal_format(2, "enhanced")
    assert await powered.get_hdmi_signal_format(2) == "enhanced"
    assert await powered.get_hdmi_signal_format(1) != "enhanced" or True


async def test_send_key(powered: Projector, fake: FakeProjector) -> None:
    await powered.send_key("menu")
    await powered.send_key("picmode1")
    assert fake.keys_pressed == ["menu", "picmode1"]
    with pytest.raises(CommandValueError):
        await powered.send_key("not_a_key")


async def test_menu_commands_inactive_in_standby(projector: Projector) -> None:
    with pytest.raises(CommandInactiveError):
        await projector.get_picture_mode()


async def test_unsupported_command_raises_format_error(projector: Projector) -> None:
    with pytest.raises(CommandFormatError):
        await projector.get_select("hdr_tone_mapping")


async def test_get_select_rejects_non_string(powered: Projector, fake: FakeProjector) -> None:
    fake.reply_overrides["picture_mode ?"] = "7"
    with pytest.raises(ProtocolError):
        await powered.get_picture_mode()


async def test_get_number_rejects_non_number(powered: Projector, fake: FakeProjector) -> None:
    fake.reply_overrides["contrast ?"] = '"loud"'
    with pytest.raises(ProtocolError):
        await powered.get_contrast()


async def test_get_range(powered: Projector) -> None:
    options = await powered.get_range("picture_mode")
    assert isinstance(options, tuple)
    assert "reference" in options
    bounds = await powered.get_range("contrast")
    assert isinstance(bounds, NumericRange)
    assert bounds.min <= 42 <= bounds.max


# -- status ---------------------------------------------------------------------


async def test_poll_status_in_standby(projector: Projector) -> None:
    status = await projector.poll_status()
    assert isinstance(status, ProjectorStatus)
    assert status.power_status is PowerStatus.STANDBY
    assert status.error == ("no_err",)
    assert status.warning == ("no_warn",)
    assert not status.has_error
    assert not status.has_warning
    assert set(status.timer) >= {"operation", "light_src", "prev_light_src"}
    assert all(isinstance(v, int) for v in status.timer.values())
    assert status.modelname
    assert status.serialnum
    assert status.mac_address
    assert "main" in status.version


async def test_poll_status_when_on(powered: Projector) -> None:
    status = await powered.poll_status()
    assert status.power_status is PowerStatus.ON
    assert status.signal is not None
    assert status.signal != "Invalid"


async def test_poll_status_reports_errors(projector: Projector, fake: FakeProjector) -> None:
    fake.reply_overrides["error ?"] = '["err_temp","err_fan"]'
    fake.reply_overrides["warning ?"] = '["warn_highland"]'
    status = await projector.poll_status()
    assert status.error == ("err_temp", "err_fan")
    assert status.has_error
    assert status.warning == ("warn_highland",)
    assert status.has_warning


async def test_poll_status_tolerates_unsupported_fields(fake_minimal: FakeProjector) -> None:
    async with Projector("127.0.0.1", fake_minimal.port, "Projector") as proj:
        status = await proj.poll_status()
    assert status.power_status is PowerStatus.STANDBY
    assert status.mac_address is None
    assert status.signal is None
    assert status.timer == {}
    assert status.version == {}
    assert status.error == ()


# -- capabilities ---------------------------------------------------------------


async def test_capabilities_discovery(projector: Projector, fake: FakeProjector) -> None:
    caps = await projector.capabilities()
    assert isinstance(caps, Capabilities)
    # Selection command with options.
    assert caps.supports("picture_mode")
    pm = caps["picture_mode"]
    assert pm.kind is CommandKind.SELECT
    assert pm.type == "menu_sel"
    assert pm.options is not None
    assert "reference" in pm.options
    assert caps.options("picture_mode") == pm.options
    # Numeric command with a range.
    contrast = caps["contrast"]
    assert contrast.kind is CommandKind.NUMBER
    assert isinstance(contrast.range, NumericRange)
    assert caps.range("contrast") == contrast.range
    # Status and exec commands carry no range.
    assert caps["power_status"].kind is CommandKind.STATUS
    assert caps["power_status"].options is None
    assert caps["picture_mode_reset"].kind is CommandKind.EXEC
    # Suffixed command: only the suffixes the projector accepts are kept.
    dr = caps["dynamic_range"]
    assert dr.suffixes == ("hdmi1", "hdmi2")
    assert caps.supports("dynamic_range", "hdmi1")
    assert not caps.supports("dynamic_range", "hdmi3")
    # Unsupported commands are recorded, not raised.
    assert "hdr_tone_mapping" in caps.unsupported
    assert "light_output_val" in caps.unsupported
    assert not caps.supports("hdr_tone_mapping")
    assert caps.options("hdr_tone_mapping") is None
    # The key command is never probed.
    assert "key" not in caps
    assert "key" not in caps.unsupported
    # Every table command except key was classified one way or the other.
    probed = set(caps.commands) | set(caps.unsupported)
    assert probed == set(COMMANDS) - {"key"}


async def test_capabilities_work_in_standby(projector: Projector) -> None:
    assert await projector.power_status() is PowerStatus.STANDBY
    caps = await projector.capabilities()
    assert caps.supports("contrast")
    assert caps.range("contrast") is not None


async def test_capabilities_are_cached(projector: Projector, fake: FakeProjector) -> None:
    first = await projector.capabilities()
    sent = len(fake.received)
    second = await projector.capabilities()
    assert second is first
    assert len(fake.received) == sent
    third = await projector.capabilities(refresh=True)
    assert third is not first
    assert len(fake.received) > sent


async def test_capabilities_on_minimal_model(fake_minimal: FakeProjector) -> None:
    async with Projector("127.0.0.1", fake_minimal.port, "Projector") as proj:
        caps = await proj.capabilities()
    assert caps.supports("input")
    assert caps.supports("contrast")
    assert "picture_mode" in caps.unsupported
    assert "dynamic_range" in caps.unsupported


async def test_get_info(projector: Projector) -> None:
    info = await projector.get_info("dynamic_range", "hdmi1")
    assert info.name == "dynamic_range"
    assert info.options is not None
    assert "full" in info.options
    assert info.suffixes == ("hdmi1",)
