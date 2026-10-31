# Prompt 04: `sony_projector` settings entities for automations (ADCP)

You are contributing to Home Assistant core, working in the user's fork of
`home-assistant/core`. Target branch: `dev`. The documentation change goes to
the user's fork of `home-assistant/home-assistant.io`.

## Background

- `sony_projector` has a config flow, YAML import, two backends chosen by
  `pyadcp.probe()` (ADCP for 2016+ models, SDCP for older ones), a
  `DataUpdateCoordinator`, reauth and reconfigure, and one power `switch`.
  Read the current code first.
- `pyadcp` (PyPI; source `johnmschoonover/sony-adcp`) is an asyncio ADCP
  client. Read `README.md` and `docs/integration-guide.md`, especially "A
  suggested client flow" and "What the library will not do". The API used
  here:
  - `await projector.capabilities()` returns a `Capabilities` object.
    `caps.supports(name, suffix=None)`, `caps.options(name, suffix=None)`
    (select values) and `caps.range(name, suffix=None)` (a `NumericRange`
    with min/max/step) come from the projector at runtime through
    `? --info`. `caps.incomplete` lists commands whose details were withheld
    (usually in standby). `caps.changes_since(old)` tells you what to add or
    remove after a re-probe.
  - `caps.to_dict()` / `Capabilities.from_dict()` and
    `projector.restore_capabilities()` persist discovery across restarts.
    A full discovery is about 200 round trips, so it runs once.
  - `get_select` / `set_select`, `get_number` / `set_number`, with an optional
    suffix (`--hdmi1`), plus `poll_status()`.
  - `CommandInactiveError` means the projector is in the wrong state for this
    command (for example standby or no signal). `CommandValueError` and
    `CommandOptionError` mean the value or suffix is not valid right now.
- **No per-model tables.** Never branch on model names. Which entities exist
  is decided by `caps.supports(...)` on the connected unit. A curated mapping
  from *command name* to entity metadata (platform, translation key,
  category, enabled by default) is fine and expected. That is the client's
  job.
- If you need protocol behaviour that pyadcp lacks (for example a batch read
  of many settings), stop and describe it as a pyadcp change rather than
  adding protocol code here.

Before designing, read pyadcp's `docs/hardware-checklist.md`. It records a
run against a real VPL-VW295ES (firmware 2.000, 2026-10-31). The facts that
shape this PR:

- **Power timing.** After `power "on"` the unit reports `standby` for about
  10 s, `startup` at about 15 s, and `on` at about 21 s. After
  `power "off"` it reports `cooling1` at about 3 s, `cooling2` from about
  6 s, and `standby` at about 128 s.
- **Capabilities are complete in standby.** `? --info` answered for every
  command in standby, and `incomplete` was empty both in standby and on. So
  entities can be created at first setup even with the projector asleep.
  Keep the re-probe after power-on for other models, but do not depend on it.
- **Settings need an input signal, not just power.** `picture_mode ?` and
  the picture settings answer `err_inactive` in standby **and** when the unit
  is on with no input signal. They answer once a source is playing. Some
  commands answer without a signal: `input ?` and `menu_pos ?` even in
  standby; `dynamic_range --hdmiN` and `hdmi_signal_format --hdmiN` when on
  with no signal.
- **`signal ?` can be stale.** It returns `"Invalid"` in standby and with no
  source, but kept reporting the last format (`"3840x2160/60p"`) through
  cooling and on the first standby poll. Do not use the signal alone to
  decide availability.
- **Valid options shrink with the signal.** With a 4K signal,
  `aspect ? --range` omitted `stretch`, which `--info` lists, and
  `aspect "stretch"` then answered `err_val`.
- **`pic_pos_sel` exists on the VW295ES**, despite its manual lens, with
  options `1.85_1`, `2.35_1`, `custom1`, `custom2`, `custom3`.
  `pic_pos_save` and `pic_pos_del` answer `err_cmd` there. Whether
  `pic_pos_sel ?` reads back the current position was not recorded.
- **Reads are fast.** 4 to 7 ms per query on a wired LAN. The first query
  after a set took about 450 ms. A full discovery took about 1.5 s. Reading
  every enabled setting each cycle is cheap, so no batch helper is needed.
- **Remote keys have side effects.** `key "picmode1"` selected `brt_tv`,
  not `cinema_film1`. Lens keys on the manual-lens unit open the lens
  adjustment screen. Both are reasons to keep keys optional and disabled.
- **Not yet verified:** whether settings are memorised per input and/or per
  picture mode (`contrast --reset` restoring "the preset's default" hints at
  per-mode storage), `pic_pos_sel ?` readback, and behaviour with "Standby
  mode" = Low.

If an item is still unverified, design for the conservative case and say so
in the PR.

