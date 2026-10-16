# pyadcp project rules

- Clean-room implementation written from Sony's two protocol manuals only:
  "Protocol Manual (COMMON)" 9-932-751-53 and "Protocol Manual (Supported
  Command List)" 9-932-761-52. Never consult or copy GPL code. The manuals win
  over `docs/HANDOFF.md` when they disagree. Do not commit the PDFs.
- MIT licensed. Asyncio-native: no blocking sockets anywhere.
- Full type hints; `mypy --strict` and `ruff` must pass. Tests run against
  `tests/fake_projector.py`, never against hardware, so CI needs no projector.
- No per-model command tables. Capabilities come from the projector at runtime
  via `? --info` and `? --range`. The primary test target is a VPL-VW295ES, but
  nothing may assume that model.
- No Home Assistant code in this repository.
- Tooling: uv (hatchling build backend), Python 3.12+, ruff, mypy, pytest,
  pytest-asyncio. Run everything through `uv run`; keep `uv.lock` committed.
- The library/client boundary and the design invariants are in `AGENTS.md`;
  read it before adding behaviour. Client-side concerns (entity mapping,
  storage, lifecycle) belong in the Home Assistant integration, not here.
