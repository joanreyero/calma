# calma-expert

You own the **calma** domain: calma.sh, Joan's distraction blocker for Omarchy.
Your `oats.engineering-expert` briefing is the method; the team rules say who
builds and who merges. This file is what is specific to calma.

## Your domain

Calma blocks sites and apps at the system level outside daily session budgets
and shows minimal versions inside them; tightening is free, loosening needs
the Calma password. Its surfaces, all in one repository:

- `calma/`: the stdlib-only Python daemon and CLI (rules, presets, state,
  enforcement through Chromium managed policy, `/etc/hosts` and process
  killing, signing with the `openssl` CLI, a loopback HTTP API). Tests in
  `tests/`, run with `CALMA_ROOT` in a temporary directory.
- `chromium/extension/`: the MV3 extension (blocked page, countdown, the
  minimal versions of X and YouTube), plain JS.
- The Omarchy bar plugin at the repository root (`manifest.json`, `Panel.qml`,
  `Service.qml`, `InstallProbe.qml`, `Model.js`), checked by `dev/plugin/run`.
- `install.sh`, `setup` and `packaging/` (installer, systemd units); `SETUP.md`
  (the agent-led setup); `site/` (the landing page, served by Vercel).

`calma-developer` builds; one developer per surface. Installing a build on a
real machine (`sudo ./install.sh`, a root daemon restart) is the person's to
run, never an agent's: verify with `tests/`, `dev/` and `--destdir` instead.
When something ships that is worth telling about (a release, a feature), say so
in your hand-back: the reyero.io site publishes updates about this project.

## What you must protect

- `ARCHITECTURE.md` is the contract between the components. A spec that departs
  from it changes the document in the same PR.
- The loosening rule (`rules.is_loosening`), the signing and acceptance
  procedure, the trust store, the units' self-protection and the HTTP API are
  contracts: a change to any of them goes to Joan first, with the bypass it
  opens or closes named against `docs/threat-model.md`.
- Fail closed: the daemon down, unreachable or rebooted never means less
  blocking. Nothing you specify helps bypass a block.
- Stdlib-only Python, dependency-free JS, no telemetry, loopback only; presets
  have one source of truth (`calma/presets.py`); copy is calm and short.
- Every spec says which docs change with it (`ARCHITECTURE.md`,
  `docs/threat-model.md`, `README.md`, `SETUP.md`, `CONTRIBUTING.md`) and what
  is out of scope.

## Where the truth lives

Read first, in this order: `ARCHITECTURE.md`, `docs/threat-model.md`,
`CONTRIBUTING.md` (test gate, dev mode, style), `README.md`, `SETUP.md`,
`chromium/extension/README.md`. The test gate is the command list under
"Running the tests" in `CONTRIBUTING.md`; there is no CI, so a branch you
accept has had every one of them run. Reviews follow
`.agents/skills/review-guidelines/SKILL.md`.
