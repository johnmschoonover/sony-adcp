# Home Assistant integration: prompt set

These are self-contained prompts for separate Claude Code sessions. Each one
can be pasted as the opening message of a new session. This folder is
git-ignored on `main` (see `.gitignore` here); it is force-added only on a
working branch so the prompts can be opened from the Claude app. Do not merge
it into `main`.

| File | Work | Repository | Depends on |
| --- | --- | --- | --- |
| `01-pyadcp-release-readiness.md` | Four automation hardware checks; PyPI release and HA library rules are done (1.0.0 on PyPI) | `johnmschoonover/sony-adcp` (this repo) | none |
| `02-ha-config-flow-and-yaml-import.md` | `sony_projector`: config flow + YAML import, still pySDCP, no behaviour change | your fork of `home-assistant/core`, and `home-assistant/home-assistant.io` | none |
| `03-ha-adcp-backend.md` | `sony_projector`: add pyadcp as a second backend chosen by `probe()` | core fork + docs | 02 merged (pyadcp 1.0.0 is on PyPI) |
| `04-ha-automation-entities.md` | `sony_projector`: settings entities for automations, plus status sensors | core fork + docs | 03 merged |
| `05-sdcp-backend-policy.md` | Decide whether the SDCP backend stays, changes library, or is deprecated | core fork + docs | 03 merged |

## Goal of the integration work

The user wants automations, not a remote control. Examples:

- When the PS5 turns on, switch the projector to its game picture mode with
  input lag reduction on.
- When a given source is on, recall a picture position (lens memory on motorised-lens models) and
  set brightness/contrast.

So every useful projector setting must be a normal Home Assistant entity
(select, number, switch) that reports its current state, so that it can be
used as both a trigger/condition and an action. Remote key emulation is optional
and comes last.

## Notes for every session

- `developers.home-assistant.io` and `www.home-assistant.io` may not resolve
  from cloud sessions. Read the same pages from GitHub raw instead:
  `https://raw.githubusercontent.com/home-assistant/developers.home-assistant/master/docs/<path>.md`
  and `https://raw.githubusercontent.com/home-assistant/home-assistant.io/current/source/_integrations/sony_projector.markdown`.
- The Home Assistant sessions need the user's fork of `home-assistant/core`
  (and of `home-assistant/home-assistant.io` for docs) attached.
- Keep every PR small and single-purpose. That is how core reviews go fastest.
