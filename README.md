# pyadcp

[![CI](https://github.com/johnmschoonover/sony-adcp/actions/workflows/ci.yml/badge.svg)](https://github.com/johnmschoonover/sony-adcp/actions/workflows/ci.yml)

Asyncio-native Python library for Sony projectors' ADCP protocol. Built as the
backend for a Home Assistant integration; covers the VW and XW series and every
other model that speaks ADCP.

pyadcp talks to Sony's Advanced Display Control Protocol, the text-based
network control interface on Sony VPL home cinema projectors from 2016 onward.
It handles the authentication handshake, command framing and error mapping, and
exposes typed getters and setters for power, input, picture settings, HDR, lamp
control, status, warnings, timers and remote key emulation. Capabilities are
discovered from the projector at runtime, so option lists and numeric ranges
match the connected model without per-model tables. Written clean-room from
Sony's published protocol manuals. MIT licensed.

## Install

```sh
pip install pyadcp
```

Python 3.12 or newer. No runtime dependencies.

## Usage

```python
import asyncio

from pyadcp import Projector


async def main() -> None:
    async with Projector("192.168.1.50", password="Projector") as projector:
        await projector.power_on()

        status = await projector.poll_status()
        print(status.power_status, status.modelname, status.signal)

        await projector.set_input("hdmi1")
        await projector.set_picture_mode("reference")
        await projector.set_contrast(55)
        await projector.send_key("menu")

        # Ask the projector what it supports instead of hard-coding a model.
        caps = await projector.capabilities()
        print(caps.options("picture_mode"))   # ('cinema_film1', 'reference', ...)
        print(caps.range("contrast"))         # NumericRange(min=0, max=100, step=1)
        print(caps.supports("lamp_control"))  # True on lamp models, False on laser
        print(caps.options("key"))            # the remote key codes this unit accepts
        print(caps.unsupported)               # commands this unit answered err_cmd to


asyncio.run(main())
```

`poll_status()` is what a polling integration calls on every refresh. It reads
power state, errors, warnings, timers and the input signal, and fills in the
model name, serial number, MAC address and firmware versions from a cached
`identify()` so a routine poll is five round trips. A full `capabilities()`
discovery is about 200 round trips and runs once; it is cached, re-probes
anything the projector refused while in standby on the next call, and takes
`names=[...]` to limit it to the commands you care about.

For building a client on top of the library there are a few more pieces:
per-suffix options and ranges (`caps.range("blanking", "top")`),
`caps.to_dict()` / `Capabilities.from_dict()` plus
`projector.restore_capabilities()` to persist discovery across restarts,
`new.changes_since(old)` to learn what to update after a re-probe, and
`projector.discover_capabilities()` (or `capabilities(progress=...)`) for
per-command progress. `docs/integration-guide.md` describes them, together
with what the library deliberately leaves to the client and why.

Lower levels are available when you need them:

```python
from pyadcp import AdcpConnection, get_command

async with AdcpConnection("192.168.1.50") as conn:
    print(await conn.send(get_command("power_status").query()))  # 'standby'
    print(await conn.send('timer ?'))  # [{'operation': 3400}, ...]
```

Check whether a host speaks ADCP, and whether it needs a password, without
opening a session or sending a command:

```python
from pyadcp import ProbeOutcome, probe

result = await probe("192.168.1.50")              # reads the greeting only
print(result.outcome, result.is_adcp)             # ProbeOutcome.AUTH_REQUIRED True

result = await probe("192.168.1.50", password="Projector")
if result.outcome is ProbeOutcome.AUTH_FAILED:    # ADCP, wrong password
    ...
```

Expected outcomes (refused, unreachable, timed out, closed, not ADCP, host
denied, no auth, auth required, password accepted, rejected or unconfirmed)
come back on the result rather than as exceptions; a rejected password is
always reported as ADCP. The default timeout is 15 s because a unit in
low-power standby can be slow to answer. `docs/integration-guide.md` lists
every outcome and how a config flow should route on them.

Find projectors on the local network through their SDAP broadcasts:

```python
from pyadcp import discover

for packet in await discover(timeout=35):
    print(packet.product_name, packet.serial, packet.address)
```

## Command line

A small helper is included for trying things against a real unit:

```sh
python -m pyadcp status 192.168.1.50
python -m pyadcp capabilities 192.168.1.50
python -m pyadcp -v send 192.168.1.50 'picture_mode ?' 'contrast ? --range'
python -m pyadcp discover
python -m pyadcp probe 192.168.1.50 --password Projector
```

`--password` (or `PYADCP_PASSWORD`), `--port`, `--timeout` and
`--wake-timeout` apply to the first three; `-v` logs every line exchanged
with a timestamp. `send` exits with status 1 if any line was answered with an
error. `probe` prints its result as JSON, checks a password only when one is
given (it does not assume the factory value), and exits with status 0 only
for `no_auth`, `auth_required` and `auth_ok`.

## Errors

Every `err_*` reply becomes a typed exception: `CommandFormatError`
(unsupported or malformed command), `CommandOptionError`, `CommandInactiveError`
(for example a picture setting while in standby), `CommandValueError`,
`Internal1Error` and `Internal2Error`. Transport problems raise
`AdcpConnectionError`, `AuthenticationError` or `AdcpTimeoutError`. All of them
derive from `AdcpError`, which is the one to catch in a polling loop. A
connection lost after a *set* command was written is reported rather than
retried, because the projector may already have acted on it; queries are
resent once. A mistake on the caller's side (unknown command name, wrong value
type, bad suffix) raises `UnknownCommandError` or `CommandFormError` before
anything is sent.

## Projector setup

ADCP is on by default with authentication enabled and the password
`Projector`, the same as the web administrator password. To control the
projector while it is in standby, set "Standby mode" to Standard or "Network
management" to On in the projector menu.

Picture settings (`picture_mode`, `contrast`, `aspect`, `hdr`, ...) answer
`err_inactive` not only in standby but also while the projector is on with
no input signal, so a `CommandInactiveError` on one of them usually means
"no source", not a fault. `docs/hardware-checklist.md` records what a
VPL-VW295ES confirmed and the raw logs live in `docs/hardware/`.

## Development

```sh
uv sync
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run pytest
```

Tests run against `tests/fake_projector.py`, an asyncio server that speaks ADCP
as the manuals describe it, so CI needs no hardware. `docs/protocol.md` is a
derived description of the protocol.
