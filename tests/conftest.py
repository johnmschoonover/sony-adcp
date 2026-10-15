"""Shared fixtures: a running fake projector and a client connected to it."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from pyadcp.connection import AdcpConnection
from tests.fake_projector import FakeProjector

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

PASSWORD = "Projector"


@pytest.fixture
async def fake_factory() -> AsyncIterator[Callable[..., FakeProjector]]:
    """Build and start fake projectors with custom options; all are stopped on teardown."""
    started: list[FakeProjector] = []

    def build(**kwargs: Any) -> FakeProjector:
        projector = FakeProjector(**kwargs)
        started.append(projector)
        return projector

    yield build
    for projector in started:
        await projector.stop()


@pytest.fixture
async def fake() -> AsyncIterator[FakeProjector]:
    """A started fake projector that requires the default password."""
    async with FakeProjector(PASSWORD) as projector:
        yield projector


@pytest.fixture
async def fake_nokey() -> AsyncIterator[FakeProjector]:
    """A started fake projector with authentication switched off."""
    async with FakeProjector(None) as projector:
        yield projector


def make_connection(port: int, password: str = PASSWORD, **overrides: Any) -> AdcpConnection:
    """An :class:`AdcpConnection` to a local fake with short test-friendly timeouts."""
    options: dict[str, Any] = {
        "connect_timeout": 2,
        "command_timeout": 2,
        "wake_timeout": 0.5,
        "wake_retry_delay": 0.05,
    }
    options.update(overrides)
    return AdcpConnection("127.0.0.1", port, password, **options)


@pytest.fixture
async def connection(fake: FakeProjector) -> AsyncIterator[AdcpConnection]:
    """A not-yet-connected client for ``fake``, closed on teardown."""
    conn = make_connection(fake.port)
    yield conn
    await conn.close()
