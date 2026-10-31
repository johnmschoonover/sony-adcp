# Prompt 02b: `sony_projector` config flow, YAML import, coordinator and backend interface

You are contributing to Home Assistant core, in the user's fork of
`home-assistant/core`. Target branch: `dev`. The documentation change goes to
the user's fork of `home-assistant/home-assistant.io`.

Prerequisite: the library swap PR (prompt 02a) is merged, so the integration
uses `pysdcp-extended`. If it isn't merged yet, stop and tell the user.

## What exists today (read the code to confirm)

- `homeassistant/components/sony_projector/__init__.py` contains only a
  docstring. Everything is in `switch.py`:
  - a **synchronous** YAML `setup_platform` that checks `get_power()` once
    and creates no entity if that fails;
  - a `SwitchEntity` that polls `get_power()` through `update()` on the
    switch default `SCAN_INTERVAL` (30 s).
- YAML is **platform-level**, under `switch:`, not a `sony_projector:` key:
  ```yaml
  switch:
    - platform: sony_projector
      host: 192.168.1.50
      name: Living Room Projector   # optional, default "Sony Projector"
  ```
- The entity has **no `unique_id`**, so it is not in the entity registry. Its
  entity ID is the slug of `name` (for example `switch.living_room_projector`).
- The manifest has no codeowners and `quality_scale: legacy`. There is no
  `config_flow` and no `tests/components/sony_projector/` folder.
- pysdcp-extended is synchronous and opens a new socket per command (2 s
  timeout). A refused connection raises `OSError`. A timeout raises a bare
  `Exception("Timeout while trying to send command …")`. A projector error
  reply raises a bare `Exception("Received failed status …")`. The serial
  number is only available through `get_pjinfo()`, which waits up to 31 s
  for a UDP broadcast and is too slow for a flow.
- The user does **not** maintain pysdcp-extended and will not upstream
  changes to it. Work with its behaviour as it is.

## Long-term shape (design for it now, build only the SDCP side)

The integration will permanently support two backends:
- **ADCP**, through `pyadcp`, for every projector that speaks ADCP (2016+).
  That comes in the next PR.
- **SDCP**, through `pysdcp-extended`, for older projectors. That is this PR.

So this PR introduces the per-entry coordinator and a thin backend interface.
The next PR then only adds an ADCP implementation.

## Reference reading (GitHub raw)

From `home-assistant/developers.home-assistant` (`docs/…`), because the
`developers.home-assistant.io` site may not resolve:
- `deprecating.md`
- `config_entries_config_flow_handler.md`
- `integration_fetching_data.md`
- `core/integration-quality-scale/rules/*.md`, all bronze rules
- `development_checklist.md`

From `home-assistant/architecture`: `adr/0010-integration-configuration.md`.

Check how current core declares schemas. The existing `switch.py` imports
`probatio`, not `voluptuous`, so follow what core does now.

Do not read or copy GPL code. In particular, kennymc-c's Unfolded Circle
Sony integrations are GPL. pysdcp-extended itself is MIT.

## What to build

1. **Backend interface** (`backend.py`, or similar):
   - A small typed `Protocol` or ABC with these async methods:
     `async_get_state() -> ProjectorState`, `async_set_power(on: bool) -> None`,
     `async_close()`.
   - Integration-level exceptions: `ProjectorConnectionError`, and
     optionally `ProjectorCommandError`.
   - The **SDCP implementation** wraps `pysdcp_extended.Projector`. Every
     library call goes through **one** private helper that runs it in the
     executor (`hass.async_add_executor_job`). That helper is the **only**
     place with `except Exception`: it re-raises as the integration's own
     typed errors, and a comment explains that the library raises bare
     `Exception` for timeouts and projector errors. Keep `OSError` as a
     separate, explicit `except` before it.
   - Nothing outside the adapter catches broad exceptions.
2. **Coordinator** (`coordinator.py`): a `DataUpdateCoordinator[ProjectorState]`
   per entry, with a 30 s interval (unchanged). `ProjectorState` is a small
   frozen dataclass, for now just `is_on: bool`. Later backends add fields
   without changing the coordinator's type. It maps
   `ProjectorConnectionError` to `UpdateFailed`. Logs one message when the
   projector becomes unavailable and one when it recovers; don't spam the log.
3. **Config flow** (`config_flow.py`, `strings.json`):
   - The user step asks for **host only**. Core does not accept name fields
     in config flows; users rename the entry and device in the UI.
   - Validate by calling the backend's `async_get_state()`.
     `ProjectorConnectionError` maps to `cannot_connect`. An unexpected error
     maps to `unknown`, with `_LOGGER.exception`, following core's config
     flow convention.
   - Uniqueness: there is no cheap stable ID, so set **no `unique_id`**.
     Prevent duplicates with
     `self._async_abort_entries_match({CONF_HOST: host})`. Never use the IP as
     `unique_id`.
   - The entry title is `"Sony Projector"` for user-created entries, and the
     YAML `name` for imported ones.
4. **YAML import, from the switch platform**:
   - Replace the synchronous `setup_platform` with `async_setup_platform`.
     It starts an import flow for each YAML block and **adds no entities**:
     ```python
     result = await hass.config_entries.flow.async_init(
         DOMAIN, context={"source": SOURCE_IMPORT},
         data={CONF_HOST: config[CONF_HOST], CONF_NAME: config[CONF_NAME]},
     )
     ```
   - Treat `already_configured` as success. The YAML stays until the user
     deletes it, so the import runs again on every restart.
   - On success, raise the core `deprecated_yaml` issue under
     `HOMEASSISTANT_DOMAIN`, with `issue_domain=DOMAIN`,
     `breaks_in_ha_version` at least 6 months after the release this ships
     in, severity `WARNING`, and the `domain` / `integration_title`
     placeholders.
   - On any other abort (for example `cannot_connect`), raise the
     integration's own `ERROR` issue with a translated message, and create
     nothing. The import is tried again on the next restart. Follow
     `docs/deprecating.md`: raise `deprecated_yaml` **only after a
     successful import**.
   - Several YAML blocks (several projectors) must each import separately.
