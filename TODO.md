# TODO

Ideas worth a feature branch. Each entry says what, why and roughly how, so
it can be picked up cold.

## Report capabilities the code has never seen

**What.** When discovery meets a reply the library does not recognise, say
so, and make it one command to produce a shareable report of what this unit
supports. Reports from other owners then grow the reference data without
anyone needing the projector.

**Why.** pyadcp was verified on exactly one unit, a VPL-VW295ES. That run
found eight key codes missing from `KEY_CODES`, an info type (`ir_remote`)
missing from `INFO_TYPE_KINDS`, and a `sys_var` info shape nobody expected.
Every other ADCP model will have its own surprises, and the owners of those
models are the only people who can see them. Today they would have to read
`-v` logs to notice.

**What the projector can tell us.** Everything comes from `? --info`, which
the VW295ES answers in standby for every command. Three things are
detectable against what the code already knows:

1. `key ? --info` lists codes that are not in `KEY_CODES`.
2. An info reply carries a `type` that is not in `INFO_TYPE_KINDS`.
3. `_parse_info` cannot read a reply and falls back to "supported, details
   unknown" (this is how the `sys_var` shape showed up).

Not detectable: commands missing from `COMMANDS`. Discovery can only probe
names it has, so a new model's new commands stay invisible until someone
reads its manual. The report should carry `modelname` and `version` so that
gap can at least be attributed.

**How.**

- Library: a `CapabilityReport` (or fields on `Capabilities`) that lists
  `unknown_key_codes`, `unknown_types` and `unparsed_replies` (command,
  suffix, raw reply). Log each at `WARNING` the first time it is seen with a
  one-line pointer to how to report it. `changes_since` ignores them.
- CLI: `python -m pyadcp report HOST` writes one JSON document with
  `modelname`, `serialnum` redacted or omitted, `version`, the
  `Capabilities.to_dict()` snapshot and the three lists above, so a user can
  attach it to an issue as-is. Keep `mac_address`, addresses and location
  out of it.
- Repository: submitted reports go under `docs/models/<modelname>.json`.
  They are documentation and test fixtures, not library data: the fake
  projector can grow a profile per report, and `KEY_CODES` /
  `INFO_TYPE_KINDS` are updated by hand from them. `src/` keeps no per-model
  tables.
- Checklist: add an item per new model so the owner knows which commands to
  exercise by hand (sets, keys, power timing) beyond what `report` collects.

**Open questions.** Whether the warning belongs in the library or only in the
CLI (a Home Assistant integration may want to surface it as a repair issue
instead); whether `report` should also run `? --range` with the unit on to
capture the signal-dependent lists.
