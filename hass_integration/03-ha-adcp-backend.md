# Prompt 03: `sony_projector` gets an ADCP backend (pyadcp), chosen by probe

You are contributing to Home Assistant core, working in the user's fork of
`home-assistant/core`. Target branch: `dev`. The documentation change goes to
the user's fork of `home-assistant/home-assistant.io`.

## Background

- `sony_projector` now has a config flow, a YAML import, a per-entry
  coordinator, a thin backend interface, and a power `switch`. These were
  added by the config-flow PR (prompt 02b). The only backend so far is
  SDCP, through `pysdcp-extended` (PJ Talk, TCP 53484, synchronous, run in
  the executor). Its broad `except Exception` lives only in the SDCP
  adapter. The user is the code owner. Read the current integration code
  first.
- Both backends are **permanent**:
  - ADCP, through pyadcp, for every projector that speaks it.
  - SDCP, through pysdcp-extended, for older projectors.

  The user does not maintain pysdcp-extended. Do not plan upstream changes
  to it.
- Sony projectors from 2016 on (VW and XW series) speak ADCP (TCP 53595, a
  text protocol, SHA-256 challenge auth with the default password
  `Projector`). There, ADCP is a strict superset of SDCP, and PJ Talk is off by
  default and missing from Sony's current manuals. Pre-2016 lamp models speak
  only SDCP.
- `pyadcp` (PyPI; source `johnmschoonover/sony-adcp`) is an asyncio-native,
  MIT, fully typed ADCP client. Read its `README.md` and
  `docs/integration-guide.md` before designing anything. The key API:
  - `probe(host, port=53595, *, timeout=15.0, password=None) -> ProbeResult`
    reads only the connect greeting. `result.outcome` is a `ProbeOutcome`;
    `result.is_adcp` is `True`, `False` or `None` (cannot tell).
  - `Projector(host, port=..., password=...)` is an async context manager.
    It provides `identify()` (model, serial, MAC, firmware; cached),
    `poll_status()` (power, error, warning, timers, signal; five round trips),
    `power_on()` / `power_off()`, and typed getters and setters.
  - Exceptions derive from `AdcpError`. `AuthenticationError` means a wrong
    password; `AdcpConnectionError` and `AdcpTimeoutError` are transport
    problems; `CommandInactiveError` means "not in this power state".
- The library is the protocol layer. If you need protocol behaviour that
  pyadcp lacks, stop and describe it as a pyadcp change. Do not put protocol
  code in Home Assistant; that is a core rule.