Read from GitHub raw (`home-assistant/developers.home-assistant`, `docs/`):
the entity docs for `select`, `number`, `switch`, `sensor` and
`binary_sensor`; `core/entity.md` (has_entity_name, translation_key,
entity_category, entity_registry_enabled_default); and the quality-scale
rules `entity-translations`, `entity-category`, `entity-device-class`,
`entity-disabled-by-default`, `icon-translations`, `dynamic-devices`,
`stale-devices`, `exception-translations`, `action-exceptions` and
`parallel-updates`.

## The user's goal: automations

The user wants to automate the projector, not drive it like a remote. For
example:

- "When the PS5 is on, switch to the game picture mode and turn input lag
  reduction on."
- "When source X is on, recall picture position 2.35_1 and set brightness 55 and
  contrast 60."

So every useful setting must be an entity that both **reports** its current
value (for triggers and conditions) and **accepts** changes through the
standard actions (`select.select_option`, `number.set_value`,
`switch.turn_on`). Add no custom actions for setting values; the standard
entity actions already cover it. Write-only "fire and forget" settings are a
last resort.

## Entities (ADCP entries only; SDCP entries keep just the power switch)

Create each entity only if `caps.supports(command)` is true on that unit. If
a command is supported but `incomplete`, create it once it is completed, as
in step 3 of the Behaviour section.

**Enabled by default (the automation set):**

| Command | Platform | Notes |
| --- | --- | --- |
| `input` | select | Options from capabilities. |
| `picture_mode` | select | |
| `pic_pos_sel` | select | "Picture position" (includes the lens on motorised-lens models). Exists on the VW295ES. If it cannot be read back, `current_option` is `None`, and the docs explain why. |
| `aspect` | select | |
| `hdr` | select | |
| `lamp_control`, `light_output_dyn`, `iris_dyn_cont` | select | Whichever the unit supports. |
| `light_output_val`, `iris_brightness` | number | Laser and lamp light level. |
| `contrast`, `brightness`, `color`, `hue`, `sharpness` | number | Range from capabilities, `mode: slider` or `box`. |
| `color_temp`, `gamma_correction`, `motionflow`, `contrast_enh` | select | |
| `input_lag_red`, `real_cre`, `blank` | switch | Selects whose options are exactly on/off. Name `blank` "Picture muting". |

**Created but disabled by default** (`entity_registry_enabled_default = False`;
use `EntityCategory.CONFIG` where it fits):

- `dynamic_range` and `hdmi_signal_format` (one entity per HDMI suffix)
- `color_space`, `nr`, `mnr`, `smooth_grd`, `film_mode`, `real_cre_reso`,
  `real_cre_noise`, `hdr_tone_mapping`, `xvcolor`, `clear_white`
- `2d3d_sel`, `3d_format`, `3d_depth`, `sim3d_effect`
- `menu_pos`, `status_disp`, `high_alt_mode`, `powsave_nosig`, `image_flip`,
  `anamorphic_lens`, `trigger --portN`

**Sensors** (from `poll_status()`; `EntityCategory.DIAGNOSTIC` unless noted):

- `power_status` as an enum sensor (standby, startup, on, cooling1,
  cooling2). Not diagnostic, because automations use it.
- `signal`: the current input signal format. Not diagnostic.
- error and warning: an enum sensor or `binary_sensor` with the `problem`
  device class, with the specific code as state or attribute.
- operation and light-source hours, as `duration` with
  `SensorStateClass.TOTAL_INCREASING`.
- `temperature`, if supported.

**Never expose:** network and security settings (`ipv4_*`, `ipv6_*`,
`network_mgmt`, `remote_start`, `web_control_ui`, `setting_lock`,
`ir_receiver`, `remote_id`, `lens_lock`), installation-only commands
(`panel_align*`, `blend*`, `corner_keystone*`, `color_matching*`, `blanking*`,
`image_shift*`, `screen_fitting_reset`, `language`), and destructive or
maintenance ones (`pic_pos_save`, `pic_pos_del`, `complete_maintenance`,
`activate_licenses`, `*_reset`, `test_pattern`). A user who needs these can
use the projector's menu. Mention this in the PR so reviewers see it was a
choice.

Every entity uses `has_entity_name` and a `translation_key`. Every select
option gets a state translation in `strings.json`. Take the known values
from pyadcp's command table and docs. A value the projector reports that is
not translated must still work; check how core handles untranslated
options. Add icons through `icons.json`.

## Behaviour

1. **Discovery and persistence.**
   - On first setup, run `capabilities()` and store `caps.to_dict()` in the
     config entry data or a `Store`; pick one and justify it.
   - On later starts, `restore_capabilities()` from the stored copy, then run
     `capabilities()`, which only re-probes what was incomplete.
   - If `from_dict` raises `ValueError` (the format changed), discover again.
