# Hardware verification checklist

Everything in pyadcp was written from Sony's manuals and tested only against
`tests/fake_projector.py`. The items below could not be confirmed without a
real unit. Work through them against the VPL-VW295ES (or any other ADCP
projector) and record the outcome; each one either confirms a guess or tells
us what to change.

Quick tools:

```sh
poetry run python -m pyadcp HOST status
poetry run python -m pyadcp HOST capabilities > caps.json
poetry run python -m pyadcp -v HOST send 'power_status ?' 'picture_mode ? --info'
poetry run python -m pyadcp discover --timeout 35
```

## Connection and handshake

- [ ] The projector greets with a nonce line and accepts the SHA-256 digest of
      nonce + password; `poll_status` works with the factory password.
- [ ] Setting authentication off on the web page makes the greeting `NOKEY`
      and commands work without a password.
- [ ] A wrong password yields `err_auth` and `AuthenticationError`.
- [ ] After more than the projector's connection time-out (60 s default) of
      silence, the next command reconnects transparently (run `send` with a
      long `--timeout`, wait, run again; or watch `-v` logs for "session
      lost").
- [ ] With "Standby mode" = Low (or "Network management" = Off), the first
      command after a wake is retried once after a silent period and still
      succeeds. The manual says VW units stay silent up to 10 s;
      `wake_timeout` defaults to 10 s. Note how long the real unit takes.
- [ ] Reply latency: note typical `-v` timings so `command_timeout` (5 s) can
      be tuned.

## Command forms

- [ ] `power "on"`, `power "off"` and `power_status ?` behave as expected and
      the transitional states `startup`, `cooling1`, `cooling2` are seen.
- [ ] `power ?` returns `err_cmd` (the manual says power cannot be queried).
- [ ] `picture_mode ? --info` returns an object with `type`, `version` and a
      `range` array; `contrast ? --info` returns `range` as `{min,max}` with
      `step` only when it is not 1.
- [ ] Does `? --info` work while in standby? If a `menu_*` command answers
      `err_inactive`, `capabilities()` lists it in `incomplete` and probes
      it again on the next call; check `incomplete` is empty after running
      `capabilities` once with the unit on.
- [ ] An unsupported command (`hdr_tone_mapping ? --info`, XW-only) answers
      `err_cmd`, not `err_option` or `err_inactive`.
- [ ] An unsupported suffix (`dynamic_range --hdmi3 ? --info`) answers
      `err_option`.
- [ ] Suffixed set and query: `dynamic_range --hdmi1 "full"`,
      `dynamic_range --hdmi1 ?`, `hdmi_signal_format --hdmi2 ?`.
- [ ] `contrast --rel 1`, `contrast --reset`, `picture_mode_reset`.
- [ ] `pic_pos_save` / `pic_pos_del` are modelled as select commands taking a
      slot value (the manual types them `menu_exec` but lists values). Not on
      the VW295ES (manual lens), so this stays open for lens-memory models.
- [ ] `image_shift --h 2` as a bare number (the manual's example quotes it).
      Not on the VW295ES; open for VW5000/VZ1000.
- [ ] `key "menu"`, `key "picmode1"`, `key "aspect_2.35_1_zoom"`: confirm
      the key codes with `+`/`-` and dots are accepted as plain ASCII.
- [ ] Lens keys on a manual-lens unit: `key "lens_focus_far"` presumably
      answers `ok` and does nothing.

## Status replies

- [ ] `error ?` and `warning ?` return JSON arrays (`["no_err"]`), not bare
      strings.
- [ ] `timer ?` returns `[{"operation":N},{"light_src":N},{"prev_light_src":N}]`
      and `version ?` returns `[{"main":"..."}]`; `poll_status` flattens both.
- [ ] `signal ?` returns `"Invalid"` in standby or with no source, and a
      format string such as `3840x2160/60p` with a source. Check whether it
      answers `err_inactive` in standby instead.
- [ ] `mac_address ?` format (`08-12-34-ab-cd-ef` style per the manual).
- [ ] `modelname ?` and `serialnum ?` values; compare the serial with SDAP.

## Capabilities discovery

- [ ] `capabilities` finishes in reasonable time (about 170 probes at 30 to
      1000 ms each). Record the wall time.
- [ ] The result for the VW295ES matches the manual's VW290ES/VW270ES
      column: `lamp_control` supported, `light_output_val` unsupported,
      `hdr_auto` supported, `motionflow` without `impulse`, aspect without
      `full1`/`full2`, `dynamic_range` suffixes `hdmi1`/`hdmi2` only.
- [ ] Option lists from `--info` versus `--range`: confirm `--range` shrinks
      with signal or picture mode (e.g. `aspect ? --range` with no signal).

## SDAP

- [ ] `discover` sees the projector's broadcast within 35 s on the same
      subnet.
- [ ] The 4-byte serial decodes big-endian to the same number as
      `serialnum ?`. If not, the raw bytes are in `serial_raw`; try
      little-endian or BCD.
- [ ] The 2-byte power status values in standby and on (the manual only
      defines `0xFFFF`); map them once known.
- [ ] Product name is the model name as expected; location is the string set
      on the web page.
