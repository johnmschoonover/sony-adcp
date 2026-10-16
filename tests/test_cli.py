"""Smoke tests for the ``python -m pyadcp`` helper against the fake projector."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from pyadcp.__main__ import amain

if TYPE_CHECKING:
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