5. **Setup** (`__init__.py`):
   - `async_setup_entry` creates the backend and the coordinator, then calls
     `await coordinator.async_config_entry_first_refresh()`. That raises
     `ConfigEntryNotReady` if the projector is unreachable, and Home
     Assistant retries automatically. This is an improvement on today, where
     an unreachable projector at startup silently gets no entity.
   - Store `entry.runtime_data` (a typed `ConfigEntry` alias), then forward
     to `Platform.SWITCH`.
   - `async_unload_entry` unloads the platforms and closes the backend.
6. **Switch entity** (`switch.py`, `entity.py`):
   - A `CoordinatorEntity` base with `_attr_has_entity_name = True` and
     `DeviceInfo` (identifiers from `entry.entry_id`, manufacturer "Sony",
     name from the entry title).
   - The switch has `_attr_name = None`, so it takes the device name, and
     `unique_id = entry.entry_id`.
   - **Entity ID preservation:** device name = YAML `name`, so the new
     entity ID is `switch.<slug of name>`, the same as the legacy entity. The
     legacy entity was never in the registry, so nothing collides. Prove this
     with a test.
   - `turn_on` / `turn_off` call the backend. On failure they raise a
     translated `HomeAssistantError` instead of only logging. On success they
     request a coordinator refresh.
   - `PARALLEL_UPDATES = 1` for writes. Coordinator entities don't poll on
     their own.
7. **Manifest:**
   - `config_flow: true`, `integration_type: "device"`.
   - `codeowners: ["@johnmschoonover"]`. Confirm the handle with the user.
   - Keep `pysdcp-extended`. Set `quality_scale` to match what you can
     honestly claim (see the next step).
8. **Quality scale:**
   - Add `quality_scale.yaml` and go through every bronze rule.
     `dependency-transparency` is now satisfied, thanks to prompt 02a.
   - Remove `sony_projector` from the hassfest lists in
     `script/hassfest/quality_scale.py` (`INTEGRATIONS_WITHOUT_QUALITY_SCALE_FILE`,
     and `INTEGRATIONS_WITHOUT_SCALE` if applicable), as hassfest requires.
   - Claim `bronze` only if every bronze rule is `done` or validly `exempt`.
     Note which silver rules are already met (unavailable logging,
     config-entry unloading, parallel updates, action exceptions).
9. **Tests** (`tests/components/sony_projector/`, all new):
   - `conftest.py` with a mocked `pysdcp_extended.Projector`, a mock config
     entry, and a fixture for setting up the integration.
   - `test_config_flow.py`: **100% coverage**. Cover user success,
     `cannot_connect`, `unknown`, duplicate abort, import success, import
     `already_configured`, and import failure, with the repair issues checked
     through `issue_registry`.
   - `test_init.py`: setup, unload, `ConfigEntryNotReady`.
   - `test_switch.py`: a snapshot test of the entity (syrupy), turning on and
     off, failure raising `HomeAssistantError`, coordinator unavailable and
     recovery, and the **entity ID preservation** test for imported YAML.
   - Never touch the network.
10. **Checks:**
    - `python -m script.hassfest`
    - `python -m script.translations develop` (if needed)
    - ruff, and mypy if the integration is in `.strict-typing`
    - `pytest tests/components/sony_projector --cov=homeassistant.components.sony_projector --cov-report=term-missing`

## Documentation PR (home-assistant.io)

Edit `source/_integrations/sony_projector.markdown`. New features target the
`next` branch. Check the current contributing guide, and link the core PR.

- **Front matter:** add `ha_config_flow: true` and `ha_codeowners`. See how
  other integrations set `ha_quality_scale`, which may be generated.
- **Setup:** replace the YAML configuration section with the standard UI
  setup include (`{% include integrations/config_flow.md %}`). Describe the
  single **Host** parameter in the configuration-parameters style.
- **Prerequisites:** keep them. Enable "Start PJ Talk Service" in the
  projector's web UI, and add the Home Assistant IP to its host address list.
- **Data updates:** state that it polls every 30 seconds.
- **Removal:** add removal instructions, using the shared include if one
  exists (bronze `docs-removal-instructions`).
- **No YAML deprecation notice.** The repair issue and the release notes
  announce it, and the page describes only what currently works.
- Keep the description accurate: this release controls power only.

## PR conventions

- Use the core PR template. Type of change: "New feature".
- If anything user-visible changes, fill in "Breaking change". The goal is
  that nothing does: the YAML keeps working for 6 months and entity IDs are
  preserved.
- One-sentence title, for example `Add config flow to Sony Projector`.
- No AI model identifiers in commits or PR text.

## Done when

- Existing YAML users are imported with unchanged entity IDs and see one
  `deprecated_yaml` warning.
- New users can set the integration up from the UI.
- hassfest, ruff, mypy and the tests pass, with 100% config-flow coverage.
- The backend interface and coordinator are ready for an ADCP implementation
  without restructuring.
- The docs PR is ready and links the core PR.
- Your report lists what reviewers are likely to question: no `unique_id`,
  the entity ID preservation approach, and the single `except Exception` in
  the SDCP adapter.
