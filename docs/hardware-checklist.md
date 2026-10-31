# Hardware verification checklist

Everything in pyadcp was written from Sony's manuals and tested only against
`tests/fake_projector.py`. The items below could not be confirmed without a
real unit. Work through them against the VPL-VW295ES (or any other ADCP
projector) and record the outcome; each one either confirms a guess or tells
us what to change.

Quick tools:

```sh
uv run python -m pyadcp status HOST
uv run python -m pyadcp capabilities HOST > caps.json
uv run python -m pyadcp -v send HOST 'power_status ?' 'picture_mode ? --info'
uv run python -m pyadcp discover --timeout 35
uv run python -m pyadcp probe HOST
```

`-v` logs every line exchanged with timestamps. `--password` or
`PYADCP_PASSWORD` sets the password; `--timeout` is the per-command reply
timeout and `--wake-timeout` (10 s) the one for the first command after
connecting.

The results below come from one run against a VPL-VW295ES (firmware 2.000)
on 2026-10-31, every command sent through the CLI with `-v`. Identity
values (serial, MAC, addresses, location) are not reproduced here.

## Connection and handshake

- [x] The projector greets with a nonce line and accepts the SHA-256 digest of
      nonce + password; `poll_status` works with the factory password.
      Result (VW295ES, firmware 2.000, 2026-10-31): greeting `052bee14` (8 hex digits, different per
      connection), digest accepted with `OK`; `poll_status` returned every
      field. The unit has a
      non-default password, so the factory value itself was not exercised.
- [x] Setting authentication off on the web page makes the greeting `NOKEY`
      and commands work without a password.
      Result (VW295ES, firmware 2.000, 2026-10-31): greeting `NOKEY`, `probe`
      reports `no_auth` in 2 ms whether or not a password is given, and
      `status` and `send` work with the default password left in place
      (`auth=False`, no digest sent).
- [x] A wrong password yields `err_auth` and `AuthenticationError`.
      Result (VW295ES, firmware 2.000, 2026-10-31): wrong password gave `err_auth` after the digest; the CLI
      reported "projector rejected the password" and exited 1.
- [x] After more than the projector's connection time-out (60 s default) of
      silence on one session, the next command reconnects transparently:
      `-v send HOST --pause 70 'power_status ?' 'power_status ?'` should log
      a second "connecting" and still answer the second line.
      Result (VW295ES, firmware 2.000, 2026-10-31): second "connecting" logged 70 s after the first, both lines
      answered `"standby"`, exit 0.
- [x] With "Standby mode" = Low (or "Network management" = Off), the first
      command after a wake is retried once after a silent period and still
      succeeds. The manual says VW units stay silent up to 10 s;
      `wake_timeout` defaults to 10 s. Note how long the real unit takes.
      Result (VW295ES, firmware 2.000, 2026-10-31): this model's menu has no "Standby mode"
      item; the equivalent is "Network management", and "Remote start"
      appears in the menu only while it is Off. With Network management =
      Off the unit does not go silent-then-answer: it leaves the network
      entirely about 135 s after `power "off"`, as it reaches standby (no
      ARP reply, no ping, no TCP connect, no SDAP). `probe` reports
      `unreachable` with "Host is down", "No route to host" or a connect
      time-out, and keeps doing so for as long as it was watched (6 min).
      The manual's silent first command was therefore never seen and the
      `wake_timeout` retry was never exercised. A Wake-on-LAN magic packet
      to the MAC from `identify()` (Remote start = On) powered the unit
      fully on: ADCP answered 19 s and 21 s after the packet, `power_status`
      was already `on` on the first command (122 ms, then 4 ms), and the
      lamp was lit. In one of the two cycles the port answered `refused`
      from 13 s to 16 s before the greeting appeared. With Network
      management = On (the setting as found, restored afterwards) the first
      command in standby took 5 ms every time. `network_mgmt ?` and
      `remote_start ?` answer `err_inactive` in standby and while cooling;
      `remote_start ?` also answers `err_inactive` while on unless Network
      management is Off.
- [x] Reply latency: note typical `-v` timings so `command_timeout` (5 s) can
      be tuned.
      Result (VW295ES, firmware 2.000, 2026-10-31): 4 to 7 ms per query on a wired LAN, in standby and on; the
      first query after a set took about 450 ms (`dynamic_range` set then
      query). Power transitions did not delay replies.
      `command_timeout` of 5 s has a wide margin.

