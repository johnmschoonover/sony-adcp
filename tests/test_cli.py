"""Smoke tests for the ``python -m pyadcp`` helper against the fake projector."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from pyadcp.__main__ import amain

if TYPE_CHECKING:
    from collections.abc import Callable

    import pytest

    from tests.fake_projector import FakeProjector


async def test_status(fake: FakeProjector, capsys: pytest.CaptureFixture[str]) -> None:
    assert await amain(["status", "127.0.0.1", "--port", str(fake.port)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["power_status"] == "standby"
    assert out["modelname"]


async def test_send(fake: FakeProjector, capsys: pytest.CaptureFixture[str]) -> None:
    argv = ["send", "127.0.0.1", "--port", str(fake.port), 'power "on"', "power_status ?"]
    assert await amain(argv) == 0
    out = capsys.readouterr().out
    assert "-> ok" in out
    assert '-> "on"' in out


async def test_send_reports_errors_per_line(
    fake: FakeProjector, capsys: pytest.CaptureFixture[str]
) -> None:
    argv = ["send", "127.0.0.1", "--port", str(fake.port), "bogus ?", "power_status ?"]
    assert await amain(argv) == 1
    out = capsys.readouterr().out
    assert "err_cmd" in out
    assert '-> "standby"' in out


async def test_send_pause_keeps_one_session(
    fake: FakeProjector, capsys: pytest.CaptureFixture[str]
) -> None:
    lines = ["power_status ?", "modelname ?"]
    argv = ["send", "127.0.0.1", "--port", str(fake.port), "--pause", "0.05", *lines]
    assert await amain(argv) == 0
    assert fake.connections == 1
    assert fake.received == lines


async def test_capabilities(fake: FakeProjector, capsys: pytest.CaptureFixture[str]) -> None:
    assert await amain(["capabilities", "127.0.0.1", "--port", str(fake.port)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert "picture_mode" in out["supported"]
    assert "hdr_tone_mapping" in out["unsupported"]
    assert out["incomplete"] == []


async def test_bad_password_is_an_error(
    fake: FakeProjector, capsys: pytest.CaptureFixture[str]
) -> None:
    argv = ["status", "127.0.0.1", "--port", str(fake.port), "--password", "nope"]
    assert await amain(argv) == 1
    assert "rejected the password" in capsys.readouterr().err


async def test_capabilities_verbose_prints_progress(
    fake: FakeProjector, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await amain(["-v", "capabilities", "127.0.0.1", "--port", str(fake.port)]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["supported"]
    assert "] contrast: ok" in captured.err
    assert "] hdr_tone_mapping: unsupported" in captured.err


async def test_probe_nokey(fake_nokey: FakeProjector, capsys: pytest.CaptureFixture[str]) -> None:
    assert await amain(["probe", "127.0.0.1", "--port", str(fake_nokey.port)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["outcome"] == "no_auth"
    assert out["is_adcp"] is True
    assert out["auth_required"] is False
    assert fake_nokey.received == []


async def test_probe_without_password_does_not_assume_factory_default(
    fake: FakeProjector, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PYADCP_PASSWORD", raising=False)
    assert await amain(["probe", "127.0.0.1", "--port", str(fake.port)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["outcome"] == "auth_required"
    assert out["password_accepted"] is None
    assert fake.handshake_received == []


async def test_probe_password(fake: FakeProjector, capsys: pytest.CaptureFixture[str]) -> None:
    argv = ["probe", "127.0.0.1", "--port", str(fake.port), "--password", "Projector"]
    assert await amain(argv) == 0
    assert json.loads(capsys.readouterr().out)["outcome"] == "auth_ok"


async def test_probe_wrong_password_exits_1(
    fake: FakeProjector, capsys: pytest.CaptureFixture[str]
) -> None:
    argv = ["probe", "127.0.0.1", "--port", str(fake.port), "--password", "nope"]
    assert await amain(argv) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["outcome"] == "auth_failed"
    assert out["is_adcp"] is True


async def test_probe_silent_port_times_out(
    fake_factory: Callable[..., FakeProjector], capsys: pytest.CaptureFixture[str]
) -> None:
    fake = fake_factory(greeting_behaviour="silent")
    await fake.start()
    argv = ["probe", "127.0.0.1", "--port", str(fake.port), "--timeout", "0.2"]
    assert await amain(argv) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["outcome"] == "timeout"
    assert out["is_adcp"] is None
