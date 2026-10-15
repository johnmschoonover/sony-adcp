"""Command-line helper for trying pyadcp against a real projector.

Examples::

    python -m pyadcp 192.168.1.50 status
    python -m pyadcp 192.168.1.50 capabilities
    python -m pyadcp 192.168.1.50 send 'picture_mode ?'
    python -m pyadcp 192.168.1.50 send 'power "on"'
    python -m pyadcp discover --timeout 35

The password defaults to the factory value and can be given with
``--password`` or the ``PYADCP_PASSWORD`` environment variable.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import logging
import os
import sys
from typing import TYPE_CHECKING

from pyadcp.connection import DEFAULT_PASSWORD, DEFAULT_PORT
from pyadcp.exceptions import AdcpError
from pyadcp.projector import Projector
from pyadcp.sdap import SDAP_PORT, discover

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["amain", "main"]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pyadcp", description=__doc__.split("\n\n")[0])
    parser.add_argument("-v", "--verbose", action="store_true", help="log every line exchanged")
    sub = parser.add_subparsers(dest="command", required=True)

    def host_parser(name: str, help_text: str) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_text)
        p.add_argument("host", help="projector address")
        p.add_argument("--port", type=int, default=DEFAULT_PORT)
        p.add_argument(
            "--password",
            default=os.environ.get("PYADCP_PASSWORD", DEFAULT_PASSWORD),
            help="ADCP password (default: factory value or $PYADCP_PASSWORD)",
        )
        p.add_argument("--timeout", type=float, default=5.0, help="command timeout in seconds")
        return p

    host_parser("status", "print poll_status() as JSON")
    host_parser("capabilities", "probe every command with '? --info' and print the result")
    send = host_parser("send", "send raw command lines and print each reply")
    send.add_argument("lines", nargs="+", help="command lines, e.g. 'power_status ?'")

    disc = sub.add_parser("discover", help="listen for SDAP advertisements")
    disc.add_argument("--timeout", type=float, default=35.0)
    disc.add_argument("--port", type=int, default=SDAP_PORT)
    return parser


def _dump(value: object) -> str:
    return json.dumps(value, indent=2, default=_jsonable)


def _jsonable(value: object) -> object:
    """Make dataclasses, read-only mappings, sets and bytes JSON-serialisable."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        # Shallow, unlike dataclasses.asdict, so read-only mappings inside survive.
        return {f.name: getattr(value, f.name) for f in dataclasses.fields(value)}
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, frozenset | set):
        return sorted(value)
    if hasattr(value, "items"):
        return dict(value.items())
    return str(value)


async def _run(args: argparse.Namespace) -> int:
    if args.command == "discover":
        packets = await discover(args.timeout, port=args.port)
        print(_dump(packets))
        return 0

    projector = Projector(
        args.host, args.port, args.password, command_timeout=args.timeout, wake_timeout=args.timeout
    )
    async with projector:
        if args.command == "status":
            print(_dump(await projector.poll_status()))
        elif args.command == "capabilities":
            caps = await projector.capabilities()
            print(_dump({"supported": caps.commands, "unsupported": caps.unsupported}))
        else:
            for line in args.lines:
                try:
                    reply = await projector.send(line)
                except AdcpError as exc:
                    print(f"{line}\n  -> {exc}")
                else:
                    print(f"{line}\n  -> {'ok' if reply is None else json.dumps(reply)}")
    return 0


async def amain(argv: Sequence[str] | None = None) -> int:
    """Parse ``argv`` and run the requested action inside the current event loop."""
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    try:
        return await _run(args)
    except AdcpError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for ``python -m pyadcp``."""
    try:
        return asyncio.run(amain(argv))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
