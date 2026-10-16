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
- Typical reply latency is 30 to 1000 ms. Heavy traffic slows the projector.
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
| `sys_sel` | system selection such as `power` | set, `?` (not for `power`), `? --range`, `? --info` |
| `sys_stat` | read-only status such as `power_status`, `error`, `timer` | `?`, `? --info` (no range in info) |
| `sys_var` | network addresses as quoted strings | set, `?`, `? --range` (no `? --info` is documented) |
| `menu_sel` | OSD choice setting such as `picture_mode` | set, `--reset`, `?`, `? --range`, `? --info` |
| `menu_num` | OSD numeric setting such as `contrast` | set, `--rel`, `--reset`, `?`, `? --range`, `? --info` |
| `menu_exec` | OSD action such as `picture_mode_reset` | execute, `? --info` |
| `key` | remote control emulation: `key "menu"` | set only |

The manual also documents expert-only types (`panel_align_zone`,
`user_gamma`, `pattern_sel`, `pattern_pos`) with their own option syntax.
pyadcp does not model them.

## Replies

| Reply | Meaning |
|---|---|
| `ok` | The command was accepted. |
| `err_cmd` | The command is not recognised or is malformed. Unsupported commands on a given model answer this. |
| `err_option` | An option, such as an unsupported `--suffix`, is not accepted. |
| `err_inactive` | The command exists but cannot be used right now, typically because the projector is in standby or has no signal. |
| `err_val` | The value is outside the numeric range or not one of the valid choices. |
| `err_auth` | Authentication failed. Only sent during the handshake. |
| `err_internal1`, `err_internal2` | A communication error inside the projector. |

## Model differences

Which commands, values and suffixes exist differs by model. Rather than carry
per-model tables, pyadcp asks the projector: a command that answers `err_cmd`
to `? --info` is unsupported, a suffix that answers `err_option` is
unsupported, and the `range` in the info reply gives the option list or the
numeric bounds for that unit. A command that answers `err_inactive` to the
info query (typically in standby) is kept as supported with unknown details
and asked again later; any other reply is treated the same way rather than
stopping discovery. The `sys_var` commands are probed with `? --range`.

Some practical consequences for a Home Assistant style client:

- In standby most `menu_*` commands answer `err_inactive`. Poll only
  `power_status`, `error`, `warning` and `timer` until the power status is
  `on`.
- `power ?` is not supported; read the state with `power_status ?`, which
  returns one of `standby`, `startup`, `on`, `cooling1`, `cooling2`.
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
`0xFFFF`. pyadcp decodes both as big-endian unsigned integers and exposes the
raw bytes too, pending confirmation against hardware.
