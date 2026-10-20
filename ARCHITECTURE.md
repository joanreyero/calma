# Calma: architecture and contract

Calma is a bulletproof-by-design distraction blocker for Omarchy (Arch Linux + Hyprland + Chromium).
You define **rules** (a set of websites and/or apps) with a **daily budget in sessions**
("X: twice a day, 5 minutes each"). Outside a session the sites and apps are blocked at the
system level; inside a session you only get the **minimal** version of the site (X: only the
people you follow; YouTube: no home feed, no Shorts, no recommendations).

Rules can be **tightened** at any time. **Loosening** anything (raising a budget, removing a
site, removing a rule, uninstalling) requires the **Calma password**, set once at install. Give
the password to someone you trust and you can't loosen anything yourself. Never set one and
nothing can ever be loosened.

This document is the contract between the components. Every component must follow it exactly;
when something here is wrong or missing, fix this document first.

## Honesty about limits

Linux gives root the final word. Calma is designed to defeat **impulse**, not a determined
administrator with an hour to spare. Every bypass requires a deliberate, multi-step act as root
(stopping a service that refuses to stop, editing files the daemon rewrites, deleting the
daemon's own trust store). The README and `calma doctor` must say this plainly. The layers:

1. **Chromium managed policy** (`/etc/chromium/policies/managed/calma.json`): `URLBlocklist` /
   `URLAllowlist`. Mandatory for every Chromium instance of every user; the user can't override
   it in the browser. Primary layer.
2. **Hosts file** (`/etc/hosts`, a block between `# calma:begin` and `# calma:end`): other
   browsers and tools can't resolve blocked domains while a rule is blocked.
3. **Process killing**: rules may list process names; while blocked they're SIGKILLed within 1 s.
4. **Browser extension** (loaded via `--load-extension`): the minimal versions, the nice blocked
   page, the countdown, and closing of open tabs the moment a session ends. The daemon
   re-adds the flag if it disappears.
5. **Self-protection**: `RefuseManualStop=yes`, `Restart=always`, a watchdog timer that
   re-enables and restarts the daemon and repairs the policy and flags; the rules file is only
   accepted when it's not a loosening or is signed with the password-protected key; tampering
   is logged and shown as a desktop notification.

## Components and layout (public repo)

```
calma/                     Python 3.12+ package, stdlib only (no pip deps). Daemon + CLI.
  __init__.py              __version__
  __main__.py              `python -m calma` → cli.main()
  cli.py                   `calma` command (argparse, pretty output)
  daemon.py                `calmad` main loop + HTTP API
  rules.py                 Rules model, validation, loosening comparison, presets expansion
  presets.py               Built-in presets (x, youtube) — the single source of truth
  state.py                 Day budget / session state, persistence, clock handling
  enforce/
    chromium.py            Policy JSON generation + writing; chromium-flags.conf repair
    hosts.py               /etc/hosts managed block
    procs.py               Process scanning and killing
  crypto.py                Password → key handling via the `openssl` CLI; sign / verify
  theme.py                 Reads the Omarchy theme colours for the UI
  notify.py                Desktop notifications to the logged-in user, from root
  paths.py                 All filesystem paths; honours CALMA_ROOT (dev mode)
bin/calma                  Entry point: exec python3 -m calma "$@" (PYTHONPATH=/usr/lib/calma)
bin/calmad                 Entry point for the daemon: python3 -m calma.daemon
extension/                 Chromium MV3 extension (plain JS, no build step)
plugin/                    Omarchy bar plugin (Quickshell QML), id io.github.joanreyero.calma
packaging/
  calma.service            systemd system unit for the daemon
  calma-watchdog.service   oneshot repair unit
  calma-watchdog.timer     every minute
  chromium-flags.conf      the line installed to /etc/chromium-flags.conf
install.sh                 idempotent root installer (also used for upgrades)
tests/                     pytest for the Python package (run with CALMA_ROOT in a tmp dir)
docs/threat-model.md       what Calma does and does not protect against
README.md, LICENSE (MIT)
```

### Installed locations (all root-owned)

| Path | What |
|---|---|
| `/usr/lib/calma/calma/` | The Python package |
| `/usr/bin/calma`, `/usr/lib/calma/calmad` | CLI and daemon entry points |
| `/usr/share/calma/extension/` | The Chromium extension (unpacked) |
| `/etc/calma/rules.json` | The rules and settings (mode 0644) |
| `/etc/calma/rules.sig` | Base64 Ed25519 signature of `rules.json`, present only when the last write was a password-authorised loosening |
| `/etc/calma/key.pem`, `/etc/calma/key.pub` | Password-encrypted Ed25519 private key, and its public key. Absent = no password = nothing can ever be loosened |
| `/etc/calma/key.pub.sig` | Signature of the current `key.pub` by the *previous* key, when the password was changed |
| `/var/lib/calma/state.json` | Day usage, sessions (mode 0644: readable so status works without root) |
| `/var/lib/calma/accepted/` | The daemon's trust store: `rules.json` and `key.pub` as last accepted |
| `/etc/chromium/policies/managed/calma.json` | Generated Chromium policy |
| `/etc/chromium-flags.conf` | Contains `--load-extension=/usr/share/calma/extension` |
| `/etc/systemd/system/calma.service`, `calma-watchdog.service`, `calma-watchdog.timer` | Units |
| `/run/calma/` | Runtime dir (pid, nothing else) |

**Dev mode:** if the environment variable `CALMA_ROOT` is set (e.g. `/tmp/calma-dev`), every
absolute path above is prefixed with it (`$CALMA_ROOT/etc/calma/rules.json`, `$CALMA_ROOT/etc/hosts`,
…), the HTTP port is `CALMA_PORT` (default 7390), process killing only logs what it would kill
unless `CALMA_KILL=1`, notifications print to stderr, and root is not required. All tests run this way.

## Rules file: `/etc/calma/rules.json`

```json
{
  "version": 1,
  "settings": {
    "day_starts_at": 4
  },
  "rules": {
    "x": {
      "preset": "x",
      "label": "X",
      "domains": [],
      "processes": [],
      "sessions_per_day": 2,
      "session_minutes": 5,
      "daily_minutes": 10,
      "cooldown_minutes": 0
    },
    "youtube": {
      "preset": "youtube",
      "sessions_per_day": 1,
      "session_minutes": 20,
      "daily_minutes": 20,
      "cooldown_minutes": 0
    },
    "steam": {
      "label": "Steam",
      "domains": ["store.steampowered.com"],
      "processes": ["steam"],
      "sessions_per_day": 1,
      "session_minutes": 60,
      "daily_minutes": 60,
      "cooldown_minutes": 0
    }
  }
}
```

- Rule names: `[a-z0-9][a-z0-9-]{0,31}`.
- `preset` (optional): `"x"` or `"youtube"`; contributes domains, hosts, minimal path rules and
  a label/glyph. `domains` and `processes` are added on top.
- `domains`: bare registrable domains (`example.com`); Chromium matches subdomains. Lowercased,
  no scheme, no path.
- `processes`: process names (`comm`) or executable basenames.
- `sessions_per_day` ≥ 0 (0 = fully blocked, always), `session_minutes` ≥ 1,
  `daily_minutes` ≥ 1, `cooldown_minutes` ≥ 0 (minimum gap between the end of one session and
  the start of the next). The CLI derives `daily_minutes = sessions_per_day * session_minutes`
  when not given.
- `settings.day_starts_at`: hour 0–23 at which the budget resets (default 4).

**Loosening** (`rules.is_loosening(old, new) -> list[str]` returns human-readable reasons, empty
if none): any of these is loosening: a rule removed; a domain or process removed from a rule; a
preset removed from a rule; `sessions_per_day`, `session_minutes` or `daily_minutes` increased;
`cooldown_minutes` decreased; `day_starts_at` changed at all; `version` changed. Anything else
(adding rules/domains/processes, decreasing budgets, label changes) is tightening or neutral.

## Presets (`calma/presets.py`)

The single source of truth for minimal versions on the policy side. The extension has its own
copy of the *cosmetic* rules (CSS/DOM) but must take the domain lists from `/status`.

```python
PRESETS = {
  "x": {
    "label": "X",
    "glyph": "𝕏",
    "domains": ["x.com", "twitter.com"],
    # exact host names for /etc/hosts
    "hosts": ["x.com", "www.x.com", "mobile.x.com", "twitter.com", "www.twitter.com", "mobile.twitter.com"],
    # when a session is open: allow everything on these patterns…
    "allow": ["x.com", "twitter.com"],
    # …except these (more specific, so they win)
    "block": ["x.com/explore", "x.com/i/grok", "x.com/i/trending", "x.com/i/communities",
              "x.com/i/premium_sign_up", "x.com/i/verified-choose", "x.com/jobs",
              "twitter.com/explore", "twitter.com/i/grok", "twitter.com/i/trending"],
    "home": "https://x.com/home",
  },
  "youtube": {
    "label": "YouTube",
    "glyph": "",
    "domains": ["youtube.com", "youtu.be", "youtube-nocookie.com"],
    "hosts": ["youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be", "www.youtube-nocookie.com"],
    "allow": ["youtube.com/watch", "youtube.com/results", "youtube.com/feed/subscriptions",
              "youtube.com/feed/library", "youtube.com/feed/history", "youtube.com/feed/playlists",
              "youtube.com/feed/you", "youtube.com/playlist", "youtube.com/@", "youtube.com/channel/",
              "youtube.com/c/", "youtube.com/user/", "youtube.com/embed/", "youtube-nocookie.com/embed/",
              "youtu.be", "youtube.com/account", "youtube.com/paid_memberships", "youtube.com/premium"],
    "block": [],   # home, /shorts, /feed/trending, /gaming stay blocked because only the allow list opens paths
    "home": "https://www.youtube.com/feed/subscriptions",
  },
}
```

Chromium precedence: the most specific filter (longest host+path) wins; `URLAllowlist` wins
ties. `youtube.com` in the blocklist plus `youtube.com/watch` in the allowlist blocks home and
Shorts and allows watching. Generated policy for the file above with `x` open and `youtube` blocked:

```json
{
  "URLBlocklist": ["youtube.com", "youtu.be", "youtube-nocookie.com",
                   "x.com/explore", "x.com/i/grok", "..."],
  "URLAllowlist": ["x.com", "twitter.com"]
}
```

For a blocked rule: every domain of the rule goes into `URLBlocklist`, nothing in the allowlist.
For an open rule with a preset: `allow` → `URLAllowlist`, `block` → `URLBlocklist`, and the
rule's plain `domains` (non-preset extras) → `URLAllowlist`. For an open rule without a preset:
`domains` → `URLAllowlist`. Always write both keys (possibly empty lists) so a stale policy from
an older version can't linger. Rewrite the file only when its content changes (Chromium
reloads policy files on change).

