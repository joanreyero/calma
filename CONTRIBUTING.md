# Contributing to Calma

Thanks for taking a look. Calma is small on purpose: a stdlib-only Python daemon and CLI, a
plain-JS Chromium extension, a QML bar plugin and a shell installer. Read
[ARCHITECTURE.md](ARCHITECTURE.md) first; it is the contract every part follows. If you find
it wrong or ambiguous, fix the document in the same change.

## Running the tests

```sh
uvx pytest          # Python tests under tests/
uvx ruff check      # lint
bash -n install.sh && uvx --from shellcheck-py shellcheck install.sh
systemd-analyze verify packaging/*.service packaging/*.timer
```

The tests run with `CALMA_ROOT` pointing at a temporary directory, so they never touch the
real system and do not need root. `systemd-analyze verify` complains that
`/usr/lib/calma/calmad` is not executable when Calma is not installed; that is expected.

## Dev mode

Set `CALMA_ROOT` to run everything against a scratch tree instead of `/`:

```sh
export CALMA_ROOT=/tmp/calma-dev
export CALMA_PORT=7390          # default
PYTHONPATH=. python3 -m calma.daemon &   # the daemon, no root needed
PYTHONPATH=. python3 -m calma add x --preset x --sessions 2 --minutes 5
PYTHONPATH=. python3 -m calma status
```

In dev mode every absolute path is prefixed with `CALMA_ROOT` (`/tmp/calma-dev/etc/calma/rules.json`,
`/tmp/calma-dev/etc/hosts`, …), processes are only logged rather than killed unless
`CALMA_KILL=1`, and notifications go to stderr. Inspect `$CALMA_ROOT/etc/chromium/policies/managed/calma.json`
and `$CALMA_ROOT/etc/hosts` to see what the daemon would have written.

To try the installer without touching the system: `./install.sh --destdir /tmp/calma-pkg`
prefixes every path and skips `systemctl`.

## Loading the extension unpacked

With a dev daemon running on `127.0.0.1:7390`:

1. Open `chrome://extensions` in Chromium, turn on Developer mode.
2. Load unpacked, pick the `extension/` directory of your checkout.
3. Reload the extension after editing files; the service worker's console is behind the
   "service worker" link on the card.

For the real installed copy, `/etc/chromium-flags.conf` loads `/usr/share/calma/extension`;
`sudo ./install.sh` re-copies it, then restart Chromium.

## Style

- Python: stdlib only, type hints, `ruff`-clean, a docstring at the top of each file saying
  what it does.
- JS: plain ES2022, `"use strict"`, no bundler, no dependencies.
- Copy shown to users is calm and short: no exclamation marks, no emoji, no shaming.
- One change per pull request, with a test when it is Python.
