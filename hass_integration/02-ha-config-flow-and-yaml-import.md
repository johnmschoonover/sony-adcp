# Prompt 02: `sony_projector` config flow and YAML import (no behaviour change)

You are contributing to Home Assistant core, working in the user's fork of
`home-assistant/core`. Target branch: `dev`. The documentation change goes to
the user's fork of `home-assistant/home-assistant.io`.

## Background

- The built-in `sony_projector` integration
  (`homeassistant/components/sony_projector/`) is YAML-only and has one
  `switch` platform for power. Its manifest has `requirements:
  ["pySDCP==1"]`, `iot_class: local_polling`, `quality_scale: legacy`,
  `loggers: ["pysdcp"]` and **no codeowners**. pySDCP is synchronous and
  speaks Sony's SDCP / PJ Talk protocol (TCP 53484).
- This is the first PR in a series. Later PRs add a second backend
  (`pyadcp`, Sony's ADCP protocol for 2016+ models) and settings entities
  for automations. This PR must not add either. It only moves the existing
  behaviour to a config entry.
- ADR-0010 (`home-assistant/architecture`, `adr/0010-integration-configuration.md`):
  integrations that talk to devices are configured through the UI. Existing
  YAML integrations are encouraged to add a config flow, and changes to
  their YAML configuration are no longer accepted.
- The deprecation rules (`docs/deprecating.md` in
  `home-assistant/developers.home-assistant`) set a minimum of 6 months for
  user-facing removals. To drop YAML: add a config flow and an import flow,
  and raise the core `deprecated_yaml` issue under `HOMEASSISTANT_DOMAIN`
  with `issue_domain=DOMAIN`, `breaks_in_ha_version` at least 6 months
  out, and severity `WARNING`. Raise it **only after the import succeeded**.
  If the import aborts, raise your own `ERROR` issue instead. After the
  period, `CONFIG_SCHEMA` becomes `cv.config_entry_only_config_schema(DOMAIN)`.
  That later step is a separate PR, not this one.

Read these from GitHub raw before writing code, because the
`developers.home-assistant.io` site may not resolve here:
`docs/deprecating.md`, `docs/config_entries_config_flow_handler.md`,
`docs/core/integration-quality-scale/rules/*.md` (bronze rules at least), and
`docs/development_checklist.md`. Then read the current `sony_projector`
source and tests in core. pySDCP is MIT and can be read freely. Do not copy
code from any GPL project (for example kennymc-c's Unfolded Circle Sony
integrations); Home Assistant is Apache-2.0.

## What to build

1. **Config flow** (`config_flow.py`, `strings.json`). There is a user step
   asking for host and an optional name. It validates by connecting with
   pySDCP in the executor (`hass.async_add_executor_job`; pySDCP blocks),
   for example a power-status read. Errors: `cannot_connect` and `unknown`,
   with translations.
2. **Uniqueness.** Use a stable hardware ID as `unique_id` if pySDCP can
   provide one cheaply (check what it exposes; SDAP discovery takes about
   30 s, which is too slow for a flow). Otherwise prevent duplicates with
   `self._async_abort_entries_match({CONF_HOST: host})` and set no
   `unique_id`. Do not use the IP address as `unique_id`.
3. **Import flow** (`async_step_import`). It imports each YAML `switch`
   platform entry. It must abort, without creating a duplicate, if the host is
   already configured. Raise `deprecated_yaml` only after a successful
   import; on failure, raise a translated `ERROR` issue (follow the pattern
   in `docs/deprecating.md`).
4. **Setup through the config entry.** `async_setup_entry` and
   `async_unload_entry`. Store the client in `entry.runtime_data` with a
   typed `ConfigEntry` alias. Raise `ConfigEntryNotReady` when the
   projector is unreachable at startup.
5. **Same switch, same behaviour.** Keep the `switch` entity working exactly
   as before: the same on/off semantics and attributes. Use
   `_attr_has_entity_name = True` with a device (`DeviceInfo`).
   **Preserve existing entity IDs**: an imported YAML switch must keep the
   `entity_id` it had (for example `switch.<yaml name>`) so the user's
   automations do not break. Check how the legacy platform named entities,
   and add a test that proves the entity ID survives import.
6. **Polling** stays at the current cadence. A `DataUpdateCoordinator` is
   fine if it makes the next PRs easier, but keep the diff small.
7. **Manifest.** Add `config_flow: true`, `integration_type: "device"`, and
   the user as a code owner (GitHub handle: `johnmschoonover`; confirm with
   the user). Keep `pySDCP==1`.
8. **Quality scale.** Add `quality_scale.yaml`. Mark the bronze rules done,
   or exempt with a reason. Check hassfest's quality-scale exemption list (in
   `script/hassfest/quality_scale.py`) and remove `sony_projector` from it if
   adding the file requires that. Aim for bronze. Do not claim tiers you have
   not met.
9. **Tests** in `tests/components/sony_projector/`. Cover 100% of
   `config_flow.py`: user step success, `cannot_connect`, `unknown`,
   duplicate abort, and import success, abort and failure, including the repair
   issues created, checked through the `issue_registry` fixture. Cover setup,
   unload and not-ready. Mock pySDCP; never touch a network.
10. Run `python -m script.hassfest`, `python -m script.translations develop`
    if needed, `ruff`, `mypy` (if the integration is in `.strict-typing`, keep
    it strict), and `pytest tests/components/sony_projector --cov=homeassistant.components.sony_projector --cov-report=term-missing`.

## Documentation PR (home-assistant.io)

Edit `source/_integrations/sony_projector.markdown`. New features target the
`next` branch. Check the current contributing guide for the branch rule and
the PR template, and link the core PR in the docs PR.

- Front matter: add `ha_config_flow: true` and `ha_codeowners`. Leave
  `ha_quality_scale` for the bot or tooling if it is generated; check how
  other integrations do it.
- Replace the YAML configuration section with the standard UI setup include
  (`{% include integrations/config_flow.md %}`) and describe the fields
  (host, name) in the configuration-parameters style other integrations use.
- Keep the projector-side prerequisites: enable "Start PJ Talk Service" in the
  projector's web UI, and add the Home Assistant IP to the host address list.
- Add removal instructions (the bronze `docs-removal-instructions` rule),
  using the shared include if one exists.
- Do **not** add a deprecation notice for YAML. The deprecation docs say the
  repair issue and the release notes announce it, and the integration page
  describes only what currently works.
- Keep the high-level description accurate: this release still controls only
  power over SDCP.

## PR conventions

- Use the core PR template. Type of change: "New feature" (config flow).
  Fill in the "Breaking change" section if anything user-visible changes; the
  goal is that nothing does.
- One-sentence title, for example `Add config flow to Sony Projector`.
- Commit messages and PR bodies must not contain AI model identifiers.

## Done when

- Existing YAML users are imported with unchanged entity IDs and see one
  `deprecated_yaml` warning. New users can set up from the UI.
- hassfest, ruff, mypy and the tests pass, with 100% config-flow coverage.
- The docs PR is ready and links the core PR.
- Your report lists anything a reviewer is likely to question, such as the
  unique ID choice and the entity ID preservation approach.