## State and time

`/var/lib/calma/state.json`:

```json
{
  "day": "2026-10-20",
  "rules": {
    "x": {"sessions_used": 1, "seconds_used": 142, "last_session_end": 1760970000.0},
    "youtube": {"sessions_used": 0, "seconds_used": 0, "last_session_end": null}
  },
  "sessions": {
    "x": {"started_wall": 1760969858.0, "started_mono": 12345.6, "ends_mono": 12645.6, "minutes": 5}
  },
  "tamper_events": [{"at": 1760969000.0, "what": "rules.json loosened without a valid signature; kept previous rules"}]
}
```

- "Today" is the local date of `now - day_starts_at hours`. When it differs from `day`, usage is
  reset. If the local date goes **backwards** past `day` (clock moved back), nothing is reset
  and nothing is granted: compare dates as strings and only reset when `today > day`.
- Sessions use `time.monotonic()`... but monotonic doesn't advance during suspend and resets on
  reboot, so **use `time.clock_gettime(time.CLOCK_BOOTTIME)`** for `*_mono` fields, and store
  the boot id (`/proc/sys/kernel/random/boot_id`) in the state. After a reboot, any session in
  `sessions` is ended and its full `minutes` are charged (fail closed).
- Every loop tick (1 s): end expired sessions, charge `seconds_used` for running sessions
  (increment by the real elapsed boottime, not 1), persist on change, re-render enforcement.