2. **Availability, per entity, from what the projector says.**
   - Do not hard-code which settings need a signal. Read each enabled
     setting on every poll. An entity is available when its last read
     returned a value, and unavailable when it returned
     `CommandInactiveError` (`err_inactive`).
   - This handles the measured cases (standby, on with no signal, on with a
     signal) and other models' gating without a table.
   - Outside `on`, mark settings unavailable without reading them. That
     saves round trips, and some answers are stale outside `on`.
   - While `power_status` is `standby`, `cooling1` or `cooling2`, expose
     the `signal` sensor as unknown, because its reply can be stale.
   - Sensors and the power switch stay available whenever the projector
     answers.
3. **After power on**, if `caps.incomplete` is not empty, call
   `capabilities()` again, compute `changes_since`, and add new entities
   dynamically. Store the new copy. This is the `dynamic-devices` pattern
   applied to entities.
4. **Polling.**
   - Each cycle runs `poll_status()`.
   - While the projector is on, read the values of **enabled** settings
     entities only.
   - Keep one connection and respect pyadcp's lock.
   - At the measured 4 to 7 ms per query, reading every enabled setting
     each cycle is cheap. Pick the interval for responsiveness and document
     it.
   - Allow for the roughly 450 ms first query after a set.
5. **After any set,** request a coordinator refresh of all settings, not
   just the one written. Changing `input` or `picture_mode` changes other
   values (per-input and per-mode memory), and the UI and automations must
   see the new values.
6. **Errors from actions.**
   - Map `CommandInactiveError` to `HomeAssistantError` with a translated
     message such as "The projector must be on to change this setting".
   - Map `CommandValueError` / `CommandOptionError` to
     `ServiceValidationError` with a translated message ("not available for
     the current signal or mode").
   - Use `translation_domain` and `translation_key` (the
     `exception-translations` rule).
   - Never queue a setting to apply later. That hides failures from
     automations.
7. Set **`PARALLEL_UPDATES`** per platform. Writes to one projector should
   be serialised, and pyadcp's connection lock serialises them anyway.
8. **Diagnostics** (`diagnostics.py`): config entry (password redacted),
   identity, the capabilities snapshot and the last status.

## Optional, in a separate later PR: remote keys

The user is not opposed but it is not a priority. Add a `remote` entity
whose `send_command` accepts key names from `caps.options("key")`, with
power on/off mapped to the projector's power. Disable it by default.
Prepare this only if asked.

## Documentation PR (home-assistant.io, `next` branch)

Update `source/_integrations/sony_projector.markdown`:

- **Supported functionality.** List the entities by platform, saying which
  are enabled by default. Explain that the set depends on what the connected
  projector reports, so it varies by model. List the settings deliberately
  not exposed and why.
- **Data updates.** Give the polling interval, what is read when the
  projector is on versus in standby, and the refresh after every change.
- **Known limitations.**
  - Picture settings are unavailable in standby, while warming up, **and
    while the projector is on with no input signal**. They become available
    once a source is playing. Power-on takes about 20 s before the projector
    reports on. Power-off then cooling takes about 2 minutes, during which
    the projector cannot be turned back on.
  - Sony stores picture settings per input and per picture mode (state this
    only if the hardware pass has confirmed it; otherwise say "may store"),
    so changing the input or picture mode can change other values.
  - Some options are valid only for certain signals (for example, `stretch`
    aspect is rejected with a 4K signal).
  - Picture position (`pic_pos_sel`) recalls a stored position. On
    motorised-lens models this includes the lens. If the projector cannot
    report the current position, the select's state shows as unknown.
  - SDCP (older) projectors get power control only.
- **Examples.** These matter most for this integration. Include ready-to-use
  YAML for:
  1. **PS5 on → game mode**, through the "apply look" script in the
     appendix.
  2. **Source X on → picture position and brightness/contrast**, through
     the same script. Explain the order and the two waits: power `on` before
     selecting the input, then the picture-mode entity becoming available,
     which means the source is sending a signal, before any picture setting.
     Explain that a scene is not recommended because it does not guarantee
     the order.
  3. Turn the projector on, wait until it reports on, then switch input.
  4. Notify when the projector reports a warning or error.

  Use placeholder entity IDs and say how to find the real ones.
- **Troubleshooting.** A setting can't be changed (projector not on, or the
  option isn't valid for the current signal). An entity is missing (not
  supported by this model, or disabled by default).
- **Removal instructions.** Keep the existing section.

## PR conventions

- This adds several platforms. Ask the user whether core reviewers prefer
  one platform per PR. Splitting into select+number, switch, then sensors,
  each with its docs section, is usually faster to review. Propose a split
  and let the user decide.
- Use the core PR template and link the docs PR. One-sentence titles, for
  example `Add select and number entities to Sony Projector`.
- No AI model identifiers in commits or PR text.

## Done when

