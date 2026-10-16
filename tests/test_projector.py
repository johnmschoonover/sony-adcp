"""Tests for the Projector facade against the fake projector."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from pyadcp import UnknownCommandError
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
    ProjectorIdentity,
    ProjectorStatus,
)
from tests.fake_projector import VW290ES, FakeCommand, FakeProjector, profile_with

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

type FakeFactory = Callable[..., FakeProjector]


def _projector(fake: FakeProjector) -> Projector:
    """A not-yet-connected facade for ``fake`` with short test-friendly timeouts."""
    return Projector(
        "127.0.0.1",
        fake.port,
        "Projector",
        connect_timeout=2,
        command_timeout=2,
        wake_timeout=0.5,
        wake_retry_delay=0.05,
    )


async def _start(factory: FakeFactory, **kwargs: object) -> FakeProjector:
    projector = factory(**kwargs)
    await projector.start()
    return projector


def _info_names(lines: list[str]) -> list[str]:
    """The command names of every ``? --info`` line in ``lines``."""
    return [line.split(" ")[0] for line in lines if line.endswith("? --info")]


@pytest.fixture
async def projector(fake: FakeProjector) -> AsyncIterator[Projector]:
    proj = _projector(fake)
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


async def test_adjust_and_reset_number(fake_factory: FakeFactory) -> None:
    # A model whose factory contrast is not the fake's fallback of 0, so the
    # reset is observable.
    model = profile_with(
        VW290ES, add={"contrast": FakeCommand("menu_num", range=(0, 100, 1), default=60)}
    )
    fake = await _start(fake_factory, model=model)
    default = fake.model.commands["contrast"].default
    assert default == 60
    async with _projector(fake) as powered:
        await powered.power_on()
        assert await powered.get_number("contrast") == default
        await powered.set_number("contrast", 50)
        await powered.adjust_number("contrast", 3)
        assert await powered.get_number("contrast") == 53
        await powered.adjust_number("contrast", -5)
        assert await powered.get_number("contrast") == 48
        await powered.reset("contrast")
        assert await powered.get_number("contrast") == default
    assert fake.received[-2:] == ["contrast --reset", "contrast ?"]


@pytest.mark.parametrize(
    ("value", "outcome"),
    [
        (1.5, "err_val"),
        (-1.5, "err_val"),
        (2.0, "ok"),
        (100.0, "ok"),
        (100.5, "err_val"),
    ],
)
async def test_set_number_with_float(
    powered: Projector, fake: FakeProjector, value: float, outcome: str
) -> None:
    # contrast has step 1, so only whole numbers are on the grid.
    assert fake.model.commands["contrast"].range == (0, 100, 1)
    if outcome == "ok":
        await powered.set_number("contrast", value)
        assert await powered.get_number("contrast") == int(value)
    else:
        with pytest.raises(CommandValueError):
            await powered.set_number("contrast", value)
        assert await powered.get_number("contrast") == 0
    assert fake.received[1] == f"contrast {value}"


async def test_set_number_with_float_respects_step(fake_factory: FakeFactory) -> None:
    model = profile_with(VW290ES, add={"brightness": FakeCommand("menu_num", range=(0, 100, 5))})
    fake = await _start(fake_factory, model=model)
    async with _projector(fake) as proj:
        await proj.power_on()
        await proj.set_number("brightness", 15.0)
        assert await proj.get_number("brightness") == 15
        with pytest.raises(CommandValueError):
            await proj.set_number("brightness", 12.5)
        assert await proj.get_number("brightness") == 15


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


async def test_hdmi_signal_format_per_input(fake_factory: FakeFactory) -> None:
    model = profile_with(
        VW290ES,
        add={
            "hdmi_signal_format": FakeCommand(
                "menu_sel",
                options=("standard", "enhanced"),
                suffixes=("hdmi1", "hdmi2"),
                default={"hdmi1": "standard", "hdmi2": "standard"},
            )
        },
    )
    fake = await _start(fake_factory, model=model)
    defaults = fake.model.commands["hdmi_signal_format"].default
    assert isinstance(defaults, dict)
    async with _projector(fake) as powered:
        await powered.power_on()
        await powered.set_hdmi_signal_format(2, "enhanced")
        assert await powered.get_hdmi_signal_format(2) == "enhanced"
        # The other input is untouched.
        assert await powered.get_hdmi_signal_format(1) == defaults["hdmi1"] == "standard"


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


@pytest.mark.parametrize("reply", ['"x"', "7", '{"min":0}', '{"min":"a","max":"b"}', "null"])
async def test_get_range_rejects_non_range_reply(
    powered: Projector, fake: FakeProjector, reply: str
) -> None:
    fake.reply_overrides["contrast ? --range"] = reply
    with pytest.raises(ProtocolError, match="contrast"):
        await powered.get_range("contrast")
    # The session itself is fine: the reply was well-formed JSON, just not a range.
    assert await powered.get_contrast() == 0


async def test_get_info_without_suffix(projector: Projector, fake: FakeProjector) -> None:
    info = await projector.get_info("picture_mode")
    assert info.name == "picture_mode"
    assert info.kind is CommandKind.SELECT
    assert info.type == "menu_sel"
    assert info.version == "1.0"
    assert info.options == VW290ES.commands["picture_mode"].options
    assert info.range is None
    assert info.suffixes == ()
    assert fake.received == ["picture_mode ? --info"]


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


async def test_identify_is_cached_until_refreshed(
    projector: Projector, fake: FakeProjector
) -> None:
    identity = await projector.identify()
    assert isinstance(identity, ProjectorIdentity)
    assert identity.modelname == "VPL-VW295ES"
    assert identity.serialnum == "1234567"
    assert identity.mac_address == "08-00-46-12-34-56"
    assert identity.version == {"main": "1.000"}
    assert await projector.identify() is identity
    assert fake.received.count("modelname ?") == 1
    refreshed = await projector.identify(refresh=True)
    assert refreshed is not identity
    assert refreshed == identity
    assert fake.received.count("modelname ?") == 2


async def test_poll_status_reads_identity_once(projector: Projector, fake: FakeProjector) -> None:
    first = await projector.poll_status()
    second = await projector.poll_status()
    assert fake.received.count("modelname ?") == 1
    assert fake.received.count("serialnum ?") == 1
    assert fake.received.count("version ?") == 1
    assert fake.received.count("mac_address ?") == 1
    # The health fields are read every time.
    assert fake.received.count("power_status ?") == 2
    assert (first.modelname, first.serialnum) == (second.modelname, second.serialnum)
    third = await projector.poll_status(refresh_identity=True)
    assert fake.received.count("modelname ?") == 2
    assert third.modelname == "VPL-VW295ES"


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


async def test_capabilities_in_standby_are_completed_after_power_on(
    fake_factory: FakeFactory,
) -> None:
    fake = await _start(fake_factory, info_gated=True)
    async with _projector(fake) as proj:
        caps = await proj.capabilities()
        first = len(fake.received)
        assert {"picture_mode", "contrast", "dynamic_range"} <= caps.incomplete
        # Incomplete commands count as supported but say nothing about values.
        assert caps.supports("picture_mode")
        assert caps.options("picture_mode") is None
        assert caps.range("contrast") is None
        assert caps["picture_mode"].kind is CommandKind.SELECT
        assert caps["contrast"].kind is CommandKind.NUMBER
        assert caps["dynamic_range"].suffixes == ("hdmi1", "hdmi2")
        assert caps.supports("dynamic_range", "hdmi1")
        # System commands are not gated, and unsupported ones are still known.
        assert "power_status" not in caps.incomplete
        assert caps["power"].options == ("on", "off")
        assert "hdr_tone_mapping" in caps.unsupported

        await proj.power_on()
        before = len(fake.received)
        again = await proj.capabilities()
        second = len(fake.received) - before
        assert again is not caps
        assert again.incomplete == frozenset()
        assert again.options("picture_mode") is not None
        assert "reference" in (again.options("picture_mode") or ())
        assert again.range("contrast") == NumericRange(0, 100, 1)
        assert again["dynamic_range"].suffixes == ("hdmi1", "hdmi2")
        assert again.unsupported == caps.unsupported
        assert set(again.commands) == set(caps.commands)
        # Only what standby withheld is probed again.
        assert 0 < second < first
        assert set(_info_names(fake.received[before:])) == caps.incomplete
        # And now the result is stable.
        assert await proj.capabilities() is again
        assert len(fake.received) == before + second


async def test_capabilities_names_probe_only_those(
    projector: Projector, fake: FakeProjector
) -> None:
    caps = await projector.capabilities(names=["contrast", "hdr_tone_mapping"])
    assert fake.received == ["contrast ? --info", "hdr_tone_mapping ? --info"]
    assert set(caps.commands) == {"contrast"}
    assert caps.unsupported == {"hdr_tone_mapping"}
    assert caps.incomplete == frozenset()
    assert caps.supports("contrast")
    assert caps.range("contrast") == NumericRange(0, 100, 1)
    assert not caps.supports("hdr_tone_mapping")
    assert not caps.supports("picture_mode")
    # A further names probe merges into what is known.
    merged = await projector.capabilities(names=["picture_mode", "dynamic_range"])
    assert fake.received[2:] == [
        "picture_mode ? --info",
        "dynamic_range --hdmi1 ? --info",
        "dynamic_range --hdmi2 ? --info",
        "dynamic_range --hdmi3 ? --info",
        "dynamic_range --hdmi4 ? --info",
    ]
    assert set(merged.commands) == {"contrast", "picture_mode", "dynamic_range"}
    assert merged.unsupported == {"hdr_tone_mapping"}
    assert merged["dynamic_range"].suffixes == ("hdmi1", "hdmi2")
    # Probing a name again replaces its earlier verdict rather than duplicating it.
    fake.model = profile_with(fake.model, remove=("contrast",))
    reprobed = await projector.capabilities(names=["contrast"])
    assert "contrast" not in reprobed.commands
    assert reprobed.unsupported == {"hdr_tone_mapping", "contrast"}


async def test_capabilities_names_rejects_unknown_command(
    projector: Projector, fake: FakeProjector
) -> None:
    with pytest.raises(UnknownCommandError, match="nope"):
        await projector.capabilities(names=["nope"])
    assert fake.received == []
    # key has no info form and is skipped, not an error.
    caps = await projector.capabilities(names=["key"])
    assert fake.received == []
    assert "key" not in caps
    assert "key" not in caps.unsupported


async def test_full_capabilities_after_partial_probe_covers_everything(
    projector: Projector, fake: FakeProjector
) -> None:
    partial = await projector.capabilities(names=["contrast", "hdr_tone_mapping"])
    assert set(partial.commands) == {"contrast"}
    full = await projector.capabilities()
    probed = set(full.commands) | set(full.unsupported)
    assert probed == set(COMMANDS) - {"key"}
    # The two already-probed commands are not asked again.
    assert fake.received.count("contrast ? --info") == 1
    assert fake.received.count("hdr_tone_mapping ? --info") == 1
    # And a third call sends nothing.
    sent = len(fake.received)
    assert await projector.capabilities() is full
    assert len(fake.received) == sent


async def test_get_info(projector: Projector) -> None:
    info = await projector.get_info("dynamic_range", "hdmi1")
    assert info.name == "dynamic_range"
    assert info.options is not None
    assert "full" in info.options
    assert info.suffixes == ("hdmi1",)