- A session may start when: rule exists; no session is running for it; `sessions_used <
  sessions_per_day`; `seconds_used < daily_minutes*60`; and `now - last_session_end >=
  cooldown_minutes*60`. Its length is `min(requested or session_minutes, session_minutes,
  remaining_daily)`. Starting charges `sessions_used += 1` immediately.
- Closing early stops the charge at the real time used; the rest of the daily budget remains.
- When a session ends (expiry or close): `last_session_end = now`, enforcement re-renders at
  once, a notification is sent ("X · session over · 1 session left today"). One minute before
  expiry: a notification ("X · 1 minute left").

## The HTTP API (daemon, `127.0.0.1:7390`, loopback only)

Plain `http.server` in a thread. JSON everywhere, `Content-Type: application/json`. Every
response includes `Access-Control-Allow-Origin: *` (nothing here is secret and starting a
session is never a loosening). POST bodies are JSON. **POSTs are only accepted with the header
`X-Calma: 1`** (so a web page can't trigger a simple cross-origin POST; the extension and CLI
send it).

### `GET /status`

```json
{
  "version": "0.1.0",
  "now": 1760969900.0,
  "day": "2026-10-20",
  "day_starts_at": 4,
  "next_reset": 1761015600.0,
  "password_set": true,
  "healthy": true,
  "problems": [],
  "rules": {
    "x": {
      "label": "X",
      "glyph": "𝕏",
      "preset": "x",
      "domains": ["x.com", "twitter.com"],
      "processes": [],
      "home": "https://x.com/home",
      "sessions_per_day": 2, "session_minutes": 5, "daily_minutes": 10, "cooldown_minutes": 0,
      "sessions_used": 1, "seconds_used": 142,
      "sessions_left": 1, "seconds_left": 458,
      "state": "open",
      "session": {"ends_at": 1760970158.0, "seconds_left": 258, "minutes": 5},
      "can_open": false,
      "cannot_open_reason": null,
      "next_opportunity": null
    },
    "youtube": {
      "...": "...",
      "state": "blocked",
      "session": null,
      "can_open": true,
      "cannot_open_reason": null,
      "next_opportunity": null
    },
    "steam": {
      "...": "...",
      "state": "blocked",
      "can_open": false,
      "cannot_open_reason": "no sessions left today",
      "next_opportunity": 1761015600.0
    }
  }
}
```

