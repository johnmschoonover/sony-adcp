# Prompt 01: get pyadcp ready for Home Assistant core

> **Status (2026-10-31): mostly done on `main`.** pyadcp 1.0.0 is on PyPI.
> It was published by `.github/workflows/publish.yml` (tag-triggered,
> trusted publishing; PR #15). The licence, licence files and project URLs
> are in `pyproject.toml`, and release steps are under "Releasing" in
> `README.md`. The hardware checklist was finished in PR #10; only items
> needing other models remain.
>
> **Still open:** the four automation checks in Part A (per-input and
> per-picture-mode memory, `pic_pos_sel ?` readback, per-command signal
> gating for query and set, and setting the input during `startup`), plus
> confirming in Part C that the GitHub issue tracker is enabled. Treat
> Parts B and C as an audit of what exists, not as new work.

Work in `johnmschoonover/sony-adcp` (the `pyadcp` library). Read `CLAUDE.md`
and `AGENTS.md` first and follow them: clean-room from Sony's two manuals,
asyncio only, `mypy --strict` + ruff, tests only against
`tests/fake_projector.py`, no per-model tables, no Home Assistant code. CI
already exists in `.github/workflows/ci.yml` (lint, test matrix 3.12–3.15,
build with a `dist` artifact, uv pinned in `env.UV_VERSION`).

The library's first consumer is the Home Assistant `sony_projector`
integration. That integration will be used mainly for **automations**: set
picture mode, picture position (`pic_pos_sel`), brightness and contrast, input lag
reduction, HDR and so on when other devices change state. It needs every one
of those settings to be both readable and writable. Treat that as the priority
when deciding what to verify.

This prompt has three parts. Do them as separate PRs in this order. Each PR
title is one sentence in the style of earlier PRs, with no semicolons.

## Part A: finish the hardware pass (needs the real projector)

The main pass is done. `docs/hardware-checklist.md` records a run against a
VPL-VW295ES (firmware 2.000, 2026-10-31), and the library was fixed where the
unit contradicted it (PR #8). Only the remaining open items are left. This
part works only from a machine on the same LAN as the projector. If the
session cannot reach it, stop and say so. Do not fake results.

Open items that the Home Assistant automation work depends on. Add any that
are missing to the checklist, then record the results there:

1. **Per-input and per-picture-mode memory.** With a source playing:
   - Note `contrast ?` and `brightness ?`.
   - Change `picture_mode`, read them again, change back, and read again.
   - Do the same for `input` (two sources needed).
   - Record whether values are stored per picture mode, per input, or both.

   The example automations order their steps on this assumption.
2. **`pic_pos_sel ?` readback.** `pic_pos_sel ? --info` is supported on the
   VW295ES with `1.85_1`, `2.35_1` and `custom1` to `custom3`. Does the
   plain query return the current position, and does a set change it? What
   does it answer with no signal?
3. **Signal-gating per command.** For each setting in the automation set
   (`input`, `picture_mode`, `aspect`, `hdr`, `pic_pos_sel`, `lamp_control`,
   `contrast`, `brightness`, `color`, `hue`, `sharpness`, `color_temp`,
   `gamma_correction`, `motionflow`, `input_lag_red`, `real_cre`,
   `contrast_enh`), record what a **query** and a **set** return in three
   states: standby, on with no signal, and on with a signal. The checklist
   already says picture settings answer `err_inactive` without a signal,
   while `input`, `menu_pos`, `dynamic_range` and `hdmi_signal_format`
   answer without one. Fill in the rest.
4. **Settable during `startup`?** Does `input "hdmi2"` succeed between
   `power "on"` and the `on` state, or only once the unit reports `on`?
5. **Still open from the checklist:** the authentication-off `NOKEY`
   greeting, "Standby mode" = Low wake behaviour, the standby greeting delay
   for `probe()`, and what a host outside the allow-list sees. Do these only
   if the owner is willing to change those projector settings, and restore
   them afterwards.

When the hardware contradicts the library or the fake projector, fix both in
the same PR, with tests. Update `docs/protocol.md` where needed. The manuals
still win: record any disagreement instead of silently changing documented
behaviour. No batch-read helper is needed: queries measured 4 to 7 ms each.

## Part B: publish to PyPI with trusted publishing

1. Add `.github/workflows/release.yml`:
   - Trigger: push of tags matching `v*`.
   - Build exactly like the CI `build` job: same pinned uv from
     `env.UV_VERSION`, `uv build`. Before building, fail if the tag (without
     `v`) does not equal `project.version` in `pyproject.toml` and
     `pyadcp.__version__`.
   - Run the full test suite once before publishing.
   - Publish with `pypa/gh-action-pypi-publish` using PyPI trusted publishing
     (OIDC): a job with `permissions: id-token: write`, a GitHub environment
     named `pypi`, and no API token. Keep the build and publish jobs separate,
     passing `dist/` as an artifact so the publish job runs no project code.
   - Pin every action to a full commit SHA with a `# vX.Y.Z` comment. Resolve
     SHAs with `git ls-remote`; never guess them.
   - Optionally create a GitHub Release for the tag with the built files.
2. Add `CHANGELOG.md` (Keep a Changelog style) with a `0.1.0` entry.
3. Document the release procedure in `README.md` under Development: bump the
   version in both places, update the changelog, merge, then tag `vX.Y.Z` on
   `main` and push the tag.
4. The user must do two things by hand. List them at the end of your report
   and do not attempt them:
   - On pypi.org, add a pending trusted publisher for project `pyadcp`:
     owner `johnmschoonover`, repo `sony-adcp`, workflow `release.yml`,
     environment `pypi`.
   - In the GitHub repo settings, create the `pypi` environment, optionally
     with required reviewers.
5. Do not push a tag yourself.

## Part C: Home Assistant library requirements

Check each item against the current Home Assistant docs (read them from
GitHub raw: `docs/api_lib_index.md` and
`docs/core/integration-quality-scale/rules/dependency-transparency.md` in
`home-assistant/developers.home-assistant`) and fix what is missing:

- An OSI licence (MIT) in the package metadata (`license` /
  `license-files` in `pyproject.toml`), visible on the built wheel's METADATA.
- A source distribution is published, not only wheels.
- PyPI versions correspond to tagged releases, published automatically from
  public CI (Part B).
- The issue tracker is enabled. Tell the user if it is not; you cannot change
  repo settings.
- `project.urls` with Homepage, Source, Issues and Changelog.
- `py.typed` is shipped (it already exists; confirm it is in the wheel).
- Optionally, an issue template chooser that links Home Assistant
  integration bugs to `home-assistant/core` issues, as the HA docs suggest.

## Done when

- The open checklist items are recorded (Part A), or you have said clearly
  that the projector was unreachable.
- The release workflow passes actionlint, and a dry run (build + version check
  on a branch, without the publish job) has been shown working.
- `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`,
  `uv run pytest -q -W error` and `uv build` all pass.
- Your report lists the manual steps for the user and the facts from the
  hardware pass that prompts 03 and 04 depend on: per-input memory, standby
  behaviour, `pic_pos_sel` readback, and whether settings can be changed during startup.
