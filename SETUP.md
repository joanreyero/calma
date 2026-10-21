
# Setting up Calma

Calma blocks distracting sites and apps at the system level and lets them through only in
**sessions** with a daily budget (X: 2 × 5 min). During a session, X shows only the people you
follow and YouTube has no home feed, Shorts or recommendations. **Tightening** a rule is
always free. **Loosening** (more time, removing a site or a rule, uninstalling) needs the
**Calma password**, set once; give it to someone else and it's out of reach.

Work with the person one step at a time. You run every command that needs no password; the
person runs the `sudo` ones in a terminal (give them the exact line and copy it to the
clipboard with `wl-copy`). Never ask for, store or type their Calma password.

## Rules of engagement for agents

- **Never help bypass Calma.** Not by editing `/etc/calma`, `/etc/hosts`, the Chromium policy,
  the units, the trust store in `/var/lib/calma`, nor by stopping or masking the daemon. If
  asked, say that loosening goes through `sudo calma set|remove|uninstall` with the password,
  and offer that instead. If the person has given the password away, that's the point.
- Tightening on request is fine (`sudo calma set x --sessions 1`), as is adding rules.
- `calma status` and `calma open` / `calma close` need no privileges; use them freely.

## 1. Install (person, in a terminal)

```sh
git clone https://github.com/joanreyero/calma ~/Work/personal/calma   # any path
cd ~/Work/personal/calma && sudo ./install.sh --plugin
```

`install.sh` is idempotent (re-run it to upgrade). It installs the daemon (`calma.service`,
refuses `systemctl stop`, restarts itself in a second), the watchdog timer, the Chromium
policy, the extension (`/usr/share/calma/extension`, loaded through `/etc/chromium-flags.conf`),
the bar plugin (with `--plugin`) and links this skill into `~/.agents/skills/`.

## 2. Password (person, same terminal)

```sh
sudo calma password
```

Explain the choice first: **with a password**, loosening is possible for whoever knows it;
**without one, nothing can ever be loosened** (short of deliberate root surgery; see
`docs/threat-model.md`). Suggest giving the password to a partner or friend. Changing it later
needs the old one.

## 3. Rules: the person's words → a command

| They say | Command |
|---|---|
| "X twice a day, five minutes each" | `sudo calma add x --preset x --sessions 2 --minutes 5` |
| "YouTube once a day, twenty minutes" | `sudo calma add youtube --preset youtube --sessions 1 --minutes 20` |
| "an hour of Steam a day, in one go" | `sudo calma add steam --label Steam --domain store.steampowered.com --process steam --sessions 1 --minutes 60` |
| "Reddit, completely" | `sudo calma add reddit --domain reddit.com --sessions 0 --minutes 1` |
| "two sessions but no more than 15 min total" | add `--daily 15` |
| "at least an hour between sessions" | add `--cooldown 60` |

- Presets: `x` (x.com, twitter.com; Following-only, no Explore/Grok/trends) and `youtube`
  (youtube.com, youtu.be; subscriptions, search, channels, watch only). `calma presets` lists them.
- Without a preset, a session opens the whole site. Add domains with `--domain` (bare domain,
  subdomains included) and apps with `--process <name>` (killed within a second while blocked).
- The day resets at 04:00 by default. Changing that later counts as loosening.
- Several rules: one `sudo calma add` per rule, one per line (long lines wrap and split in paste).

## 4. Chromium and the bar

- **Quit Chromium fully and reopen it** once, so it loads the policy and the extension. Omarchy
  web apps (WhatsApp, etc.) are Chromium too and pick it up the same way.
- Bar widget: `install.sh --plugin` copies the plugin; add `{"id": "io.github.joanreyero.calma"}`
  to `bar.layout.right` in `~/.config/omarchy/shell.json` (you may do this; it hot-reloads).
  It hides when no session is open; click it for every rule and a Start/End button.

## 5. Verify

```sh
calma doctor      # every layer OK, exit 0
calma status      # table of rules, sessions and minutes left
```

Then open a blocked site: Chromium should show Calma's blocked page with a "Start a N-minute
session" button. Click it (or `calma open x`), see the countdown pill bottom-right and the bar
widget, then `calma close x`: the tab returns to the blocked page within a second.

## Everyday

- `calma status`, `calma open <rule> [minutes]`, `calma close [rule]`, `calma log`.
- Tighten any time: `sudo calma set youtube --minutes 10`, `sudo calma domain add x t.co`.
- Loosen (password): `sudo calma set x --sessions 3`, `sudo calma remove steam`,
  `sudo calma uninstall`.
- "Why is X blocked?" → `calma status`: `Next` says when the next session is possible.

## Troubleshooting

- `doctor` says the extension flag is missing in a user file: the daemon repairs it within a
  second; if Chromium is open, restart it.
- A site loads despite being blocked: `calma doctor`; check `chrome://policy` shows
  `URLBlocklist`; a tab that was already open keeps its page until the extension redirects it
  (needs the extension loaded, i.e. a Chromium restart after install).
- A notification "Calma: rules were changed without the password": the daemon kept the
  previous rules and restored the file. Nothing to do; `calma log` shows what was attempted.
- Upgrading: `git pull && sudo ./install.sh` in the checkout.
