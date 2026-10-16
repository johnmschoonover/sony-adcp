# Working on pyadcp as an AI agent

Read this before changing anything. `CLAUDE.md` holds the short project
rules; this file explains the design boundaries so changes land in the right
place.

## What this library is

An asyncio-native, MIT-licensed Python client for Sony projectors' ADCP
protocol, written clean-room from Sony's two protocol manuals. Its first
consumer is a Home Assistant integration that lives in a separate
repository. The library is the protocol layer; the integration is the
client layer.

## The boundary between library and client

The deciding question for any new behaviour: **would a second, non-Home-
Assistant consumer want it too?** The command line helper, a script and
another automation platform are the test cases. If yes, it belongs here. If
it only makes sense in terms of config entries, coordinators or entity
platforms, it belongs in the integration and must not be added here.

Library side (already present, extend here):

- Wire protocol: handshake, framing, reconnect, standby retries, timeouts.
- Typed exceptions for every projector error reply.
- The command table and the rendering of every command form.
- Capability discovery, its cache, per-suffix detail, the serialisation
  format (`Capabilities.to_dict` / `from_dict`, versioned by
  `CAPABILITIES_FORMAT`), the change comparison (`changes_since`) and
  progress events (`discover_capabilities`, the `progress` callback).
- Status polling (`poll_status`) and cached identity (`identify`).
- SDAP discovery.

Client side (never add here):

- When discovery runs and when it is repeated.
- Where a capabilities snapshot is stored and when it is invalidated.
- Mapping capabilities to entity types, names, icons or device classes.
- Entity lifecycle, polling cadence, availability, unique IDs.
- Any import of `homeassistant`.

`docs/integration-guide.md` is the human-facing version of this table with
the reasoning and a suggested client flow. Keep the two in step.

## Hard rules

- Clean-room: the only protocol sources are the two Sony manuals (not in
  the repo) and `docs/protocol.md`, which is derived from them in our own
  words. Never consult or copy GPL code. Never commit the PDFs.
- No per-model command tables in `src/`. Support comes from the projector at
  runtime via `? --info` and `? --range`. The fake projector's model
  profiles are test fixtures, not library data.
- No blocking sockets. Everything is asyncio.
- Full type hints; `mypy --strict` and ruff must pass.
- Nothing may assume the VPL-VW295ES even though it is the primary test
  target.

## Protocol invariants to preserve

- One command in flight at a time, serialised by the connection lock.
- A socket that may still owe a reply is never reused: timeouts, protocol
  errors and cancellation drop the session.
- After a write, only queries are resent on a lost connection; a set or
  exec may already have been applied.
- The first command after a connect may be retried (silence or `err_cmd`)
  because of the standby wake behaviour in the manual.
- `err_cmd` to a probe means unsupported; `err_option` means that suffix
  does not exist; `err_inactive` means supported but ask again later;
  anything else means supported with unknown detail. Discovery never aborts
  on one odd command.

## How to work

```sh
uv sync
uv run ruff check . && uv run ruff format .
uv run mypy
uv run pytest -q -W error
uv build
```

All four must pass before a commit. Tests run only against
`tests/fake_projector.py`; if a behaviour cannot be tested there, extend the
fake rather than skipping the test. When a change depends on something the
manual leaves open, add it to `docs/hardware-checklist.md` so it gets
verified on a real unit.

Keep the public surface documented: `README.md` for users,
`docs/integration-guide.md` for client authors, `docs/protocol.md` for the
wire format. Update them in the same change as the code.