`state` ∈ `"open"`, `"blocked"`. `cannot_open_reason` ∈ `null`, `"already open"`, `"no sessions
left today"`, `"no time left today"`, `"cooling down"`, `"always blocked"`. `next_opportunity`
is a unix timestamp when something is known (reset or cooldown end), else null.
`domains` is the fully expanded list (preset + extras). `problems` is a list of strings from the
self-check (missing policy file, extension flag missing in a user's chromium-flags.conf, tamper
events today, …).

### `POST /open` `{"rule": "x", "minutes": 5}` (minutes optional)

→ `200 {"ok": true, "rule": {...as in /status...}}` or `409 {"ok": false, "error": "no sessions
left today", "rule": {...}}` or `404 {"ok": false, "error": "unknown rule"}`.

### `POST /close` `{"rule": "x"}` → `200 {"ok": true, "rule": {...}}` (idempotent).

### `GET /theme`

Colours of the Omarchy theme of the primary user (see Theme), for the blocked page and plugin:

```json
{"mode": "dark", "name": "tokyo-night", "colors": {"accent": "#7aa2f7", "background": "#1a1b26", "...": "..."}}
```

`colors` is the flat key→hex map from `colors.toml`. A built-in fallback palette is returned when
no theme is found. Also `"font": "JetBrainsMono Nerd Font"` best effort (null if unknown).

### `POST /ping` `{"extension": "0.1.0"}` → `{"ok": true}`

The extension calls this on service-worker startup and every 30 s. `/status.problems` reports
"extension not seen for N min while Chromium is running" when a Chromium process is running
and no ping arrived for 2 minutes.

### `GET /events?since=<unix>`

Long-poll (up to 25 s) that returns `{"changed": true, "status": {...}}` as soon as any rule's
`state` changes or `since` is older than the last change, else `{"changed": false}`. The
extension and plugin use it to react within milliseconds when a session ends.

## Rule changes: how the CLI writes `/etc/calma/rules.json`

All rule edits are done by the CLI running as root (it re-execs itself with `sudo` and says
why). Procedure (`rules.write_rules(new, password=None)`):

1. Load current file → `old`. Compute `reasons = is_loosening(old, new)`.
2. If `reasons` is empty: write the file atomically (temp + rename, 0644), delete `rules.sig`.
3. If loosening: if no `key.pem` exists → refuse: "Calma has no password, so nothing can be
   loosened. Ever." If a password is given (prompt with `getpass`, or `--password-file`): sign
   the exact new file bytes with the private key (`crypto.sign(data, password)`), write
   `rules.sig` (base64) **before** `rules.json` (so the daemon never sees an unsigned loosened
   file), then the file. Wrong password → refuse, exit 1, no change.
