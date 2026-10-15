# Hand-off: Sony ADCP projector library for Home Assistant

This file is the complete context from the pySDCP-extended analysis session (2026-10-15). Attach it, plus the two Sony PDFs, to the first message of the new session.

## Opening prompt for the new session (copy as is)

> Build `pyadcp`, an MIT-licensed, asyncio-native Python library for Sony projectors' ADCP protocol, as the backend for a Home Assistant integration. Attached: a hand-off brief (this file) with the wire format, auth handshake, error codes, per-model command coverage and the agreed architecture; Sony's "Protocol Manual (COMMON)" 9-932-751-53 and "Protocol Manual (Supported Command List)" 9-932-761-52. Primary test target is a VPL-VW295ES (manual column "VW290ES/VW270ES"); the XW series must also work. Start with: package skeleton, the connection/auth layer with typed errors, a fake-projector test server driven by the COMMON manual, and the `Projector` facade covering power, input, muting, picture preset, aspect, HDR, lamp control, status (power, error, warning, timer, signal, version, MAC), and the remote key command. No GPL code may be consulted or copied; write from the manuals only.

## Decisions already made

1. New repository and new PyPI package, not a fork of pySDCP-extended. SDCP shares nothing with ADCP except SDAP discovery, the existing code is synchronous, and the `pysdcp-extended` PyPI name belongs to upstream.
2. Name: `pyadcp` (free on PyPI as of 2026-10-15; alternatives also free: `sony-adcp`, `pysonyadcp`, `aiosonyadcp`; `adcp` is taken).
3. License MIT, clean-room from Sony manuals. kennymc-c/ucr-integration-sonyADCP has a GPL-3.0 ADCP client; do not copy from it.
4. Layout: `pyadcp/connection.py` (TCP session, nonce + SHA-256 auth, line framing, typed exceptions, silent reconnect after the projector's 60 s idle close), `pyadcp/commands.py` (command table typed as sel/num/exec/stat with suffix support, no per-model tables), `pyadcp/projector.py` (facade with HA-oriented getters/setters, `poll_status()`, capability discovery via `? --info` and `? --range`), `pyadcp/sdap.py` (UDP 53862 discovery), `tests/fake_projector.py` (protocol-accurate asyncio server so CI needs no hardware).
5. Keep Sony's PDFs out of a public repo; keep a derived `docs/protocol.md` instead.
6. Pre-2016 lamp models stay on pySDCP / HA core `sony_projector`; this library targets 2016+ including every XW model.

## VPL-VW295ES: ADCP support summary (manual column VW290ES/VW270ES)

Supported: power on/off; power_status (standby, startup, on, cooling1, cooling2); error (no_err, err_power, err_power2, err_cover, err_light_src, err_temp, err_fan); warning (no_warn, warn_light_src_life, warn_highland, warn_temp, warn_signal_freq); timer (operation, light_src, prev_light_src); modelname; serialnum; signal (SD/HD/UHD set, no 120p/48p); version; mac_address; ipv4/ipv6 settings; input hdmi1/hdmi2; blank; picture_mode cinema_film1, cinema_film2, reference, tv, photo, brt_cinema, brt_tv, user, game; picture_mode_reset; contrast, brightness, color, hue, sharpness; color_temp custom1-5, d93, d75, d65, d55 with coltemp_gain/bias r/g/b; color_space bt709, bt2020, color_space1-3, custom with col_space_x/y; gamma_correction (all); film_mode; real_cre, real_cre_db, real_cre_reso, real_cre_noise; motionflow smooth_high, smooth_low, true_cinema, off; nr, mnr, smooth_grd, contrast_enh, col_correction (+hue/color/brt), clear_white, xvcolor; input_lag_red; lamp_control high/low; hdr auto, hdr10, hdr_reference, hlg, off; aspect normal, v_stretch, squeeze, 1.85_1_zoom, 2.35_1_zoom, stretch; 2d3d_sel auto/3d; 3d_format sim3d, sidebyside, overunder; 3d_depth; sim3d_effect; test_pattern; setting_lock; hdmi_signal_format --hdmiN; language; menu_pos; status_disp on/off; ir_receiver; lens_lock; powsave_nosig; dynamic_range --hdmiN; image_flip; blanking + blanking_enable; panel_align_*; high_alt_mode on/off; remote_start; network_mgmt; anamorphic_lens; trigger --portN off/power/v_stretch/2.35_1_zoom; all basic remote keys incl. picmode1-9, picture/color/bright/hue/sharpness +/-, aspect keys, 3D keys.

Not supported on this model: hdr_auto (footnote 2 excludes VW270ES series), iris_dyn_cont, iris_brightness, light_output_*, pic_pos_* (manual lens, no lens memory), temperature, 3d_status, 2d3d_sel "2d", picture_mode user1-3, hdr_tone_mapping, live_color_enh, motionflow impulse/combination, high_alt_mode level1/2, web_control_ui, remote_id, lens_position* keys, laser_brightness keys, iris_mode key. Lens focus/zoom/shift keys are listed as supported but the lens is manual; treat as no-ops.

Compared with SDCP on this model there is no functionality loss; ADCP is a strict superset.

## Full analysis report follows

# Coverage of Sony Protocol Manual 9-932-761-52 by pySDCP-extended

Manual: "Video Projector Protocol Manual (Supported Command List)", 1st Edition Revised 1, May 2025 (file 99327615M.pdf).
Library: pysdcp_extended 0.2.1 (`pysdcp_extended/protocol.py`, `pysdcp_extended/__init__.py`).

## 1. The manual and the library describe different protocols

| | Manual (ADCP) | Library (SDCP / PJ Talk) |
|---|---|---|
| Transport | TCP 53595, ASCII lines, e.g. `power "on"` / `power_status ?` | TCP 53484, 10 or 12 byte binary frames |
| Values | Strings and JSON, e.g. `"hdr10"`, `[{"operation":3400}]` | 16-bit item numbers and 16-bit data words |
| Auth | Manual says authentication is ON by default | Community string, "SONY" by default |
| Discovery | SDAP, UDP 53862 | SDAP, UDP 53862 (implemented) |
| Where documented | This manual plus the "Protocol Manual (COMMON)" for framing and auth | Older per-model SDCP manuals (code cites the VW320/VW520 manual) |

Consequences:

1. Nothing in this manual is implemented directly. The library speaks SDCP only. Coverage below is by *function*, matching ADCP command names to SDCP items.
2. This manual contains no SDCP item numbers or data codes, so it cannot be used to fill the gaps in `protocol.py`. Filling them needs the SDCP manual for the target model, or probing.
3. The manual's "Network Communication" port table (page 29) lists SDAP, ADCP, DDDP, SDDP and CIP. It does not list PJ Talk / SDCP (53484). The per-model protocol table on page 1 also has no SDCP row. Before relying on this library for XW-series units, confirm on the device that PJ Talk is still offered and enabled.
4. The ADCP wire format comes from the companion "Protocol Manual (COMMON)" 1st Edition Revised 2, 9-932-751-53 (file 99327515M.pdf). It is summarised in section 7 below. That manual also lists every protocol the projector offers (SDAP, ADCP, PJLink, DDDP, SDDP, Crestron, SNMP, PJ Control API) and PJ Talk / SDCP is not among them.

Model columns in the manual (21): VW5000, VW760ES, VW675ES, VW665ES, VW365ES, VW360ES, VW260ES, VZ1000, HW65ES, HW45ES, VW890ES/VW870ES, VW570ES, VW290ES/VW270ES, VW790ES, VW590ES, XW7000, XW6000, XW5000, XW8100, XW6100, XW5100. Notes below call out the XW series because the README lists the XW6100.

## 2. Functions the library covers (via SDCP)

| ADCP command | SDCP item in library | Get/Set helper | Gap |
|---|---|---|---|
| `power "on"/"off"` | `SET_POWER` 0x0130 | `set_power()` | OK |
| `power_status ?` (standby, startup, on, cooling1, cooling2) | `GET_STATUS_POWER` 0x0102 | `get_power()` | Helper collapses five states to a bool. HA wants the raw state for "warming up" and "cooling" display. |
| `input "hdmi1"/"hdmi2"` | `INPUT` 0x0001 | `set_HDMI_input()`, `get_input()` | OK for 2-input models. `hdmi3`/`hdmi4` exist on VZ1000 only. `get_input()` returns `None` for an unknown value. |
| `blank "on"/"off"` | `PICTURE_MUTING` 0x0030 | `set_muting()`, `get_muting()` | OK |
| `picture_mode` | `CALIBRATION_PRESET` 0x0002 | `set_/get_calibration_preset()` | Manual values: cinema_film1, cinema_film2, reference, tv, photo, brt_cinema, brt_tv, user, user1, user2, user3, cinema_digital, game. Library has the first nine (as `USER`). XW series report `user1` and `user3` instead of `user`; `user2` and `cinema_digital` are VW5000 only. SDCP codes for user1/2/3 unknown. |
| `dynamic_range --hdmiN` (auto, limited, full) | `HDMI1_DYNAMIC_RANGE` 0x006E, `HDMI2_DYNAMIC_RANGE` 0x006F | `set_/get_HDMI_dynamic_range()` | OK |
| `aspect` | `ASPECT_RATIO` 0x0020 | `set_screen("ASPECT_RATIO", ...)` set only | Library: normal, v_stretch, 1.85 zoom, 2.35 zoom, stretch, squeeze. Missing `full1`, `full2` (all XW), `h_stretch` (VW5000), `aspect_ratio_scaling` (XW5100). On XW the 1.85/2.35 zoom values are not supported. No getter. |
| `pic_pos_sel` | `PICTURE_POSITION` 0x0066 | `set_screen("PICTURE_POSITION", ...)` set only | Values match (1.85_1, 2.35_1, custom1 to custom5). On XW7000/XW6000 only custom1 to 3; XW8100/XW6100 custom1 to 5; XW5000/XW5100 none. No getter. `pic_pos_save` / `pic_pos_del` not covered. |
| `timer ?` (operation, light_src, prev_light_src) | `GET_STATUS_LAMP_TIMER` 0x0113 | `get_lamp_hours()` | Returns a string, should be int. ADCP exposes three timers; SDCP item gives one. |
| `error ?` | `GET_STATUS_ERROR` 0x0101 | none | Constants only. `ERROR_STATUS` values 10, 20, 40 are decimal but the pattern (1, 2, 4, 8, ...) is a hex bitmask, so D5V, POWER and TEMP_WARNING are almost certainly wrong and should be 0x10, 0x20, 0x40. No helper decodes the bitmask. |
| `lamp_control` (high, low) | `LAMP_CONTROL` 0x001A | none | Constants only. Lamp models only; not on XW or other laser models. |
| `iris_dyn_cont` (full, limited, off) | `ADVANCED_IRIS` 0x001D | none | Constants only. Lamp models only. Laser models use `light_output_dyn` (see section 3). |
| `motionflow` | `MOTIONFLOW` 0x0059 | none | Constants only. Key typo: `SMOTH_HIGH`, `SMOTH_LOW`. `impulse` / `combination` not on VZ1000, VW790ES, VW590ES, XW series. |
| `hdr` | `HDR` 0x007C | none | Library: off, on, auto. Manual: auto, on, hdr10, hdr_reference, hlg, off. `on` exists only on VW665ES/VW365ES; every other model uses hdr10 / hdr_reference / hlg. SDCP codes for those unknown. |
| `input_lag_red` (on, off) | `INPUT_LAG_REDUCTION` 0x0099 | none | Constants only. |
| `2d3d_sel` (auto, 3d, 2d) | `2D_3D_DISPLAY_SELECT` 0x0060 | none | Constants only. Not on XW8100/XW6100/XW5100. |
| `3d_format` (sim3d, sidebyside, overunder) | `3D_FORMAT` 0x0061 | none | Constants only. `sim3d` not on XW. |
| `menu_pos` (bottom_left, center) | `MENU_POSITION` 0x00A6 | none | Constants only. |
| `modelname ?`, `serialnum ?` | SDAP broadcast only | `get_pjinfo()`, `find_projector()` | Only via a 30 s UDP wait. No on-demand SDCP query implemented. |

Remote keys. ADCP `key "..."` has about 100 key codes. `COMMANDS_IR` has 14: menu, right, left, up, down, enter, lens_shift_up/down/left/right, lens_focus_far/near, lens_zoom_up/down (named LARGE/SMALL). The remaining keys are listed in section 4.

## 3. Functions in the manual with no SDCP item in the library

Grouped by the manual's sections. "XW" means supported on XW7000/6000/5000/8100/6100/5100 unless noted. Items of interest for Home Assistant are marked with (HA).

### System status (`sys_stat`, read only)
- `warning ?` (HA): no_warn, warn_light_src_life (lamp models), warn_highland, warn_temp, warn_signal_freq. All models.
- `signal ?` (HA): current input signal format, e.g. "3840x2160/60p", "1080/120p", "Invalid". All models; 4K/120p and 48p values on XW8100/6100/5100.
- `temperature ?` (HA): numeric, VW5000 and all XW.
- `version ?` (HA, for device info): firmware versions as JSON. All models.
- `mac_address ?` (HA, for unique_id): all models except HW45ES.
- `3d_status ?`: 2d / 3d. Not on XW8100/6100/5100.
- `filter_status ?`: HW65ES/HW45ES only.
- `panel_resolution ?`, `licenses ?`, `ipv6_ip_address ?`, `ipv6_default_gateway ?`, `ipv6_prefix ?`.

### System settings (`sys_sel`, `sys_var`)
- `ipv4_network_setting`, `ipv4_set_method`, `ipv4_ip_address`, `ipv4_sub_net_mask`, `ipv4_default_gateway`.
- `complete_maintenance "lamp"/"filter"`: lamp models and HW series only.

### Picture (`menu_sel` / `menu_num` / `menu_exec`)
- `picture_mode_reset` (exec).
- Numeric: `contrast`, `brightness`, `color`, `hue`, `sharpness` (HA number entities).
- `color_temp`: custom1 to custom5, d93, d75, d65, d55, dci (dci VW5000 only). Plus `coltemp_gain_r/g/b`, `coltemp_bias_r/g/b`, `coltemp_x`, `coltemp_y`.
- `color_space`: bt709, bt2020, adobe_rgb, color_space1/2/3, custom (VW), custom1/2 and dci (VW5000). Plus `col_space_x --r/g/b`, `col_space_y`.
- `gamma_correction`: 1.8, 2.0, 2.1, 2.2, 2.4, 2.6, gamma7 to gamma10, off.
- `film_mode` auto/off: not on XW.
- `real_cre` on/off (HA), `real_cre_db` mi4k/normal (not XW), `real_cre_reso`, `real_cre_noise`, `real_cre_dfopt` on/off.
- `iris_brightness` numeric: lamp models.
- `light_output_dyn` full/limited/off (HA, laser dynamic control): VW5000, VW760ES, VZ1000, VW890ES, VW790ES, all XW.
- `light_output_val` numeric (HA, laser brightness): VW5000, VW760ES, VZ1000, VW790ES, all XW.
- `light_output_coeff` numeric: VW5000 and XW.
- `light_output_iris_val`, `light_output_iris_dyn`: VW890ES/VW870ES, VW790ES.
- `nr` auto/high/mid/low/off; `mnr` (not XW); `smooth_grd`; `contrast_enh` (HA); `col_correction` on/off plus `col_corr_hue/color/brt --r/g/b/c/y/m`.
- `clear_white` high/low/off (not XW); `live_color_enh` high/mid/low/off (XW7000, XW8100 only); `xvcolor` (not XW).
- `hdr_auto` hdr10_hlg / hdr_reference_hlg (HA): most 4K models incl. XW.
- `hdr_tone_mapping` mode1/mode2/mode3/off (HA): XW8100/XW6100/XW5100 only.
- `periodic_ld_calibration`: VW5000 only.

### Screen
- `pic_pos_save`, `pic_pos_del` (exec, same value list as pic_pos_sel).
- `image_shift --h/--v`, `image_shift_enable`: VW5000, VZ1000.

### Function
- `illumination` on/off: VZ1000.
- `3d_brt`, `3d_glass_brt`, `3d_depth`, `sim3d_effect`: 3D models only.
- `test_pattern` on/off.
- `setting_lock` off/level_a/level_b.
- `hdmi_signal_format --hdmiN` standard/enhanced (HA): all 4K models incl. XW.
- `infrared`, `infrared_output`, `dimming_filter`, `infrared_light_bank`, `stream_format`: no listed model supports them.

### Operation
- `language` (18 values), `status_disp` on/off/all_off, `ir_receiver` front_rear/front/rear, `remote_id` (VW5000), `lens_lock` on/off, `web_control_ui` on/off (XW).

### Connection / power
- `powsave_nosig` standby/off (HA).
- `remote_start` on/off (HA, Wake on LAN), `network_mgmt` on/off: all except HW.
- `anamorphic_lens` 1.24x/1.32x (HA).
- `trigger --port1/--port2` off/power/v_stretch/2.35_1_zoom/3d (HA).
- `blanking_enable` on/off, `blanking --top/bottom/left/right` numeric.

### Installation
- `image_flip` hv/h/v/off.
- `color_matching_brt/_r/_b --levN`, `color_matching_reset`: VW5000, VW890ES.
- `panel_align_shift_adj_r/_b --h/--v`, `panel_align_pattern`, `panel_alignment` on/off, `panel_align_reset`.
- `high_alt_mode`: on/off (VW), off/level1/level2 (XW).
- `blend_*` (VW5000 only).
- `corner_keystone_h/_v --top_left ...`, `screen_fitting_reset`: XW8100/XW6100/XW5100 only.
- `activate_licenses`: VW5000.

### Advanced adjustment (expert)
- `panel_align_zone`, `user_gamma`, `pattern_sel` / `pattern_pos` (`pat_*`). Out of scope for home automation.

## 4. Remote key codes not in `COMMANDS_IR`

Available on all 21 models unless noted:

- power_on, power_off, power (toggle), input_a, input_b, input (toggle), blank, reset
- picmode1 to picmode9, picmode (toggle)
- picture+/-, color+/-, bright+/-, hue+/-, sharpness+/-
- color_temp, color_mode, black_level, reality_creation, motionflow, gamma_correction, color_correction
- laser_brightness+/- (laser models incl. XW); iris_mode (lamp models)
- aspect, aspect_normal, aspect_v_stretch, aspect_stretch, aspect_squeeze; aspect_1.85_1_zoom / aspect_2.35_1_zoom (not XW); aspect_full1 / aspect_full2 (XW); aspect_h_stretch (VW5000); aspect_ratio_scaling (XW5100)
- status_on, status_off
- lens_control, lens_focus, lens_zoom, lens_shift, lens_position, lens_position1 to 5 (lens_position1/2 not XW; lens_position6/7 XW8100/XW6100 only). Lens keys are not on XW5000/XW5100.
- lens_precise_adj, lens_precise_adj_far/near (VZ1000, VW890ES)
- pattern (VW365ES, HW series)
- 3d, 2d_3d_display_select(_auto/_3d/_2d), 3d_format(_simulated_3d/_side_by_side/_over_under), 3d_brightness*, 3d_glasses_brightness*, simulated_3d_effect*, 3d_depth+/- (3D models only)
- input_c, input_d (VZ1000)

The SDCP IR codes for these are not in this manual. The SDCP manual gives them in the 0x17xx / 0x19xx / 0x1Bxx ranges; `COMMANDS_IR` has only 14 of them.

## 5. Library issues that affect Home Assistant exposure (independent of the manual)

1. `ERROR_STATUS` D5V/POWER/TEMP_WARNING values written as decimal 10/20/40; should be hex 0x10/0x20/0x40 if the item is the usual bitmask. No decoder.
2. `get_power()` hides startup and cooling states.
3. `get_lamp_hours()` returns a string.
4. No getters for aspect ratio or picture position; no helpers at all for lamp control, iris, motionflow, HDR, input lag, 2D/3D, 3D format, menu position, error status.
5. `MOTIONFLOW` keys misspelled (`SMOTH_`).
6. All socket I/O is blocking with a new TCP connection per command; HA will need these wrapped in an executor or an asyncio port.
7. `_send_command` raises bare `Exception`; HA integrations benefit from typed exceptions (timeout vs. projector error vs. connection refused).
8. `pyproject.toml` says version 0.0.5 while the changelog is at 0.2.1.

## 6. Recommended path for "expose everything" in Home Assistant

Option A (stay on SDCP): obtain the SDCP manual for the target model (XW series), add the missing item numbers and data codes, add typed get/set helpers and an error bitmask decoder. Limited by whatever SDCP exposes, which is a subset of ADCP, and by whether the XW firmware still offers PJ Talk.

Option B (add ADCP): implement an ADCP client (TCP 53595, line protocol, auth per the COMMON manual) alongside SDCP. Every item in this manual then maps 1:1 to a string command, `? --range` gives the valid options per model at runtime (useful for building HA select entities dynamically), and `? --info` gives numeric min/max for number entities. This is the only route that reaches signal status, temperature, warnings, firmware, MAC, laser brightness, HDR tone mapping, trigger outputs, and the full key list.

Option B is the better fit for the stated goal. Everything needed to implement it is in section 7.

## 7. ADCP wire format (from the COMMON manual, 9-932-751-53)

Transport
- TCP, port 53595 (configurable on the projector). Also available over RS-232C at 38400 8E1 with the same text syntax.
- US-ASCII. Every command and every response ends with CR LF. Arguments are separated by a single space.
- Max command length 512 bytes including CR LF. A partially typed command is discarded after 60 s.
- Idle TCP sessions are closed by the projector after the connection time-out (60 s default, configurable). A long-lived connection is fine as long as something is sent within that window, otherwise reconnect per command.
- Strictly request/response: wait for the reply before sending the next command. Multiple controllers are allowed but each must also wait.
- Typical response latency 30 to 1000 ms.
- In standby with "Standby mode" = Low or "Network management" = Off, VW and HW series may simply not answer for up to 10 s; resend after that. Other series return `err_cmd` on the first command after wake, resend once.

Authentication (on by default, password defaults to "Projector", same as the web admin password)
1. Client connects. Projector immediately sends one line.
2. If auth is off the line is `NOKEY`. Commands can be sent right away.
3. If auth is on the line is a random hex string, e.g. `1a2b3c4d`. Client replies with the lowercase 64-hex-digit SHA-256 of the nonce string immediately followed by the password, no separator, plus CR LF. Verified against the manual's worked example: `sha256("1a2b3c4d" + "password1234")` = `283121d4...0607`.
4. Projector replies `OK` (success, note uppercase) or `err_auth` (failure, connection should be considered dead).
5. An optional host allow-list on the projector restricts which client IPs may connect.

Command syntax (all types)
- Set selection: `cmd "value"` → `ok`
- Set number: `cmd 88`, relative: `cmd --rel 1` / `cmd --rel -1` → `ok`
- Reset to default: `cmd --reset` → `ok`
- Execute: `cmd` → `ok`
- Query: `cmd ?` → `"value"` or `88` or a JSON array/object
- Query current valid choices: `cmd ? --range` → `["a","b"]` or `{"min":0,"max":100,"step":1}` (step omitted when 1)
- Query command info: `cmd ? --info` → `{"type":"menu_sel","version":"1.0","range":[...]}`
- Suffixed items put the selector before the value or `?`: `dynamic_range --hdmi1 "full"`, `dynamic_range --hdmi1 ?`, `blanking --top 10`, `trigger --port1 "power"`.
- Values are JSON: strings quoted, numbers bare, arrays `[...]`, objects `{...}`. Replies such as `timer ?` return `[{"operation":3400},{"light_src":2300},{"prev_light_src":3000}]`.

Error replies
| Reply | Meaning |
|---|---|
| `ok` | success |
| `err_cmd` | unknown command or bad format |
| `err_option` | bad option (e.g. unknown `--suffix`) |
| `err_inactive` | command temporarily unavailable (wrong power state, no signal, etc.) |
| `err_val` | value out of range or not in the choice list |
| `err_auth` | authentication failed |
| `err_internal1`, `err_internal2` | internal communication error in the projector |

Design notes for an HA-oriented client
- `? --range` differs from `? --info`: range is what is valid *now* (varies with signal, picture mode, 3D state), info is the full set the firmware knows. Use info to build entity option lists once and range to grey out options at runtime.
- `err_inactive` is the normal answer for many picture commands while the projector is in standby. Poll only `power_status ?`, `error ?`, `warning ?`, `timer ?`, `temperature ?` in standby and poll the rest once `power_status` is `on`.
- Case matters in commands and values.

Other protocols on the same units (COMMON manual)
- SDAP, UDP 53862, broadcast every 30 s. Packet: ID `0x4441` (2), version (1, always 1), category (1, 0x0A), community (4), product name (12), serial (4), power status (2), location (24). Power status 0xFFFF on communication error. This matches `process_SDAP()` in the library.
- PJLink class 1, TCP 4352, with POWR / INPT / AVMT / ERST / LAMP / INST / NAME / INF1 / INF2 / INFO / CLSS. Off by default on every listed model. Home Assistant already has a core PJLink integration, which would give basic power, input, mute and lamp hours without any custom code once it is enabled on the projector.
- PJ Control API: JSON-RPC over HTTP POST to `http://<ip>/sony/<service>`, port 80, optional `X-Auth-PSK` header. Example `{"method":"getPowerStatus","params":[],"version":"1.0","id":1}` to `/sony/system`. The COMMON manual only gives the framing; the per-model API reference is not in either document supplied.
- DDDP (AMX), SDDP (Control4), CIP (Crestron), SNMP: discovery and third-party control systems only.

## 8. How far native SDCP can go

Evidence that SDCP still exists on current models
- The sibling Unfolded Circle SDCP integration (kennymc-c/ucr2-integration-sonySDCP) lists the XW6100 as tested, and its setup guide shows the projector web UI page "Setup > Advanced Menu > PJ Talk > Start PJ Talk Service". So PJ Talk is present but off by default and absent from Sony's 2024/2025 manuals.
- The same author has deprecated that SDCP integration in favour of an ADCP one and states that models from 2016 onward should use ADCP.
- No public SDCP implementation I can reach (vokkim/sony-sdcp-com, Galala7/pySDCP, this fork, the UC integration) knows any item codes beyond the 19 already in `protocol.py`. The only fuller source is the VW320/VW520-era SDCP manual, which is on digis.ru mirrors this environment cannot reach.

Tier 1, works today: power on/off and 5-state power status, input, picture muting, picture preset, HDMI dynamic range, aspect (set), picture position (set), lamp timer, SDAP model/serial, 14 IR keys.

Tier 2, item codes are in the library but need helpers: lamp control, advanced iris, motionflow, HDR, input lag, 2D/3D, 3D format, menu position, error status (fix the 10/20/40 decimal bug first).

Tier 3, item codes exist in the VW-era SDCP manual but are not in the library. From memory of that manual, to be verified against the PDF before use: contrast, brightness, color, hue, sharpness, color temperature, gamma correction, contrast enhancer, noise reduction, color space, reality creation on/off and its resolution/noise values, 3D depth, simulated 3D effect. Numeric items take a 16-bit value; no range query exists, so min/max must be hard-coded per model.

Tier 4, value codes unknown for post-2016 features: picture presets user1/user3, aspect full1/full2, HDR hdr10/hdr_reference/hlg, HDR auto mode, HDR tone mapping, dynamic light control and laser brightness, HDMI signal format, trigger outputs, high altitude level1/level2. Sony never published an SDCP manual for these models. They can be discovered on a real unit by using ADCP or the OSD as an oracle: change the setting, then GET the SDCP item and record the data word. Unknown items answer 0x0101 Invalid Item and unknown values 0x0104 Invalid Data, so probing is safe.

Tier 5, impossible on SDCP: input signal format, temperature, warnings as distinct from errors, firmware version, MAC address, on-demand model/serial, the three separate timers, `--range` / `--info` introspection, and any `menu_exec` action such as picture position save/delete or picture mode reset (unless an IR code exists for it).

IR keys: every remote key can be emulated through the 0x17xx / 0x19xx / 0x1Bxx IR items, fire-and-forget. Codes for the roughly 85 missing keys come from Sony IR code tables (category 0x17 projector, 0x19 projector-E, 0x1B projector-EE), not from an SDCP manual. This is the one area where SDCP can match ADCP's `key` list.

Practical ceiling: with the VW-era manual in hand and a probing session against an XW unit, SDCP can plausibly reach about 35 to 40 of the roughly 110 ADCP functions plus the full key list. The health and signal sensors that make a projector integration useful in Home Assistant (signal format, temperature, warnings, firmware) stay out of reach.

Reusable ADCP code: kennymc-c/ucr-integration-sonyADCP contains an asyncio ADCP client (`intg-sonyadcp/adcp.py`) with the SHA-256 handshake and the full error/warning enums, but it is GPL-3.0. pySDCP-extended is MIT, so it can serve as a reference but not be copied in.
