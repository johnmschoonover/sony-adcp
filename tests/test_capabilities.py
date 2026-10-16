"""Tests for the client-facing capability features: per-suffix detail,
serialisation, change comparison, progress events and restoring a snapshot."""

from __future__ import annotations

import asyncio
import json
from contextlib import aclosing
from typing import TYPE_CHECKING

import pytest

from pyadcp import (
    CAPABILITIES_FORMAT,
    Capabilities,
    CapabilityChanges,
    CommandCapability,
    CommandKind,
    DiscoveryEvent,
    NumericRange,
    Projector,
    SuffixCapability,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from tests.fake_projector import FakeProjector


@pytest.fixture
async def projector(fake: FakeProjector) -> AsyncIterator[Projector]:
    proj = Projector("127.0.0.1", fake.port, "Projector", command_timeout=2, wake_timeout=0.5)
    try:
        yield proj
    finally:
        await proj.close()


def _info_lines(lines: list[str]) -> list[str]:
    return [line for line in lines if line.endswith("? --info")]


# -- per-suffix detail ---------------------------------------------------------------


async def test_suffixed_command_records_each_suffix(projector: Projector) -> None:
    caps = await projector.capabilities(names=["dynamic_range", "blanking"])
    dr = caps["dynamic_range"]
    assert tuple(dr.by_suffix) == ("hdmi1", "hdmi2")
    assert dr.by_suffix["hdmi1"].options == dr.options
    assert dr.options_for("hdmi2") == dr.options
    assert dr.options_for("hdmi3") is None
    assert caps.options("dynamic_range", "hdmi1") == dr.options
    assert not any(d.incomplete for d in dr.by_suffix.values())
    blanking = caps["blanking"]
    assert set(blanking.by_suffix) == {"top", "bottom", "left", "right"}
    assert blanking.range_for("left") == blanking.range
    assert caps.range("blanking", "top") == blanking.range


async def test_per_suffix_range_differences_are_kept(
    projector: Projector, fake: FakeProjector
) -> None:
    fake.reply_overrides["blanking --left ? --info"] = (
        '{"type":"menu_num","version":"1.0","range":{"min":0,"max":7}}'
    )
    caps = await projector.capabilities(names=["blanking"])
    assert caps.range("blanking", "left") == NumericRange(0, 7)
    assert caps.range("blanking", "top") != NumericRange(0, 7)
    # The top-level range is the first suffix's, in table order.
    assert caps.range("blanking") == caps.range("blanking", "top")


async def test_unsuffixed_command_has_no_by_suffix(projector: Projector) -> None:
    caps = await projector.capabilities(names=["contrast"])
    assert caps["contrast"].by_suffix == {}
    assert caps["contrast"].options_for() is None
    assert caps["contrast"].range_for() == caps["contrast"].range


async def test_only_incomplete_suffixes_are_reprobed(
    projector: Projector, fake: FakeProjector
) -> None:
    fake.reply_overrides["dynamic_range --hdmi2 ? --info"] = "err_inactive"
    caps = await projector.capabilities()
    assert caps.incomplete == {"dynamic_range"}
    assert caps["dynamic_range"].by_suffix["hdmi1"].incomplete is False
    assert caps["dynamic_range"].by_suffix["hdmi2"].incomplete is True
    assert caps.options("dynamic_range", "hdmi2") is None
    # The head values come from the complete suffix.
    assert caps.options("dynamic_range") == caps.options("dynamic_range", "hdmi1")

    del fake.reply_overrides["dynamic_range --hdmi2 ? --info"]
    before = len(fake.received)
    again = await projector.capabilities()
    assert _info_lines(fake.received[before:]) == ["dynamic_range --hdmi2 ? --info"]
    assert "dynamic_range" not in again.incomplete
    assert again.options("dynamic_range", "hdmi2") == again.options("dynamic_range", "hdmi1")
    assert tuple(again["dynamic_range"].by_suffix) == ("hdmi1", "hdmi2")


# -- serialisation ---------------------------------------------------------------------


async def test_to_dict_round_trips_through_json(projector: Projector) -> None:
    caps = await projector.capabilities()
    data = caps.to_dict()
    assert data["format"] == CAPABILITIES_FORMAT
    text = json.dumps(data)  # must be JSON-native
    restored = Capabilities.from_dict(json.loads(text))
    assert restored == caps
    assert restored.to_dict() == data
    # Spot checks on the layout a client will store.
    pm = data["commands"]["picture_mode"]
    assert pm["kind"] == "select"
    assert isinstance(pm["options"], list)
    assert data["commands"]["contrast"]["range"] == {"min": 0, "max": 100, "step": 1}
    assert "hdmi1" in data["commands"]["dynamic_range"]["by_suffix"]
    assert "hdr_tone_mapping" in data["unsupported"]


def test_from_dict_rejects_unknown_format() -> None:
    with pytest.raises(ValueError, match="format"):
        Capabilities.from_dict({"format": CAPABILITIES_FORMAT + 1, "commands": {}})
    with pytest.raises(ValueError, match="format"):
        Capabilities.from_dict({"commands": {}})


@pytest.mark.parametrize(
    "bad",
    [
        {"format": 1, "commands": {"contrast": {"kind": "number"}}},  # no name
        {"format": 1, "commands": {"contrast": {"name": "contrast", "kind": "weird"}}},
        {"format": 1, "commands": {"x": {"name": "x", "kind": "select", "options": "ab"}}},
        {"format": 1, "commands": {"x": {"name": "x", "kind": "number", "range": [0, 1]}}},
        {"format": 1, "commands": {"x": {"name": "x", "kind": "number", "range": {"min": "a"}}}},
        {"format": 1, "commands": "nope"},
    ],
)
def test_from_dict_rejects_malformed(bad: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="malformed"):
        Capabilities.from_dict(bad)


def test_command_capability_round_trip_with_suffixes() -> None:
    cap = CommandCapability(
        name="blanking",
        kind=CommandKind.NUMBER,
        type="menu_num",
        version="1.0",
        range=NumericRange(0, 100, 2),
        suffixes=("top", "left"),
        by_suffix={
            "top": SuffixCapability(range=NumericRange(0, 100, 2)),
            "left": SuffixCapability(incomplete=True),
        },
    )
    assert CommandCapability.from_dict(cap.to_dict()) == cap


async def test_restore_capabilities_skips_known_commands(
    fake: FakeProjector, projector: Projector
) -> None:
    caps = await projector.capabilities()
    data = json.loads(json.dumps(caps.to_dict()))

    async with Projector("127.0.0.1", fake.port, "Projector") as fresh:
        fresh.restore_capabilities(Capabilities.from_dict(data))
        before = len(fake.received)
        again = await fresh.capabilities()
        assert again == caps
        assert _info_lines(fake.received[before:]) == []
        # refresh=True discards the snapshot and probes everything again.
        full = await fresh.capabilities(refresh=True)
        assert len(_info_lines(fake.received[before:])) > 100
        assert full == caps


async def test_restored_snapshot_with_incomplete_is_completed(
    fake: FakeProjector, projector: Projector
) -> None:
    fake.info_gated = True
    partial = await projector.capabilities(names=["picture_mode", "contrast"])
    assert partial.incomplete == {"picture_mode", "contrast"}
    fake.info_gated = False
    fake.power_status = "on"

    async with Projector("127.0.0.1", fake.port, "Projector") as fresh:
        fresh.restore_capabilities(Capabilities.from_dict(partial.to_dict()))
        before = len(fake.received)
        caps = await fresh.capabilities(names=["picture_mode", "contrast"])
        assert _info_lines(fake.received[before:]) == [
            "picture_mode ? --info",
            "contrast ? --info",
        ]
        assert caps.incomplete == frozenset()
        assert caps.options("picture_mode") is not None


# -- change comparison -------------------------------------------------------------------


def _cap(name: str, options: tuple[str, ...] | None = None, **kw: object) -> CommandCapability:
    return CommandCapability(name, CommandKind.SELECT, "menu_sel", "1.0", options=options, **kw)  # type: ignore[arg-type]


def test_changes_since_classifies_every_kind_of_difference() -> None:
    old = Capabilities(
        {"a": _cap("a", ("x",)), "b": _cap("b", ("x",)), "c": _cap("c"), "d": _cap("d", ("x",))},
        unsupported=frozenset({"e"}),
        incomplete=frozenset({"c"}),
    )
    new = Capabilities(
        {
            "a": _cap("a", ("x",)),
            "b": _cap("b", ("x", "y")),
            "c": _cap("c", ("z",)),
            "e": _cap("e"),
        },
        unsupported=frozenset({"d"}),
    )
    changes = new.changes_since(old)
    assert changes.added == {"e"}
    assert changes.removed == {"d"}
    assert changes.changed == {"b", "c"}
    assert changes.completed == {"c"}
    assert changes.affected == {"b", "c", "d", "e"}
    assert changes


def test_changes_since_is_empty_for_identical_snapshots() -> None:
    caps = Capabilities({"a": _cap("a", ("x",))})
    changes = caps.changes_since(caps)
    assert changes == CapabilityChanges()
    assert not changes
    assert changes.affected == frozenset()


async def test_changes_after_power_on_name_completed_commands(
    projector: Projector, fake: FakeProjector
) -> None:
    fake.info_gated = True
    standby = await projector.capabilities(names=["picture_mode", "contrast", "power_status"])
    fake.power_status = "on"
    powered = await projector.capabilities()
    changes = powered.changes_since(standby)
    assert changes.completed >= {"picture_mode", "contrast"}
    assert "power_status" not in changes.affected
    assert changes.added  # the rest of the table was discovered by the second call
    assert not changes.removed


# -- progress ------------------------------------------------------------------------------


async def test_progress_callback_sees_every_probe(projector: Projector) -> None:
    seen: list[DiscoveryEvent] = []
    names = ["contrast", "hdr_tone_mapping", "dynamic_range"]
    caps = await projector.capabilities(names=names, progress=seen.append)
    assert [e.name for e in seen] == names
    assert [e.index for e in seen] == [1, 2, 3]
    assert all(e.total == 3 for e in seen)
    assert seen[0].supported
    assert seen[0].capability is not None
    assert not seen[1].supported
    assert seen[1].capability is None
    assert seen[2].capability is not None
    assert seen[2].capability.suffixes == ("hdmi1", "hdmi2")
    assert not any(e.incomplete for e in seen)
    # Snapshots are cumulative and the last one is the returned result.
    assert set(seen[0].snapshot.commands) == {"contrast"}
    assert seen[1].snapshot.unsupported == {"hdr_tone_mapping"}
    assert seen[2].snapshot == caps


async def test_discover_iterator_can_stop_early_and_resume(
    projector: Projector, fake: FakeProjector
) -> None:
    names = ["contrast", "brightness", "color"]
    async with aclosing(projector.discover_capabilities(names=names)) as events:
        async for event in events:
            if event.index == 2:
                break
    assert _info_lines(fake.received) == ["contrast ? --info", "brightness ? --info"]
    # A plain call completes the table without asking those two again.
    caps = await projector.capabilities()
    lines = _info_lines(fake.received)
    assert lines.count("contrast ? --info") == 1
    assert lines.count("brightness ? --info") == 1
    assert lines.count("color ? --info") == 1
    assert set(caps.commands) >= set(names)


async def test_discover_iterator_yields_nothing_when_cache_is_complete(
    projector: Projector, fake: FakeProjector
) -> None:
    await projector.capabilities()
    before = len(fake.received)
    events = [e async for e in projector.discover_capabilities()]
    assert events == []
    assert len(fake.received) == before


async def test_capabilities_with_empty_names_does_not_probe(
    projector: Projector, fake: FakeProjector
) -> None:
    caps = await projector.capabilities(names=[])
    assert caps == Capabilities()
    assert fake.received == []


async def test_concurrent_discoveries_converge(projector: Projector, fake: FakeProjector) -> None:
    names = ["contrast", "brightness", "color", "hue", "sharpness"]
    first, second = await asyncio.gather(
        projector.capabilities(names=names), projector.capabilities(names=names)
    )
    assert set(first.commands) >= set(names)
    assert set(second.commands) >= set(names)
    final = await projector.capabilities(names=names)
    assert set(final.commands) == set(names)


async def test_event_incomplete_flag(projector: Projector, fake: FakeProjector) -> None:
    fake.info_gated = True
    seen: list[DiscoveryEvent] = []
    await projector.capabilities(names=["picture_mode", "power_status"], progress=seen.append)
    assert [(e.name, e.incomplete) for e in seen] == [
        ("picture_mode", True),
        ("power_status", False),
    ]


def test_progress_callback_type_is_plain_callable() -> None:
    # Documented contract: any sync callable taking one DiscoveryEvent.
    calls: list[str] = []
    cb: Callable[[DiscoveryEvent], None] = lambda e: calls.append(e.name)  # noqa: E731
    cb(DiscoveryEvent("x", 1, 1, None, False, Capabilities()))
    assert calls == ["x"]