- Never copy GPL code (for example kennymc-c's ADCP integration).

Read from GitHub raw (`home-assistant/developers.home-assistant`, `docs/`):
the config flow handler docs, the reauth and reconfigure docs, the
`DataUpdateCoordinator` docs (`integration_fetching_data.md`), and the
bronze and silver quality-scale rules.

## Scope of this PR

Add ADCP as a second backend, chosen at setup. Keep the entity set the same
as today (the power switch), plus device info from `identify()`. Settings
entities come in the next PR.

1. **Backend interface.** Add an ADCP implementation of the existing
   interface, using native async pyadcp. Extend the interface only as far as
   this PR needs:
   - identity (`DeviceInfo` fields);
   - a richer state object if the coordinator data is still a bare `bool`.
     Turn it into a small dataclass that both backends fill.

   Map pyadcp's typed exceptions to the integration's own exceptions. The
   ADCP adapter needs **no** broad `except`.
2. **Config flow routing.** Follow the "Choosing a backend in a config flow"
   section of pyadcp's integration guide exactly:
   - Call `probe(host)` first.
   - `NO_AUTH`: ADCP with no password.
   - `AUTH_REQUIRED`: show a password step, pre-filled with the factory
     default (`pyadcp.DEFAULT_PASSWORD`), then `probe(host, password=...)`.
   - `AUTH_FAILED`: show an `invalid_auth` error on the password step.
     **Never fall back to SDCP.**
   - `HOST_DENIED`: abort with a translated message telling the user to
     allow Home Assistant's IP in the projector's ADCP settings.
   - `AUTH_UNCONFIRMED`, `TIMEOUT`, `CLOSED` or `UNREACHABLE`
     (`is_adcp is None`): report `cannot_connect` and let the user retry. Do
     not silently fall back, because a sleeping unit can look like this.
   - `NOT_ADCP` or `REFUSED` (`is_adcp is False`): try SDCP. If that
     connects, create an SDCP entry. If not, show `cannot_connect` with a
     description pointing to the PJ Talk prerequisites.
   - Store the backend (`adcp` or `sdcp`), port and password in the entry.
     Do not probe again on every start.
   - For ADCP entries, the `unique_id` is the serial number from
     `identify()`, with `_abort_if_unique_id_configured(updates={CONF_HOST: host})`
     so an IP change updates the existing entry.
3. **Existing entries.** Entries created by the previous PR are SDCP. Add
   a config entry version/minor-version migration that sets
   `backend: "sdcp"` on them. Do not re-probe them automatically.
   Switching an old entry to ADCP is done through **reconfigure** (step 6).
4. **Coordinator.** Reuse the existing per-entry coordinator.
   - ADCP: `poll_status()` on the update interval. Map `AdcpConnectionError`
     and `AdcpTimeoutError` to `UpdateFailed`. Map `AuthenticationError` to
     `ConfigEntryAuthFailed`, which starts reauth.
   - SDCP: unchanged.
   - Log an unavailable projector once and its recovery once, without
     flooding the log (silver `log-when-unavailable`).
5. **Reauth flow** (`async_step_reauth` / `reauth_confirm`) for ADCP
   entries. It asks for the password and validates with `probe(..., password=...)`.
6. **Reconfigure flow** (`async_step_reconfigure`) lets the user change the
   host or port. It re-probes and may switch the entry's backend from SDCP
   to ADCP, because the user asked. Keep the `unique_id` stable, or update it
   deliberately with a test.
7. **Power switch on both backends.** The entity ID and behaviour are
   unchanged. For ADCP, map `PowerStatus` (standby, startup, on, cooling1,
   cooling2) sensibly. `is_on` is true for startup and on. Expose the raw
   status as an attribute only if the current switch already exposes
   attributes. Otherwise the enum sensor comes in the next PR.
8. **Device info.** For ADCP: manufacturer Sony, model from `modelname`,
   serial number, `sw_version` from the firmware versions, and connections
   from the MAC address. For SDCP: whatever is available.
9. **Manifest.** `requirements: ["pysdcp-extended==<current pin>", "pyadcp==X.Y.Z"]`.
   Use the latest pyadcp release (1.0.0 or later); check PyPI. Add `"pyadcp"` to `loggers`. Run
   `python -m script.gen_requirements_all`.
10. **Quality scale.** Update `quality_scale.yaml`. Reauth, the
    unavailable logging, `config-entry-unloading` and `parallel-updates`
    should move toward silver. Do not claim rules you have not met.
11. **Tests.** Mock `pyadcp.probe` and `pyadcp.Projector` at the
    integration's import path. Keep 100% config-flow coverage across every
    `ProbeOutcome` branch, including the rule that `AUTH_FAILED` never creates an
    SDCP entry. Also test reauth, reconfigure (including SDCP to ADCP), the
    migration, coordinator error mapping, and that SDCP entries behave
    exactly as before.

## Documentation PR (home-assistant.io, `next` branch)

Update `source/_integrations/sony_projector.markdown`:

- **Description.** Say that the integration supports 2016+ models over ADCP
  and older models over SDCP (PJ Talk), selected automatically during setup.
- **Prerequisites, split by protocol.** For ADCP, the default password
  `Projector` is the web admin password. Mention the projector's allowed-host
  setting if it is used. To control the projector from standby, set "Standby
  mode" to Standard or "Network management" to On. For SDCP, enable "Start
  PJ Talk Service" and add the Home Assistant IP as a host address.
- **Configuration parameters.** Describe host, password and port.
- **Reconfigure.** Explain changing the host, and moving an older entry to
  ADCP.
- **Troubleshooting.** Cover a wrong password (a reauth notification),
  no response from standby, and the allow-list.
- **Data updates.** State the polling interval and what one poll reads (the
  gold `docs-data-update` rule).
- **Known limitations.** SDCP projectors get power control only.
- Do not mention future features, and add no deprecation notices.

## PR conventions

- Use the core PR template. Type of change: "New feature". Link the docs PR.
- One-sentence title, for example
  `Add ADCP support to Sony Projector using pyadcp`.
- No AI model identifiers in commits or PR text.

## Done when

- A new 2016+ projector sets up over ADCP with no manual choice. An older one
  falls back to SDCP. A wrong password never falls back.
- Existing entries keep working unchanged and can be moved to ADCP through
  reconfigure.
- hassfest, ruff, mypy and the tests pass, with 100% config-flow coverage.
- Your report notes every place where pyadcp behaviour was assumed rather
  than checked on hardware.