## Probe (greeting only)

`uv run python -m pyadcp probe HOST` prints the outcome and `elapsed`;
add `--password ...` to check a password as well.

- [x] Is the greeting itself delayed in standby? The manual documents up to
      10 s of silence for the *first command* after a wake on VW, HW and GTZ
      units, but says nothing about the greeting. With "Standby mode" = Low
      (or "Network management" = Off) and the unit in standby, run `probe`
      several times and note `elapsed`. If it ever approaches 15 s, raise
      `DEFAULT_PROBE_TIMEOUT`; if it exceeds 5 s, `AdcpConnection`'s
      `connect_timeout` (5 s, which covers the greeting too) must rise as
      well.
      Result (VW295ES, firmware 2.000, 2026-10-31): with Network management = On the greeting arrived 2 ms
      after the connect in standby, every time. With Network management =
      Off there is no greeting to delay: the connect itself fails (see the
      wake item above). While the network board boots after a Wake-on-LAN
      packet the port was refused for a few seconds, then the greeting came
      within 60 ms of the connect. `DEFAULT_PROBE_TIMEOUT` and
      `connect_timeout` need no change.
- [x] Factory settings: `probe HOST` reports `auth_required`; with
      `--password Projector` it reports `auth_ok`, with a wrong password
      `auth_failed`. Authentication off: `no_auth`.
      Result (VW295ES, firmware 2.000, 2026-10-31): `auth_required` with no password, `auth_ok` with the unit's
      (non-factory) password, `auth_failed` with a wrong one, `elapsed`
      0.002 to 0.004 s; `refused` against a closed port on this Mac;
      `no_auth` once authentication was switched off on the web page.
- [x] Nonce format: note the greeting's length and letter case (pyadcp
      accepts any run of hex digits; the fake sends 8 lowercase digits).
      Result (VW295ES, firmware 2.000, 2026-10-31): always 8 lowercase hex digits, different on every connection
      (`052bee14`, `db0e84e2`, `a298f7d5`, ...), the same as the fake
- [x] What a host outside the projector's allow-list sees: an `err_auth`
      greeting (`host_denied`), an immediate close (`closed`), a refused
      connection (`refused`) or silence (`timeout`)?
      Result (VW295ES, firmware 2.000, 2026-10-31): an immediate close. With
      one other address in "Host address" on the ADCP web page, the TCP
      connect from this Mac succeeded and the projector closed it within
      2 ms without sending a byte: `probe` -> `closed`, "connection closed
      by projector", with or without a password, on every attempt; `send`
      fails with the same message. No `err_auth` greeting, so
      `host_denied` is not what this model produces. The list covers ADCP
      only: the web page still answered (HTTP 401 challenge) and the SDAP
      advertisement still arrived from the denied host.
- [x] What a second client sees while another ADCP session is open (keep
      `send HOST --pause 30 'power_status ?' 'power_status ?'` running and
      probe meanwhile). If the projector closes or ignores the second
      connection, a busy projector probes as `closed` or `timeout`, which is
      why those outcomes are "unknown" rather than "not ADCP".
      Result (VW295ES, firmware 2.000, 2026-10-31): a second session is accepted while the first is open: both
      got a nonce, `OK` and answers to `power_status ?`, and the first
      still answered afterwards. The unit
      is not single-session.
- [x] After `err_auth`, does the projector close the socket by itself?
      Result (VW295ES, firmware 2.000, 2026-10-31): no. The socket stayed open for 5 s with no further data, and a
      `power_status ?` sent on it was answered `err_auth` again. The client has to close it.
- [ ] An older Sony unit without ADCP: does port 53595 refuse the connection
      (`refused`), or is the port open and silent?
      Not testable here: no such unit available.

## Command forms

- [x] `power "on"`, `power "off"` and `power_status ?` behave as expected and
      the transitional states `startup`, `cooling1`, `cooling2` are seen.
      Result (VW295ES, firmware 2.000, 2026-10-31): `power "on"` -> `ok`; `power_status ?` stayed `"standby"`
      for about 10 s, was `"startup"` at 15 s and `"on"` at 21 s. `power "off"` -> `ok`; `power_status ?`
      was `"on"` immediately, `"cooling1"` at 3 s, `"cooling2"` from 6 s and
      `"standby"` at 128 s. `signal ?` kept reporting `"3840x2160/60p"`
      through cooling and on the first poll in standby.
