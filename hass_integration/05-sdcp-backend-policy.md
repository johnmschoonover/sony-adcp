# Prompt 05: decide the future of the SDCP backend in `sony_projector`

You are helping the code owner of Home Assistant's `sony_projector`
integration (the user, GitHub `johnmschoonover`) decide what to do with the
integration's SDCP backend. Work in the user's fork of `home-assistant/core`
(target branch `dev`) and of `home-assistant/home-assistant.io`.

## Background

- `sony_projector` has two backends, chosen at setup by `pyadcp.probe()`:
  - **ADCP** via `pyadcp`, for 2016+ models. Full settings entities.
  - **SDCP / PJ Talk** via `pySDCP==1`, for older lamp models. Power switch
    only.

  Read the current integration code first.
- The default is to **keep SDCP**. It costs little: one switch, a small
  adapter, and the executor. Removing support for working hardware is
  something core reviewers resist. Only change course with a concrete
  reason.
- Concrete reasons that would justify a change:
  1. pySDCP fails the bronze `dependency-transparency` rule. That rule
     requires an OSI licence, publication on PyPI, PyPI builds made in a
     public CI pipeline, and each PyPI version matching a tagged release. As
     of late 2026, `Galala7/pySDCP` showed no GitHub releases and the
     integration pins `pySDCP==1`.
  2. pySDCP breaks on the Python version Home Assistant requires, or
     triggers blocking-call or other core warnings that cannot be fixed
     without a release.
  3. pySDCP is abandoned and has an unfixed bug that affects users.
- Policy, from `docs/deprecating.md` in `home-assistant/developers.home-assistant`
  (read it from GitHub raw; the site may not resolve):
  - "Any removals must be deprecated first."
  - The minimum period for user-facing changes is 6 months. It can be
    extended but never shortened.
  - Every deprecation that needs the user to act gets a repair issue,
    scoped per config entry.
  - Severity is `WARNING` while the old behaviour still works and `ERROR`
    only once it has stopped.
  - The deprecation is announced through the repair issue and the release
    notes. There is no deprecation notice on home-assistant.io.
  - After the period, a follow-up PR removes the behaviour and the issue.

## Part A: assessment (always do this; no code)

Produce a short written assessment for the user:

1. **pySDCP's health.** Check:
   - Does PyPI host a source distribution?
   - Do the published versions match git tags?
   - Is publishing automated from public CI?
   - Is the licence present in the metadata?
   - When was the last commit and the last release?
   - Are there open issues that affect Home Assistant?
   - Does it run on the Python version core currently requires?
   - Does Home Assistant's blocking-call detection complain about it?
2. **Alternatives.** Check `pysdcp-extended` (kennymc-c's MIT fork) against
   the same list. If it passes and pySDCP does not, a library swap keeps
   older projectors working with no deprecation. It may even add commands
   (input, picture mode) for SDCP entries. Note: other kennymc-c projects
   are GPL. Do not read or copy GPL code; check the licence of each repo.
3. **Usage.** Can the share of SDCP entries be estimated (analytics on
   integration usage, forum and issue mentions)? State plainly what cannot
   be known.
4. **Recommendation.** Keep, swap the library, or deprecate, with the
   reasons. Stop here and wait for the user's decision before writing code.

## Part B, option "swap library": replace pySDCP with an equivalent

- Keep identical behaviour and entity IDs. Update `requirements` and
  `loggers`, run `python -m script.gen_requirements_all`, and adjust the
  tests.
- This is not a removal, so no deprecation is needed. Explain the reason in
  the PR (the dependency-transparency rule).
- Docs: update the prerequisites only if they change.

## Part B, option "deprecate": the formal process

1. **Get agreement first.** Draft a short proposal for the user to post
   where core maintainers will see it (an architecture discussion in
   `home-assistant/architecture`, or the core PR itself; ask the user which).
   It should state the reason, the affected models, the alternative for
   those users (the generic PJLink integration, if their projector supports
   PJLink), and the timeline.
2. **Deprecation PR**, once agreed:
   - On setup of each SDCP config entry, raise a repair issue under
     `DOMAIN`. Use `issue_id` `deprecated_sdcp_{entry_id}`, severity
     `WARNING`, `is_fixable=False`, `breaks_in_ha_version` at least 6 months
     after the release that ships it (calendar versioning), and
     `translation_key="deprecated_sdcp"`. Add the `strings.json` text: what
     is changing, when, why, and what the user can do. Delete the issue when
     the entry is removed.
   - Decide with the user whether new SDCP setups are blocked during the
     period: the config flow aborts with a translated `sdcp_deprecated`
     reason. Blocking new setups while existing ones keep working is
     consistent with the policy's successor pattern, but reviewers may
     prefer to keep setup open. Ask the user.
   - Tests: the issue is created for SDCP entries and not for ADCP entries,
     and it is deleted on unload or removal. Use the `issue_registry`
     fixture.
   - PR template: type "Deprecation". Write the release-notes text in the
     PR description.
3. **Removal PR**, after `breaks_in_ha_version`:
   - Remove the SDCP backend and the `pySDCP` requirement, then regenerate
     requirements.
   - Existing SDCP entries must still load into a clear state. Raise an
     `ERROR` repair issue per entry that explains support has ended, and do
     not crash. Follow the `integration_removed` pattern in
     `docs/deprecating.md`, adapted to one backend.
   - PR template: type "Breaking change". Fill in the "Breaking change"
     section, which feeds the release notes.
4. **Docs (home-assistant.io)**
   - Deprecation PR: no docs change, because notices are not allowed.
   - Removal PR: a docs PR, targeting the branch for the release that removes
     it, that removes the SDCP prerequisites (PJ Talk and host address) and
     the "older projectors" wording, and states that the integration supports
     ADCP projectors (2016 and later). Link the core removal PR.

## Conventions

- One-sentence PR titles. No AI model identifiers in commits or PR text.
- Never shorten the deprecation period, and never remove anything without
  the deprecation PR having shipped first.

## Done when

- Part A has been delivered and the user has chosen an option.
- For the chosen option, the PRs above are prepared with tests and docs,
  and hassfest, ruff, mypy and pytest pass.