- On a 2016+ projector, the automation-set entities appear, show current
  values, accept changes through standard actions, and refresh dependent
  values after every change.
- Nothing branches on model names, and unsupported commands create no
  entities.
- hassfest, ruff, mypy and the tests pass. Coverage includes availability
  gating, error translation, the refresh after a write, dynamic addition
  after power-on, and snapshot persistence and restore.
- The docs include the automation and script examples.

## Appendix: starting YAML for the docs Examples section

Start from these and adapt them. Replace the placeholder entity IDs with the
IDs the integration actually creates. Verify the syntax against the current
automation and script docs. Keep the explanation that the script applies
settings in order (power, input, picture mode, picture position, numbers
and switches) because of Sony's per-input and per-picture-mode memory, and that
this is why a script is used instead of a scene.

```yaml
script:
  projector_apply_look:
    alias: "Projector: apply look"
    mode: queued
    fields:
      input:               {required: true,  example: hdmi2,  selector: {text: {}}}
      picture_mode:        {required: true,  example: game,   selector: {text: {}}}
      picture_position:    {required: false, selector: {text: {}}}
      brightness:          {required: false, selector: {number: {min: 0, max: 100}}}
      contrast:            {required: false, selector: {number: {min: 0, max: 100}}}
      input_lag_reduction: {required: false, selector: {boolean: {}}}
    sequence:
      # A projector that is cooling down ignores power-on; cooling takes ~2 min.
      - wait_template: "{{ not states('sensor.projector_power_status').startswith('cooling') }}"
        timeout: "00:03:00"
        continue_on_timeout: false
      - if:
          - condition: state
            entity_id: switch.projector
            state: "off"
        then:
          - action: switch.turn_on
            target: {entity_id: switch.projector}
      # Power-on takes ~20 s to report "on".
      - wait_template: "{{ is_state('sensor.projector_power_status', 'on') }}"
        timeout: "00:02:00"
        continue_on_timeout: false
      - action: select.select_option
        target: {entity_id: select.projector_input}
        data: {option: "{{ input }}"}
      # Picture settings answer only once the source is sending a signal, so
      # "on" is not enough: wait for the picture-mode entity to be available.
      - wait_template: "{{ states('select.projector_picture_mode') not in ['unavailable', 'unknown'] }}"
        timeout: "00:01:00"
        continue_on_timeout: false
      - action: select.select_option
        target: {entity_id: select.projector_picture_mode}
        data: {option: "{{ picture_mode }}"}
      - if: "{{ picture_position is defined }}"
        then:
          - action: select.select_option
            target: {entity_id: select.projector_picture_position}
            data: {option: "{{ picture_position }}"}
      - if: "{{ brightness is defined }}"
        then:
          - action: number.set_value
            target: {entity_id: number.projector_brightness}
            data: {value: "{{ brightness }}"}
      - if: "{{ contrast is defined }}"
        then:
          - action: number.set_value
            target: {entity_id: number.projector_contrast}
            data: {value: "{{ contrast }}"}
      - if: "{{ input_lag_reduction is defined }}"
        then:
          - action: "switch.turn_{{ 'on' if input_lag_reduction else 'off' }}"
            target: {entity_id: switch.projector_input_lag_reduction}

automation:
  - alias: "PS5 on: projector game look"
    triggers:
      - trigger: state
        entity_id: media_player.ps5
        from: "off"
        not_to: ["unavailable", "unknown"]
    actions:
      - action: script.projector_apply_look
        data: {input: hdmi2, picture_mode: game, input_lag_reduction: true}

  - alias: "Movie source on: projector cinema look"
    triggers:
      - trigger: state
        entity_id: media_player.apple_tv
        from: "off"
    actions:
      - action: script.projector_apply_look
        data:
          input: hdmi1
          picture_mode: cinema_film1
          picture_position: "2.35_1"   # VW295ES offers 1.85_1, 2.35_1, custom1-3
          brightness: 55
          contrast: 60
          input_lag_reduction: false

  - alias: "Projector reports a problem"
    triggers:
      - trigger: state
        entity_id: binary_sensor.projector_problem
        to: "on"
    actions:
      - action: notify.notify
        data:
          message: "Projector: {{ state_attr('binary_sensor.projector_problem', 'code') }}"
```

Notes for the docs text:

- Option values (`game`, `cinema_film1`, `hdmi2`) are the raw values the
  projector reports. Tell users to copy them from the select entity's
  options. Automations use the raw values, not the translated labels.
- Picture position is optional. On motorised-lens models it also moves the
  lens. Say that the field can be left out.
- Every wait has a timeout and stops the script instead of sending settings
  the projector would reject. The last wait also catches a source that never
  sends a signal.
- If the entities prompt 04 builds differ from these (for example, the
  problem sensor is an enum sensor rather than a binary sensor), update the
  examples to match the real entities.