- [x] `power ?` returns `err_cmd` (the manual says power cannot be queried).
      Result (VW295ES, firmware 2.000, 2026-10-31): `power ?` -> `err_cmd`; `power ? --info` ->
      `{"type":"sys_sel","version":"1.0","range":["on","off"]}` and
      `power ? --range` -> `["on","off"]` both work.
- [x] `picture_mode ? --info` returns an object with `type`, `version` and a
      `range` array; `contrast ? --info` returns `range` as `{min,max}` with
      `step` only when it is not 1.
      Result (VW295ES, firmware 2.000, 2026-10-31): `picture_mode ? --info` ->
      `{"type":"menu_sel","version":"1.0","range":["cinema_film1","cinema_film2","reference","tv","photo","game","brt_cinema","brt_tv","user"]}`;
      `contrast ? --info` -> `{"type":"menu_num","version":"1.0","range":{"min":0,"max":100}}`,
      no `step`. No command on this unit reported a
      step.
- [x] Does `? --info` work while in standby? Run `capabilities` in standby
      and again with the unit on and compare the `incomplete` lists. Any
      command listed there answered `err_inactive` to `? --info`; the
      library re-probes those on its next `capabilities()` call.
      Result (VW295ES, firmware 2.000, 2026-10-31): yes. `? --info` answered for every `menu_*` command in
      standby; `incomplete` was empty in both runs and the supported and
      unsupported sets were identical. Only `?` and
      `? --range` are gated: `picture_mode ?` and `aspect ? --range` were
      `err_inactive` in standby while `menu_pos ?` and `input ?` answered.
      With the unit on but no input signal the picture settings were still
      `err_inactive`; they answered once a 4K source was
      playing.
- [x] `ipv4_ip_address ? --info`: the manual documents no info form for
      `sys_var` commands, so discovery probes them with `? --range`
      instead. Check whether `? --info` answers `err_cmd` or an object.
      Result (VW295ES, firmware 2.000, 2026-10-31): an object:
      `{"type":"sys_var","version":"1.0","min":"1.0.0.0","max":"223.255.255.255"}`
      with the string bounds at the top level; `? --range` gives
      `{"min":"1.0.0.0","max":"223.255.255.255"}`. Discovery now probes `sys_var` with
      `? --info` like everything else.
- [x] An unsupported command (`hdr_tone_mapping ? --info`, XW-only) answers
      `err_cmd`, not `err_option` or `err_inactive`.
      Result (VW295ES, firmware 2.000, 2026-10-31): `hdr_tone_mapping ? --info` -> `err_cmd`, as did
      `light_output_val`, `hdr_auto`, `filter_status`, `temperature`,
      `licenses`, `panel_resolution` and `3d_status`.
- [x] An unsupported suffix (`dynamic_range --hdmi3 ? --info`) answers
      `err_option`.
      Result (VW295ES, firmware 2.000, 2026-10-31): contradicted: `dynamic_range --hdmi3 ? --info` -> `err_cmd`,
      and so does `trigger --port2 ? --info` and a suffixed command sent
      without its suffix (`dynamic_range ? --info`). A suffix on a command
      that takes none (`contrast --hdmi1 ?`) is `err_option`.
      Discovery now drops a suffix on either reply and marks the command
      unsupported only when every suffix fails.
- [x] Suffixed set and query: `dynamic_range --hdmi1 "full"`,
      `dynamic_range --hdmi1 ?`, `hdmi_signal_format --hdmi2 ?`.
      Result (VW295ES, firmware 2.000, 2026-10-31): `dynamic_range --hdmi1 ?` -> `"full"`; set `"auto"` -> `ok`,
      read `"auto"`; set `"full"` -> `ok`, read `"full"`.
      `hdmi_signal_format --hdmi2 ?` -> `"enhanced"`. These work with the unit on and no signal.