4. The daemon watches the file (mtime/inode every tick) and runs the acceptance procedure.

**Daemon acceptance** (`daemon.load_rules()`), with the trust store `/var/lib/calma/accepted/`:

1. First run (no accepted rules yet): accept whatever is there (or an empty rule set), copy to
   the trust store. Copy `key.pub` too if present.
2. Otherwise `reasons = is_loosening(accepted, new)`. Empty → accept, update the trust store.
3. Loosening: verify `rules.sig` over the file bytes with the **accepted** `key.pub`. Valid →
   accept, update the trust store. Invalid or missing → keep the accepted rules, append a
   `tamper_events` entry, notify the user ("Calma: rules were loosened without the password;
   ignored"), and overwrite `/etc/calma/rules.json` with the accepted copy.
4. `key.pub` differs from the accepted one: accept only if `key.pub.sig` is a valid signature
   of the new `key.pub` bytes by the accepted key (or there was no accepted key). Otherwise
   restore the accepted `key.pub` and `key.pem` is untrusted: record a tamper event.

## Crypto (`calma/crypto.py`), all via the `openssl` CLI (always present on Arch)

- `generate_key(password) -> (pem_bytes, pub_bytes)`: `openssl genpkey -algorithm ed25519`, then
  `openssl pkey -aes-256-cbc -passout pass:…` (pass the password via `-passout fd:` or stdin, never
  argv). `pub`: `openssl pkey -pubout`.
- `sign(data, password) -> bytes`: `openssl pkeyutl -sign -inkey key.pem -passin … -rawin -in data`.
- `verify(data, sig, pub) -> bool`: `openssl pkeyutl -verify -pubin -inkey key.pub -rawin -in data -sigfile sig`.
- `check_password(password) -> bool`: can the key be decrypted.
- Password changes (`calma password`): need the old password when a key exists; write new
  `key.pem`/`key.pub` plus `key.pub.sig` = sign(new pub bytes) with the old key.

## Theme (`calma/theme.py`)

Primary user = `CALMA_USER` setting if present in `/etc/calma/rules.json` `settings.user`, else
the owner of the first `/home/*` with a `.local/state/omarchy` dir. Read
`~user/.local/state/omarchy/current/theme/colors.toml` with `tomllib` and
`~user/.local/state/omarchy/current/theme.name`. Fallback palette = Tokyo Night values.

## CLI (`calma`)

Pretty, calm, no emoji; Nerd Font glyphs are fine. Uses the terminal's own 16 ANSI colours (so
the Omarchy terminal theme applies). Commands:

| Command | Does | Needs |
|---|---|---|
| `calma` / `calma status` | Table: rule, state (open 4:32 left / blocked), today's sessions `1/2`, minutes `3/10 min`, next opportunity. `--json` prints `/status` | daemon |
| `calma open <rule> [minutes]` | Start a session; prints the result in a sentence | daemon |
| `calma close [rule]` | End the session (all sessions if no rule) | daemon |
| `calma add <name> [--preset x\|youtube] [--domain d]... [--process p]... --sessions N --minutes M [--daily D] [--cooldown C] [--label L]` | Add a rule (tightening, no password) | root |
| `calma set <name> [--sessions N] [--minutes M] [--daily D] [--cooldown C]` | Change budgets; loosening asks the password | root (+password) |
| `calma domain add\|remove <name> <domain>`, `calma process add\|remove <name> <proc>` | Edit members; remove asks the password | root (+password) |
| `calma remove <name>` | Remove a rule | root + password |
| `calma password` | Set (first time) or change (asks the old one) the password | root |
| `calma list` | Rules and presets as configured | — |
| `calma presets` | Show the built-in presets | — |
| `calma doctor` | Check every layer and print OK / problem per line; exit 1 if any | — |
| `calma log [-n N]` | `journalctl -u calma` | — |
| `calma uninstall` | Full removal (stops units, removes files, restores hosts/policy/flags) | root + password |
| `calma --version` | | |

`calma open`/`close` are also what the blocked page and the bar plugin use (via the API).

## Daemon (`calmad`)

