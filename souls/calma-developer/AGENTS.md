# calma-developer

You build **calma** (calma.sh), the distraction blocker for Omarchy, to the
specs `calma-expert` gives you. Your `oats.developer` briefing is the method;
this file is what is specific to this repository.

## Where you build

- `calma/`: the stdlib-only Python daemon (`daemon.py`, `state.py`,
  `enforce/`) and CLI (`cli.py`), rules and presets, crypto via the `openssl`
  CLI, `paths.py` (every path, honouring `CALMA_ROOT`). Tests in `tests/`.
- `chromium/extension/`: the MV3 extension, plain JS, no build step.
- The Omarchy bar plugin at the repository root: `manifest.json`, `Panel.qml`,
  `Service.qml`, `InstallProbe.qml`, `Model.js`; its checks in `dev/plugin/`.
- `install.sh`, `setup` and `packaging/`: the installer and the systemd units.
- `site/`: the landing page, static files served as they are.
- `SETUP.md`: what the agent reads when the widget's "Set up with the agent"
  is clicked.

## This repository's rules

- These must pass before you hand back (`CONTRIBUTING.md`, "Running the tests"):

  ```sh
  uvx pytest
  uvx ruff check
  bash -n install.sh && uvx --from shellcheck-py shellcheck install.sh
  systemd-analyze verify packaging/*.service packaging/*.timer
  ./dev/plugin/run
  omarchy plugin validate .
  ```

  Tests run with `CALMA_ROOT` in a temporary directory and need no root. Try
  the installer with `./install.sh --destdir <tmp>`; a dev daemon runs with
  `CALMA_ROOT` set (`CONTRIBUTING.md`, "Dev mode"). Never run `sudo ./install.sh`
  or touch the real `/etc/calma`, `/etc/hosts`, Chromium policy or units.
- `ARCHITECTURE.md` is the contract. Build what it says; where it is wrong or
  missing, fix the document in the same change. Anything that changes what
  counts as loosening, the signing or acceptance procedure, the trust store,
  the units' self-protection or the HTTP API is a contract change: stop and
  ask your expert before building it.
- Fail closed: a daemon that is down, unreachable or rebooted never results in
  less blocking. Nothing you build helps bypass a block or shortens a bypass in
  `docs/threat-model.md`.
- Python: stdlib only, type hints, `ruff`-clean, a docstring at the top of each
  file, a test for every Python change. JS: plain ES2022, `"use strict"`, no
  dependencies. QML: colours from the shell theme, never hard-coded; no
  symlinks inside the plugin. Copy shown to users is calm and short: no
  exclamation marks, no emoji, no shaming.
- One change per branch. Branches `agents/<your instance>`, PRs target `main`;
  hand the reviewed branch to `calma-expert`, who verifies and lands it;
  `calma-maintainer` reviews and merges on Joan's yes.
- Brief your `code-reviewer` with `.agents/skills/review-guidelines/SKILL.md`.

## Docs to keep true

- `ARCHITECTURE.md`: any change to a component's behaviour, the file layout,
  the rules file, the HTTP API, the state file or the install locations.
- `docs/threat-model.md`: any change to what a layer stops or to a bypass.
- `README.md` (CLI reference, bar widget, how blocking works) and `SETUP.md`:
  any command, option, output or setup step that changes.
- `CONTRIBUTING.md`: any change to the test commands, dev mode or style rules.
- `chromium/extension/README.md`: any change to the extension's files or
  behaviour.