- [x] `contrast --rel 1`, `contrast --reset`, `picture_mode_reset`.
      Result (VW295ES, firmware 2.000, 2026-10-31): with contrast at 100: `contrast --rel 1` -> `err_val`,
      `contrast --rel -1` -> `ok` then `99`, `contrast --rel 1` -> `ok` then
      `100`, `contrast 101` -> `err_val`, `contrast --reset` -> `ok` then
      `100` (the preset's default), `contrast 100` restored. `contrast 50.5`
      was accepted and set 50.
      `picture_mode_reset` was not run: it would reset the owner's
      calibration and cannot be undone; `picture_mode_reset ? --info` ->
      `{"type":"menu_exec","version":"1.0"}`.
- [ ] `pic_pos_save` / `pic_pos_del` are modelled as select commands taking a
      slot value (the manual types them `menu_exec` but lists values). Not on
      the VW295ES (manual lens), so this stays open for lens-memory models.
- [ ] `image_shift --h 2` as a bare number (the manual's example quotes it).
      Not on the VW295ES; open for VW5000/VZ1000.
      Result (VW295ES, firmware 2.000, 2026-10-31): `image_shift`, `image_shift_enable`, `pic_pos_save` and
      `pic_pos_del` all `err_cmd`; `pic_pos_sel ? --info`
      is supported with `["1.85_1","2.35_1","custom1","custom2","custom3"]`.
- [x] `key "menu"`, `key "picmode1"`, `key "aspect_2.35_1_zoom"`: confirm
      the key codes with `+`/`-` and dots are accepted as plain ASCII.
      Result (VW295ES, firmware 2.000, 2026-10-31): all `ok`. With a signal, `key "aspect_2.35_1_zoom"` changed
      `aspect ?` from `"normal"` to `"2.35_1_zoom"` (restored);
      `key "picmode1"` changed `picture_mode ?` from `"cinema_film2"` to
      `"brt_tv"`, not `cinema_film1` (restored); `key "picture+"` left
      `contrast ?` at 99 within 0.7 s; `key "3d_depth-"` -> `ok` in 2D.
      Without a signal the same keys answered `ok` and changed nothing. An
      unknown code (`key "nonsense"`) is `err_option`, not `err_val`.
      `key ? --info` works: type `ir_remote`, `range` lists the unit's 84
      codes, including `dec`, `adj_r`, `adj_g`, `adj_b`, `cutoff_r`,
      `wb_gain`, `wb_bias` and `memory`, which the manual's list lacks.
      `capabilities()` now probes `key` and returns the codes as its options.
- [x] Lens keys on a manual-lens unit: `key "lens_focus_far"` presumably
      answers `ok` and does nothing.
      Result (VW295ES, firmware 2.000, 2026-10-31): `key "lens_focus_far"` and `key "lens_shift_up"` -> `ok`, but
      the owner reported the focus test pattern screen appeared, so the key
      is not a no-op: it opens the lens adjustment OSD even though the lens
      is manual.

## Status replies

- [x] `error ?` and `warning ?` return JSON arrays (`["no_err"]`), not bare
      strings.
      Result (VW295ES, firmware 2.000, 2026-10-31): `error ?` -> `["no_err"]`, `warning ?` -> `["no_warn"]`.
- [x] `timer ?` returns `[{"operation":N},{"light_src":N},{"prev_light_src":N}]`
      and `version ?` returns `[{"main":"..."}]`; `poll_status` flattens both.
      Result (VW295ES, firmware 2.000, 2026-10-31): `timer ?` -> `[{"operation":6222},{"light_src":63},{"prev_light_src":2058}]`,
      `version ?` -> `[{"main":"2.000"}]`, flattened to
      `{"operation": 6222, ...}` and `{"main": "2.000"}`. `timer ? --info`
      lists `["--operation","--light_src","--prev_light_src"]` and
      `timer --operation ?` -> `{"operation":6222}`.
- [x] `signal ?` returns `"Invalid"` in standby or with no source, and a
      format string such as `3840x2160/60p` with a source. Check whether it
      answers `err_inactive` in standby instead.
      Result (VW295ES, firmware 2.000, 2026-10-31): `"Invalid"` in standby and when on with no source, never
      `err_inactive`; `"3840x2160/60p"` with a 4K source. The value follows
      the source, not the power state: with the source left on, standby
      also reported `"3840x2160/60p"`.
- [x] `mac_address ?` format (`08-12-34-ab-cd-ef` style per the manual).
      Result (VW295ES, firmware 2.000, 2026-10-31): `"cc-98-8b-00-00-00"`, lowercase hex with dashes.
- [x] `modelname ?` and `serialnum ?` values; compare the serial with SDAP.
      Result (VW295ES, firmware 2.000, 2026-10-31): `"VPL-VW295ES"` and `"1234567"` (a string); SDAP carried the
      same serial, see below.

## Capabilities discovery

- [x] `capabilities` finishes in reasonable time (139 commands, up to 210
      probe lines with suffixes, at 30 to 1000 ms each). Record the wall
      time.
      Result (VW295ES, firmware 2.000, 2026-10-31): 1.33 s wall for 170 probe lines in standby, 1.58 s for 171
      when on (`key` included), process start-up included. 93 commands
      supported, 47 unsupported.
- [x] The result for the VW295ES matches the manual's VW290ES/VW270ES
      column: `lamp_control` supported, `light_output_val` unsupported,
      `hdr_auto` supported, `motionflow` without `impulse`, aspect without
      `full1`/`full2`, `dynamic_range` suffixes `hdmi1`/`hdmi2` only.
      Result (VW295ES, firmware 2.000, 2026-10-31): all as expected except `hdr_auto`, which answered `err_cmd`
      although the column lists it. `lamp_control` `["high","low"]`,
      `motionflow` `["off","smooth_high","smooth_low","true_cinema"]`, aspect
      `["1.85_1_zoom","2.35_1_zoom","stretch","v_stretch","normal","squeeze"]`,
      `dynamic_range` suffixes `hdmi1`, `hdmi2`.
- [x] Option lists from `--info` versus `--range`: confirm `--range` shrinks
      with signal or picture mode (e.g. `aspect ? --range` with no signal).
      Result (VW295ES, firmware 2.000, 2026-10-31): with no signal `aspect ? --range` is `err_inactive`; with a
      3840x2160/60p signal it is
      `["1.85_1_zoom","2.35_1_zoom","v_stretch","normal","squeeze"]`, one
      shorter than `--info` (`stretch` missing), and `aspect "stretch"` then
      answers `err_val`.

## SDAP

- [x] `discover` sees the projector's broadcast within 35 s on the same
      subnet.
      Result (VW295ES, firmware 2.000, 2026-10-31): one packet from 192.168.1.50 (source port 33116) in each 35 s
      window, in standby and on. With the Advertisement service switched off
      on the web page, a 40 s `discover` returned `[]` with exit 0 and no
      error: such a unit is invisible to discovery and must be entered by
      address. Switching off the PJ Talk and DDDP services (with
      Advertisement back on) changed nothing: `discover` still found the
      unit and ADCP still answered, so only the Advertisement service
      matters for discovery. The Advertisement page's "broadcast address"
      list was empty as found (plain subnet broadcast). With this Mac's
      address added, the unit sent a unicast copy to it every 30 s (seen by
      a listener bound to that address, which receives no broadcasts) while
      the broadcast continued: a wildcard listener saw three packets per
      cycle from one source port instead of two. `discover` still reported
      one projector, since it de-duplicates by serial and sender address.
      A unit on another subnet can therefore be discovered by adding the
      client's address there, with no change on the client side. Emptying
      the list again (the web page accepts an empty field) restored the
      two broadcast copies per cycle with no unicast one.
- [x] The 4-byte serial decodes big-endian to the same number as
      `serialnum ?`. If not, the raw bytes are in `serial_raw`; try
      little-endian or BCD.
      Result (VW295ES, firmware 2.000, 2026-10-31): `serial_raw` `0012d687` = 1234567 big-endian, equal to
      `serialnum ?`.
- [x] The 2-byte power status values in standby and on (the manual only
      defines `0xFFFF`); map them once known.
      Result (VW295ES, firmware 2.000, 2026-10-31): `0000` in standby, `0003` on. `0005` throughout cooling2 (cooling1
      lasted 3 s and fell between advertisements); back to `0000` 20 s after
      standby. `startup` was not caught. Every advertisement arrived twice
      from the same source port, which `discover` already de-duplicates. `SdapPacket.power_state` now names these three
      values.
- [x] Product name is the model name as expected; location is the string set
      on the web page.
      Result (VW295ES, firmware 2.000, 2026-10-31): `product_name` `"VPL-VW295ES"`, `location`
      `"Home_Theater"`, `community` `"SONY"`, version 1, category 10.