- Runs as root, single process, one loop thread (1 s tick) + HTTP server thread.
- Startup: ensure dirs, load state, load/accept rules, render enforcement, start HTTP, log
  `calma <version> started: N rules`.
- Each tick: reload rules if the file changed; time/day bookkeeping; render policy, hosts,
  kill processes for blocked rules; repair `/etc/chromium-flags.conf` and each `/home/*/.config/
  chromium-flags.conf` (follow symlinks; if a `--load-extension=` line exists without our path,
  append `,/usr/share/calma/extension` to it; if no such line exists do nothing to the user file
  because `/etc/chromium-flags.conf` covers it); send notifications; persist state.
- Enforcement must be **idempotent and cheap**: compute desired content, compare, write only
  on change; log each change at info level (`policy: x open, youtube blocked`).
- `SIGTERM`: flush state and exit (systemd restarts it; the unit refuses manual stop).
- `calmad --watchdog`: used by the timer. Ensures `calma.service` is enabled and active
  (`systemctl unmask/enable/start`), the policy file exists, flags are present. Prints what it
  repaired.
- Logging to stderr (journald). No log files.

## Extension (`extension/`, MV3, plain JS)

- `manifest.json`: `permissions: ["tabs", "webNavigation", "declarativeNetRequest", "alarms", "storage"]`,
  `host_permissions: ["http://127.0.0.1:7390/*", "*://*.x.com/*", "*://*.twitter.com/*", "*://*.youtube.com/*", "*://youtu.be/*", "*://*.youtube-nocookie.com/*", "<all_urls>"]` (all_urls is needed to redirect arbitrary custom domains; keep the explicit list for clarity), `web_accessible_resources: [blocked.html]`.
- **Service worker** `background.js`: fetches `/status`; long-polls `/events`; keeps a `status`
  cache; pings `/ping` on start and every 30 s (alarm); maintains **declarativeNetRequest dynamic
  rules**: for every *blocked* rule's domain a `main_frame` redirect to
  `blocked.html?rule=<name>&url=<encoded original>`; for open rules with a preset `home` redirect
  (YouTube `/` → `/feed/subscriptions`; X `/` → `/home`). Also `webNavigation.onCommitted` → if the
  URL matches a blocked rule, `tabs.update` to the blocked page (belt and braces for the
  Chromium policy error page). When a rule flips to blocked, every open tab on its domains is
  navigated to the blocked page. Answers messages from content scripts: `{type: "status"}` →
  cached status; `{type: "open", rule, minutes}` / `{type: "close", rule}` → POST and return
  the result. Badge: `mm:ss` of the soonest-ending session, none otherwise. Handles the daemon
  being down: shows the blocked page with "Calma isn't running" and blocks everything with a rule
  (fail closed): the DNR rules persist, and the cached status is kept in `chrome.storage.session`.
- **Blocked page** `blocked.html` + `blocked.js` + `blocked.css`: themed with `/theme` colours
  (CSS custom properties; fallback palette inline). Shows the rule's glyph and label, a calm
  one-line message, "Sessions today: 1 of 2 · 5 min each · 7 min left", and one primary button
  "Start a 5-minute session" (or a small minutes input when the remaining budget is less than
  `session_minutes`). On success → `location.replace(originalUrl or rule.home)`. When
  `can_open` is false: "Back at 04:00" / "Cooling down until 15:32" with no button. A footer
  line: "Calma · calma status in a terminal". Nice typography; respects `prefers-reduced-motion`.
