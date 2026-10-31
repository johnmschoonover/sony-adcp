# Prompt 02a: swap `sony_projector` from pySDCP to pysdcp-extended

You are contributing to Home Assistant core in the user's fork of
`home-assistant/core`. Target branch: `dev`. This is a small dependency PR
and changes no behaviour. It is the first PR in a series. The next one
(prompt 02b) adds a config flow.

## Why

- `homeassistant/components/sony_projector/` uses `pySDCP==1`. That package
  has one PyPI release, from 2017, with no git tags and no CI publishing. It
  fails the bronze quality-scale rule `dependency-transparency`: the source is
  under an OSI licence, the package is on PyPI, it is built and published from
  a public CI pipeline, and the PyPI version matches a tagged release.
- `pysdcp-extended` is an MIT fork of pySDCP (`kennymc-c/pySDCP-extended`).
  Checked on 2026-10-31:
  - **Licence:** the 0.2.1 wheel metadata has `License: MIT`, the OSI
    classifier, and `LICENSE.md`.
  - **Distributions:** an sdist and a wheel are on PyPI.
  - **Public CI:** the `build+publish` workflow publishes with PyPI trusted
    publishing. Run #22 for tag `0.2.1` succeeded and is publicly visible.
  - **Tag match:** tag `0.2.1` (`5ca48b1`) matches the PyPI version, and the
    sdist's code is identical to the tag.
  - **Issues:** the tracker is enabled.
  - **Python:** `Requires-Python: >=3.11,<4`, with classifiers up to 3.14.
    Core currently requires 3.14.2 or later.
- The API used by the integration is identical: `Projector(ip)`,
  `get_power() -> bool`, and `set_power(on) -> True`, which raises on failure.
  The constructor also accepts optional `community`, `udp_port` and
  `tcp_port`. Do not use them in this PR.

Re-check every point above against the live PyPI and GitHub pages before
opening the PR. If anything has changed, stop and tell the user.

## Changes

1. `manifest.json`:
   - `"requirements": ["pysdcp-extended==0.2.1"]`, or the latest release if
     it passes the same checks.
   - `"loggers": ["pysdcp_extended"]`.
2. `switch.py`: `import pysdcp_extended` and use
   `pysdcp_extended.Projector(host)`. No other change. Keep the existing
   exception handling exactly as it is. Better error handling comes with the
   config-flow PR.
3. Run `python -m script.gen_requirements_all` to update
   `requirements_all.txt` and, if needed, `requirements_test_all.txt`. Then
   run `python -m script.hassfest` and `python -m script.licenses` (or
   whatever licence check core currently runs) so the new dependency passes.
4. No tests exist for this integration today. Do not add a test suite in this
   PR; the config-flow PR adds one. If reviewers ask for a smoke test of the
   YAML platform with the new library, add a minimal one.

## PR description

- Type of change: "Dependency upgrade" (or the closest option in the core PR
  template).
- Explain the dependency-transparency reason in two sentences.
- Link the library diff, as core asks for requirement changes. Use the
  cross-fork compare `Galala7/pySDCP:master...kennymc-c/pySDCP-extended:0.2.1`,
  plus the 0.2.1 release run and the PyPI page.
- State that behaviour is unchanged: same YAML, same entity, same power
  semantics.
- One-sentence title, for example
  `Replace pySDCP with pysdcp-extended in Sony Projector`.
- No AI model identifiers in commits or PR text.

## Documentation

None needed. The home-assistant.io page does not name the library. If it
does by the time you check, update that mention in a small docs PR against
`current`, because this is not a new feature.

## Done when

- The switch works unchanged on the new library.
- hassfest, the licence check, the requirements generation, and ruff pass.
- The PR is ready, and your report lists the live checks you re-ran.
