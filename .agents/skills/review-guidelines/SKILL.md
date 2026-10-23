---
name: review-guidelines
description: How a change to calma is reviewed. Load it to review a pull request to this repository, to self-review before opening one, or to write a change that will pass review. This is the project's part of the review; the method (direction, correctness, security, mergeability; adversarial, security and simplification passes) is the reviewer's own.
---

# Reviewing a change to calma

Calma is a distraction blocker for Omarchy that is hard to argue with: a stdlib-only Python
daemon and CLI (`calma/`), a Chromium MV3 extension (`chromium/extension/`), the Omarchy bar
plugin at the repository root, a shell installer with systemd units (`install.sh`,
`packaging/`) and the landing site (`site/`). A change usually touches one of these and the
documents that describe it; `ARCHITECTURE.md` is the contract between them all.

## What every change keeps true

- **`ARCHITECTURE.md` is the contract.** Every component follows it exactly; a change that
  departs from it fixes the document in the same change, or it is a finding.
- **Tightening is free, loosening needs the password.** `rules.is_loosening` decides what
  counts as loosening (the list is in `ARCHITECTURE.md`, "Rules file"); the CLI writes
  `rules.sig` before `rules.json`; the daemon accepts a loosening only with a valid
  signature by the *accepted* `key.pub`, and reverts, logs and notifies otherwise. A change
  to any of this, to the trust store or to the units' self-protection is a contract change
  that needs Joan's explicit yes, and the review says so.
- **Fail closed.** The daemon down, unreachable or rebooted never results in less blocking:
  the policy file and hosts block stay, the extension keeps its blocked page and dynamic
  rules, an open session is charged in full after a reboot, a clock moved back grants
  nothing.
- **No bypass gets shorter.** `docs/threat-model.md` lists each known bypass with its step
  count. A change that shortens one is a finding; a change that adds one updates the
  document.
- **Enforcement is idempotent and cheap:** compute, compare, write only on change, log each
  change. Policy writes always include both `URLBlocklist` and `URLAllowlist`.
- **Single sources of truth:** `calma/presets.py` for presets (the extension takes domain
  lists from `/status`); `calma/paths.py` for every path, honouring `CALMA_ROOT`; the
  version string agrees across `pyproject.toml`, `calma/__init__.py`, `manifest.json` and
  `chromium/extension/manifest.json`.
- **Conventions** (`CONTRIBUTING.md`, "Style"): Python stdlib only, type hints, `ruff`-clean,
  a docstring at the top of each file, a test when it is Python; JS plain ES2022,
  `"use strict"`, no bundler, no dependencies; QML colours from the shell theme; **no
  symlinks anywhere in the repository** (the repository root is the Omarchy plugin and
  `omarchy plugin validate .` refuses them, so the souls' `CLAUDE.md` and a `.claude/skills`
  link are left out here on purpose); no telemetry, no network calls except loopback; copy shown to
  users is calm and short, no exclamation marks, no emoji, no shaming. One change per pull
  request.

## The test gate

There is no CI. The gate is `CONTRIBUTING.md`, "Running the tests", run in the checkout:

```sh
uvx pytest          # Python tests under tests/, with CALMA_ROOT in a temporary directory
uvx ruff check
bash -n install.sh && uvx --from shellcheck-py shellcheck install.sh
systemd-analyze verify packaging/*.service packaging/*.timer
./dev/plugin/run    # bar plugin: ruff, node tests, qmllint, Service.qml under QtTest
omarchy plugin validate .
```

`systemd-analyze verify` complaining that `/usr/lib/calma/calmad` is not executable is
expected when Calma is not installed. A check that cannot run on the reviewer's machine
(no Omarchy, no Qt) is stated as not checked, never assumed. Nothing in a review runs
`sudo ./install.sh` or touches the real system: use `./install.sh --destdir <tmp>` and
dev mode (`CONTRIBUTING.md`, "Dev mode").

## Consumers to read

- **`calma/daemon.py` (the HTTP API, `/status` fields):** its three clients,
  `chromium/extension/background.js`, `Service.qml` with `Model.js`, and `calma/cli.py`.
- **`calma/rules.py`, `calma/presets.py`:** the policy and hosts renderers in
  `calma/enforce/`, the extension's redirects, `SETUP.md` and the README's "The model".
- **`calma/paths.py`, any file added, moved or renamed:** `install.sh`, `packaging/`,
  `calma uninstall`, `calma doctor` and `tests/conftest.py`.
- **`install.sh`, `packaging/`:** `setup` (the plugin hook that runs it), `SETUP.md`, the
  README's "Quickstart" and "Uninstalling", and the upgrade path (`systemctl kill`, since
  the unit refuses restart).
- **The plugin files at the root:** `manifest.json`'s schema and entry points,
  `dev/plugin/` (mock daemon, tests), `omarchy plugin validate .`.
- **`calma/cli.py` (a command, option or output):** the README's "CLI reference",
  `SETUP.md`, `tests/test_cli.py`.
- **`site/`:** served as static files through Vercel (`vercel.json`); nothing builds it.

## Out of scope for a review here

- How a given machine installs or configures Calma: that is the person's own setup, not
  this repository.
- The Omarchy shell, Chromium policy semantics and systemd behaviour themselves: a change
  works with them as they are and documents the assumption.
- Taste in the minimal versions of X and YouTube (which elements to hide) beyond what
  `ARCHITECTURE.md` lists: that is a direction question for Joan, not a review finding.
