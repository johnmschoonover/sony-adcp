# ADCP protocol notes

This is a derived description of Sony's Advanced Display Control Protocol
(ADCP) and the companion SDAP advertisement, written in our own words from the
two Sony protocol manuals (COMMON 9-932-751-53 and Supported Command List
9-932-761-52). It is the reference pyadcp is implemented against. The manuals
themselves are not part of this repository.

## Transport

- TCP, port 53595 by default (changeable on the projector). The same text
  syntax also runs over RS-232C at 38400 bps, 8 data bits, even parity, 1 stop
  bit, no flow control; pyadcp only implements the network transport.
- Everything is US-ASCII. A command is one line ending in CR LF (0x0D 0x0A).
  Every reply is one line ending in CR LF.
- A command may be at most 512 bytes including the terminator. If a line is
  started but not finished within 60 seconds the projector discards it.
- Commands and values are case sensitive.
- Strictly request/response: send one command, wait for its reply, then send
  the next. This holds across controllers too, so a client must serialise.
- The projector closes a session that has been idle for its "connection
  time-out" (60 seconds by default). A client either sends something within
  that window or reconnects when the socket is closed.
- The manual gives a typical reply latency of 30 to 1000 ms. A VPL-VW295ES
  (firmware 2.000) on a wired LAN answered queries in 4 to 7 ms; the first
  query after a set took about 450 ms. A full capability discovery of 170
  probe lines took 1.3 s. Heavy traffic slows the projector.
- When the projector is in standby with the low-power standby mode or with
  network management off, the first command after it wakes may not be answered
  normally. Most series answer `err_cmd` once; the VW, HW and GTZ series stay
  silent for up to 10 seconds. In either case the client resends the command.

## Session start and authentication

Authentication is on by default. The default password is `Projector`, the
same as the web page administrator password. An optional host allow-list on the
projector can restrict which client addresses may connect.

Immediately after the TCP connection opens the projector sends one line:

- `NOKEY` when authentication is off. Commands can be sent straight away.
- Otherwise a random nonce, a short string of hexadecimal digits.

With a nonce the client computes SHA-256 over the nonce string immediately
followed by the password, with no separator, and sends the lowercase 64-digit
hexadecimal digest as a line. The projector replies `OK` (uppercase) on
success, or `err_auth` on failure, after which the session is useless.

Worked check: nonce `1a2b3c4d` and password `password1234` hash to
`283121d4374034199c6f08f1a68d1639bfc8eae257fc2e0ac661d65c7e9f0607`.

The greeting comes unprompted, before the client has sent anything, so it
alone tells a client whether the port speaks ADCP and whether a password is
needed. `pyadcp.probe` relies on this: it reads the greeting, optionally
answers the challenge, and closes without sending a command.

What the manual leaves open, and what pyadcp assumes until it is checked on
hardware (see `docs/hardware-checklist.md`):

- Whether the greeting is also held back while a unit in low-power standby
  wakes, the way the first command can be (up to 10 seconds on VW, HW and GTZ
  units). The probe's default timeout of 15 seconds allows for it.
- The nonce's length. pyadcp accepts any non-empty run of hexadecimal digits
  in either case as a nonce.
- What a client outside the host allow-list sees. pyadcp treats an `err_auth`
  greeting (instead of `NOKEY` or a nonce) as "ADCP, but this host is denied".

## Command syntax

A command is a name, optionally a suffix, then either a value to set or a `?`
to query. Tokens are separated by single spaces.

| Purpose | Line sent | Reply |
|---|---|---|
| Set a choice | `cmd "value"` | `ok` |
| Set a number | `cmd 88` | `ok` |
| Change a number relatively | `cmd --rel 1`, `cmd --rel -1` | `ok` |
| Reset to default | `cmd --reset` | `ok` |
| Execute an action | `cmd` | `ok` |
| Query current value | `cmd ?` | `"value"`, `88`, or JSON |
| Query currently valid choices | `cmd ? --range` | `["a","b"]` or `{"min":0,"max":100,"step":1}` |
| Query command info | `cmd ? --info` | `{"type":"menu_sel","version":"1.0","range":[...]}` |

