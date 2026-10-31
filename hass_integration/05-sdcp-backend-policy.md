# Prompt 05: the SDCP backend long term (keep it, on pysdcp-extended)

You are helping the code owner of Home Assistant's `sony_projector`
integration (the user, GitHub `johnmschoonover`). Work in the user's forks of
`home-assistant/core` (target branch `dev`) and
`home-assistant/home-assistant.io`.

## Decision already made

- `sony_projector` keeps **two permanent backends**, chosen at setup by
  `pyadcp.probe()`:
  - **ADCP**, through `pyadcp` (the user's library), for every projector that
    speaks it, with the full set of settings entities.
  - **SDCP / PJ Talk**, through `pysdcp-extended`, for older projectors that
    don't.
- pySDCP was replaced by pysdcp-extended (prompt 02a) because pySDCP fails
  the bronze `dependency-transparency` rule and pysdcp-extended passes it.
- **The user does not maintain pysdcp-extended and will not contribute to
  it.** An earlier typed-exceptions PR there was closed on purpose. So:
  - Never plan work that depends on changes upstream.
  - Keep the library's bare `except Exception` confined to the single
    wrapper in the SDCP adapter.
  - Never call private library methods (`_send_command` and similar) and
    never put SDCP protocol code into Home Assistant. Core forbids protocol
    code in integrations.
- **Deprecating SDCP is not planned.** It becomes relevant only under the
  contingency below.

Read the current integration code first. The backend interface, coordinator
and SDCP adapter already exist.

## Part A: optional SDCP improvements (only if the user asks)

Older projectors currently get power only. pysdcp-extended's **public**
methods would allow more:

| Library method | Entity |
| --- | --- |
| `get_input` / `set_HDMI_input(1 \| 2)` | select: input (HDMI 1 / HDMI 2) |
| `get_muting` / `set_muting` | switch: picture muting |
| `get_calibration_preset` / `set_calibration_preset` | select: picture mode |
| `get_HDMI_dynamic_range(n)` / `set_HDMI_dynamic_range(n, …)` | select per HDMI input, disabled by default |
| `get_lamp_hours` | sensor: lamp hours (`duration`, total increasing, diagnostic) |

Rules:

- Use the same entity descriptions and translation keys as the ADCP
  entities, so automations look the same on both backends.
- A pre-2016 unit may not support every item. pysdcp-extended raises its
  generic error for an unsupported item. Probe each item once at setup through
  the adapter, and create only the entities that answered.
- Every call is a separate socket. Keep the poll set small, and read settings
  only while the projector is on.
- Do one platform per PR, each with its docs section on home-assistant.io
  (`next` branch): supported functionality, and known limitations for older
  models.

## Part B: watch the dependency (each time you touch the integration)

Re-check that pysdcp-extended still meets `dependency-transparency` and
works on core's current Python:

- **Licence:** OSI licence in the metadata.
- **Distribution:** sdist on PyPI.
- **Public CI:** the `build+publish` run for the pinned tag is public and
  green.
- **Tag match:** the PyPI version matches a git tag.
- **Issues:** the tracker is enabled.
- **Python:** the `Requires-Python` range allows core's version.
- **Warnings:** the library triggers no blocking-call or other core warnings.

Bump the pin only for a release that passes the same checks. The PR
description must link the library diff.

## Part C: contingency (only if Part B fails, or the library breaks for users)

Assess first. Do not write code until the user decides:

1. **What broke**, and whether a pinned older version still works.
2. **Alternatives that keep older projectors working:** another maintained
   MIT/Apache SDCP library that passes `dependency-transparency`. One
   candidate is `sony-projector-protocol` (MIT, async, SDCP and ADCP). It was
   alpha when last checked, so re-check its maturity, CI publishing and
   tags. A library swap needs no deprecation if behaviour and entity IDs stay
   the same.
3. **Deprecation, only if nothing else works.** Follow `docs/deprecating.md`
   (read it from GitHub raw: `home-assistant/developers.home-assistant`):
   - **Agree first.** Draft a proposal for the user to raise with core
     maintainers. Give the reason, the affected models, the alternative (the
     PJLink integration, if their projector supports PJLink), and the
     timeline.
   - **Deprecation PR:**
     - A repair issue per SDCP entry: `deprecated_sdcp_{entry_id}`, severity
       `WARNING`, `breaks_in_ha_version` at least 6 months out, translated.
     - Removed with the entry.
     - Tested with `issue_registry`.
     - Ask the user whether new SDCP setups should be blocked during the
       period.
     - No docs notice; the repair issue and the release notes announce it.
   - **Removal PR, after that version:**
     - Remove the SDCP backend and its requirement.
     - Existing SDCP entries load into an `ERROR` repair issue explaining
       that support ended. They must not crash.
     - PR type "Breaking change".
     - A docs PR removes the SDCP prerequisites.

## Conventions

- One-sentence PR titles. No AI model identifiers in commits or PR text.
- Never shorten a deprecation period, and never remove anything without the
  deprecation PR having shipped first.