- **Content scripts:**
  - `content/countdown.js` + `countdown.css` on all rule domains (match `<all_urls>`, activate only
    if the hostname belongs to a rule per status): a small fixed pill bottom-right "Calma · 4:32"
    (accent colour; red under 60 s) with an "End now" link that closes the session; asks the
    SW for status every 2 s (keeps the SW alive); when the rule is blocked, `location.replace`
    to the blocked page.
  - `content/x.js` + `x.css` on x.com/twitter.com: force the **Following** timeline (if the
    "For you" tab is selected on `/home`, click "Following"; hide the "For you" tab), hide the
    right sidebar (`[data-testid="sidebarColumn"]`: trends, who to follow, premium upsells),
    hide left nav items Explore, Grok, Communities, Premium, Jobs, Verified Orgs, Business,
    Monetization; hide promoted posts (`[data-testid="placementTracking"]`), hide "Discover more"
    sections in threads (the "Discover more" heading and everything after it in the column),
    hide "Who to follow"/"You might like" asides inline. MutationObserver, idempotent, resilient
    to re-renders, no layout jumps (CSS first, JS only for what CSS can't do).
  - `content/youtube.js` + `youtube.css`: hide the home feed and Shorts shelves (`ytd-rich-grid-renderer`
    on home, `ytd-reel-shelf-renderer`, `ytd-rich-shelf-renderer[is-shorts]`), the watch-page
    related column (`#secondary`, `#related`), end screens (`.ytp-endscreen-content`, `.ytp-ce-element`),
    autoplay toggle off (`.ytp-autonav-toggle-button[aria-checked="true"]` → click), comments
    (`ytd-comments`), the guide's Shorts/Explore/Trending entries, and "Up next" in miniplayer.
    Redirect `/` and `/shorts/*` to `/feed/subscriptions`. Subscriptions, search, channel pages,
    playlists and watch keep working.
- Everything degrades safely: if the daemon is unreachable, blocked pages still show (fail
  closed) and content scripts keep their cosmetic rules on.

## Bar plugin (`plugin/`, Omarchy shell, Quickshell QML)

- Study `/usr/share/omarchy/docs/` (bar, panels, services), the built-in plugins in
  `/usr/share/omarchy/shell/plugins/` and the installed `~/.config/omarchy/plugins/37signals.hey`
  before writing anything; follow the `manifest.json` schema used there.
- Id `io.github.joanreyero.calma`, kinds `["bar-widget"]`, a `Service.qml` that polls
  `GET /status` every 5 s (and `/events` if feasible) and a `Panel.qml` widget: hidden when no
  session is open and setting `hideWhenIdle` is true (default true); otherwise shows the rule
  glyph and `m:ss`. Click → a dropdown listing every rule: state, `1/2 · 7 min left`, and a
  button "Start 5 min" / "End now". Colours from the shell theme, never hard-coded.
- Installed by copying/symlinking `plugin/` to `~/.config/omarchy/plugins/io.github.joanreyero.calma`
  and adding `{"id": "io.github.joanreyero.calma"}` to `bar.layout.right` in `~/.config/omarchy/shell.json`
  (`install.sh` offers this with `--plugin` for the invoking user; never edits shell.json
  without being asked).

## Packaging (`install.sh`, `packaging/`)

- `install.sh`: `set -euo pipefail`; needs root (`sudo ./install.sh`); copies the package to
  `/usr/lib/calma/`, entry points, extension to `/usr/share/calma/extension/`, units; writes
  `/etc/chromium-flags.conf` line idempotently; creates `/etc/calma` and `/var/lib/calma` with
  a default empty rules file if missing (never overwrites an existing one); `systemctl daemon-reload`,
  `enable --now calma.service calma-watchdog.timer`; prints next steps (`sudo calma password`,
  `sudo calma add x --preset x --sessions 2 --minutes 5`, restart Chromium). Idempotent, safe to
  re-run for upgrades. `RefuseManualStop=yes` also refuses `systemctl restart`, so an upgrade
  sends `systemctl kill -s SIGTERM calma.service` after copying the files and `Restart=always`
  brings the new code up within a second.
- `calma.service`: `Type=simple`, `ExecStart=/usr/lib/calma/calmad`, `Restart=always`,
  `RestartSec=1`, `RefuseManualStop=yes`, `KillMode=process`, `WantedBy=multi-user.target`,
  `After=network.target`, `RuntimeDirectory=calma`, `StateDirectory=calma`,
  `ConfigurationDirectory=calma`, `StartLimitIntervalSec=0`.
- `calma-watchdog.timer`: `OnBootSec=30s`, `OnUnitActiveSec=1min`, `Persistent=true`.
- `calma-watchdog.service`: `Type=oneshot`, `ExecStart=/usr/lib/calma/calmad --watchdog`.

## Conventions

- Python: stdlib only, type hints, `ruff`-clean, small modules, docstring at the top of each
  file saying what it does. Tests with pytest under `tests/` using `CALMA_ROOT=tmp_path`.
- JS: plain ES2022 modules where MV3 allows, no bundler, no dependencies, `"use strict"`.
- No telemetry, no network calls except loopback.
- Copy is calm and short. No exclamation marks. No shaming.