For commands that address one of several targets (an HDMI input, a screen
edge, a trigger port, a colour channel) the target is a `--suffix` placed
directly after the command name, before the value or `?`:

```
dynamic_range --hdmi1 "full"
dynamic_range --hdmi1 ?
blanking --top 10
trigger --port1 "power"
col_corr_hue --r 20
```

Values are JSON: strings in double quotes, numbers bare, arrays in square
brackets, objects in braces. Replies that carry several items use arrays, and
replies that carry named numbers use an array of single-key objects, for
example `timer ?` returns `[{"operation":3400},{"light_src":2300},
{"prev_light_src":3000}]` and `version ?` returns `[{"main":"1.000"},
{"laser":"1.000"}]`.

`? --range` is "what is valid right now" and can shrink depending on signal,
picture mode or 3D state. `? --info` is "everything this firmware knows" and
is stable, so it is the right source for building option lists once. When the
numeric step is 1 the `step` key is omitted.

## Command types

The info reply names the command's type:

| Type | What it does | Forms |
|---|---|---|
| `sys_sel` | system selection such as `power` | set, `?` (not for `power`, `ipv4_network_setting` or `complete_maintenance`, which answer `err_cmd`), `? --range`, `? --info` |
| `sys_stat` | read-only status such as `power_status`, `error`, `timer` | `?`, `? --info` |
| `sys_var` | network addresses as quoted strings | set, `?`, `? --range`, `? --info` |
| `menu_sel` | OSD choice setting such as `picture_mode` | set, `--reset`, `?`, `? --range`, `? --info` |
| `menu_num` | OSD numeric setting such as `contrast` | set, `--rel`, `--reset`, `?`, `? --range`, `? --info` |
| `menu_exec` | OSD action such as `picture_mode_reset` | execute, `? --info` |
| `key` | remote control emulation: `key "menu"` | set, `? --info` |

Details confirmed on a VW295ES that the manual leaves open:

- `sys_var` commands do answer `? --info`, with string bounds at the top
  level rather than under `range`:
  `{"type":"sys_var","version":"1.0","min":"1.0.0.0","max":"223.255.255.255"}`.
  `? --range` returns the same object without `type` and `version`.
- `key ? --info` works and reports the type as `ir_remote`, not `key`. Its
  `range` lists every key code the unit accepts, which is how a client
  learns the key set without a table. `key ?` and `key ? --range` answer
  `err_option`.
- A `sys_stat` info reply may carry a `range` that lists `--field`
  sub-queries rather than choices: `timer ? --info` gives
  `["--operation","--light_src","--prev_light_src"]` and `version ? --info`
  gives `["--main"]`. `timer --operation ?` then returns `{"operation":6222}`.
  `? --range` on a `sys_stat` command answers `err_option`.
- A `menu_num` set accepts a float and truncates it: `contrast 50.5` sets 50.

The manual also documents expert-only types (`panel_align_zone`,
`user_gamma`, `pattern_sel`, `pattern_pos`) with their own option syntax.
pyadcp does not model them.

## Replies

| Reply | Meaning |
|---|---|
| `ok` | The command was accepted. |
| `err_cmd` | The command is not recognised. Unsupported commands on a given model answer this, and so does a suffixed command whose suffix is missing or not present on the model (`dynamic_range --hdmi3 ? --info` on a two-input unit), an empty line, and a name in the wrong case. |
| `err_option` | The command exists but the line is not a form it takes: a suffix on a command that has none (`contrast --hdmi1 ?`), a query on `key`, `--range` on a status, `--rel` on a selection, a bare `contrast`, a malformed tail (`contrast ? --bogus`, `contrast abc`), or a value token the firmware does not know at all (`picture_mode "nope"`, `key "nonsense"`). |
| `err_inactive` | The command exists but cannot be used right now. In standby this covers most `menu_*` settings; with the unit on but no input signal the picture settings (`picture_mode`, `contrast`, `aspect`, `lamp_control`, `hdr`, ...) still answer this, while `input`, `blank`, `menu_pos`, `dynamic_range` and `hdmi_signal_format` work in both states. |
| `err_val` | The value is a number outside the range (`contrast 101`, `contrast --rel 1` at 100) or a choice the firmware knows but that is not valid right now (`aspect "stretch"` with a 4K signal, when `? --range` omits it). |
| `err_auth` | Authentication failed. Only sent during the handshake. |
| `err_internal1`, `err_internal2` | A communication error inside the projector. |

## Model differences

Which commands, values and suffixes exist differs by model. Rather than carry
per-model tables, pyadcp asks the projector: a command that answers `err_cmd`
to `? --info` is unsupported, a suffix that answers `err_cmd` or `err_option`
is left out (the command is unsupported only when every suffix does), and
the `range` in the info reply gives the option list or the numeric bounds for
that unit. A command that answers `err_inactive` to the info query is kept
as supported with unknown details and asked again later; any other reply is
treated the same way rather than stopping discovery. Every command in the
table is probed with `? --info`, `key` and the `sys_var` commands included.

`? --info` is answered in standby on a VW295ES, for `menu_*` commands too, so
a discovery run there is already complete. The `err_inactive` handling stays
for models that behave differently.

Some practical consequences for a Home Assistant style client:

- In standby most `menu_*` settings answer `err_inactive`, and picture
  settings keep doing so while the unit is on without an input signal. Poll
  only `power_status`, `error`, `warning` and `timer` until the power status
  is `on`, and treat `err_inactive` on a picture setting as "no signal", not
  as a fault.
- `power ?` is not supported; read the state with `power_status ?`, which
  returns one of `standby`, `startup`, `on`, `cooling1`, `cooling2`. After
  `power "on"` a VW295ES reported `standby` for about 10 s, then `startup`,
  and `on` after about 20 s.
- A unit may lack commands its manual column lists: the VW295ES answers
  `err_cmd` to `hdr_auto`, which the VW290ES/VW270ES column marks as
  supported. Trust the probe, not the column.
- Remote key presses are not free of side effects: on a manual-lens VW295ES
  `key "lens_focus_far"` opens the focus test pattern, and `key "picmode1"`
  selected `brt_tv`. Query the setting afterwards rather than assuming.
- `error ?` and `warning ?` return JSON arrays of tokens; `no_err` and
  `no_warn` mean nothing is wrong.
- `signal ?` returns a format string such as `3840x2160/60p`, or `Invalid`
  when there is no usable signal.
- Lamp models have `lamp_control`; laser models have `light_output_*`
  commands instead. The info query tells you which you have.

## SDAP advertisement

The projector broadcasts a UDP datagram to port 53862 every 30 seconds
(interval, port, community and extra destination addresses are configurable).
The packet is 50 bytes:

| Offset | Size | Field |
|---|---|---|
| 0 | 2 | ID, always `0x44 0x41` (`DA`) |
| 2 | 1 | Protocol version, always 1 |
| 3 | 1 | Equipment category, `0x0A` for projectors |
| 4 | 4 | Community string |
| 8 | 12 | Product name, padded with `0x00` |
| 20 | 4 | Serial number |
| 24 | 2 | Power status, `0xFFFF` when the projector cannot read it |
| 26 | 24 | Installation location, padded with `0x00` |

The manual does not state the byte order of the serial number and power
status fields, nor the meaning of individual power status values other than
`0xFFFF`. A VW295ES confirmed big-endian for the serial number (the four
bytes decode to the same number `serialnum ?` returns). Its power status
bytes were `00 00` in standby, `00 03` when on and `00 05` while cooling;
`startup` was not caught between two advertisements. pyadcp decodes both
fields as big-endian unsigned integers, names those three values and exposes
the raw bytes too. The unit sent every advertisement twice.
