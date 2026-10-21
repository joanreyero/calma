# Calma

Calma is a distraction blocker for Omarchy (Arch Linux, Hyprland, Chromium) that is hard to
argue with. You decide how many short sessions a day you get on X, YouTube or anything else;
outside those sessions the sites and apps are blocked at the system level, and inside them you
only get a minimal version of the site, without feeds, Shorts or recommendations.

## The model

- A **rule** is a set of websites and/or processes with a daily budget: `sessions_per_day`
  sessions of `session_minutes` each (X: twice a day, five minutes each).
- Outside a session the rule is **blocked**: Chromium refuses the sites, other programs cannot
  resolve the domains, and listed processes are killed as soon as they start.
- Inside a session you get the **minimal version**. For X that means only the people you
  follow: no For you, no Explore, no trends, no Grok. For YouTube: subscriptions, search,
  channels, playlists and watching, but no home feed, no Shorts, no recommendations, no
  autoplay, no comments.
- You can **tighten** a rule at any time: fewer sessions, shorter sessions, more domains.
- **Loosening** anything (more sessions, longer sessions, removing a domain, removing a rule,
  uninstalling) requires the **Calma password**. If you want it to be truly out of reach, set a
  password and give it to someone you trust. If you never set one, nothing can ever be loosened,
  short of the deliberate root-level steps described under "Honesty about root".

## Quickstart

The repository is an Omarchy shell plugin, so the bar widget comes first and installs the
rest for you:

1. `omarchy plugin add https://github.com/joanreyero/calma --enable`
2. Click the Calma leaf in the bar → **Set up with the agent**. Your default coding agent
   opens with [SETUP.md](SETUP.md) and walks you through the install (you run the `sudo`
   lines), the password choice and your first rules. Prefer to do it by hand? Follow
   [SETUP.md](SETUP.md) yourself.

The manual path, from the plugin directory or any clone:

```sh
cd ~/.config/omarchy/plugins/io.github.joanreyero.calma   # or: git clone https://github.com/joanreyero/calma && cd calma
sudo ./install.sh
sudo calma password               # optional, but recommended: see "The model"
sudo calma add x --preset x --sessions 2 --minutes 5
```

Then restart Chromium so it picks up the policy and loads the extension. From now on:

```sh
calma status          # what is blocked, what is open, how much is left today
calma open x          # start a 5-minute session
calma close x         # end it early; the unused time stays in today's budget
calma doctor          # check that every layer is in place
```

Opening a session also works from the blocked page Chromium shows, and from the bar widget.

## Bar widget

The checkout is the plugin: `manifest.json`, `Panel.qml`, `Service.qml`, `InstallProbe.qml`
and `Model.js` at the root, id `io.github.joanreyero.calma`. `omarchy plugin add` clones it
into `~/.config/omarchy/plugins/` and `--enable` puts it in the right section of the bar.

- With a session open it shows the rule's glyph and the countdown (`𝕏 4:32`). Idle, it hides
  unless the `hideWhenIdle` setting is off. Click for a dropdown listing every rule with its
  state, today's sessions and minutes left, and a **Start N min** / **End now** button. Right
  click ends every open session; middle click refreshes.
- Keyboard: `j`/`k` move, `enter` acts, `e` ends all, `r` refreshes.
- Not installed yet: the widget is always visible, the leaf dimmed, and the dropdown offers
  **Set up with the agent**. Installed but the daemon doesn't answer: "Calma isn't running",
  with `calma doctor` as the hint and an **Open a terminal** button.
- Settings (in the widget's `shell.json` entry): `hideWhenIdle` (default true) and `port`
  (default 7390, matching `CALMA_PORT`).
- IPC: `omarchy-shell calma status` (also `refresh`, `start <rule>`, `end [rule]`, `toggle`).

One `Service.qml` per shell polls `/status` every 5 s and long-polls `/events`, so a session
ending shows within milliseconds. Colours come from the shell theme. Development checks live
in `dev/plugin/` (`./dev/plugin/run`: ruff, node tests for `Model.js`, qmllint, and
`Service.qml` against a mock daemon under QtTest); `omarchy plugin validate .` checks the
manifest.

## CLI reference

| Command | Does | Needs |
|---|---|---|
| `calma` / `calma status` | Table: rule, state (open 4:32 left / blocked), today's sessions `1/2`, minutes `3/10 min`, next opportunity. `--json` prints the raw status | daemon |
| `calma open <rule> [minutes]` | Start a session; prints the result in a sentence | daemon |
| `calma close [rule]` | End the session (all sessions if no rule) | daemon |
| `calma add <name> [--preset x\|youtube] [--domain d]... [--process p]... --sessions N --minutes M [--daily D] [--cooldown C] [--label L]` | Add a rule (tightening, no password) | root |
| `calma set <name> [--sessions N] [--minutes M] [--daily D] [--cooldown C]` | Change budgets; loosening asks the password | root (+password) |
| `calma domain add\|remove <name> <domain>`, `calma process add\|remove <name> <proc>` | Edit members; remove asks the password | root (+password) |
| `calma remove <name>` | Remove a rule | root + password |
| `calma password` | Set (first time) or change (asks the old one) the password | root |
| `calma list` | Rules and presets as configured | |
| `calma presets` | Show the built-in presets | |
| `calma doctor` | Check every layer and print OK / problem per line; exit 1 if any | |
| `calma log [-n N]` | Daemon log (`journalctl -u calma`) | |
| `calma uninstall` | Full removal (stops units, removes files, restores hosts, policy and flags) | root + password |
| `calma --version` | | |

Commands that need root re-run themselves with `sudo` and say why. Budgets: `--daily` defaults
to `sessions × minutes`; `--cooldown` is the minimum gap in minutes between two sessions. The
day resets at 04:00 by default.

A rule without a preset works too: `sudo calma add steam --domain store.steampowered.com
--process steam --sessions 1 --minutes 60 --label Steam`.

## How blocking works

Five layers, each covering for the others:

1. **Chromium policy.** Calma writes a managed policy file that every Chromium window of every
   user obeys and that cannot be changed from inside the browser. While a rule is blocked its
   domains are on the blocklist; while it is open, only the minimal paths are allowed.
2. **Hosts file.** While a rule is blocked, its domains resolve to nowhere for the whole
   machine, so Firefox, curl or a desktop app cannot reach them either.
3. **Process killing.** Rules may list process names (`steam`, `discord`); while blocked they are
   killed within a second of starting.
4. **Browser extension.** Loaded by a flag in `/etc/chromium-flags.conf`. It draws the calm
   blocked page with the "Start a session" button and the countdown, hides the feeds and
   recommendations during a session, and closes the tabs the moment a session ends. The daemon
   puts the flag back if it disappears.
5. **Self-protection.** The daemon runs as a system service that refuses to be stopped and
   restarts in a second if killed; a timer checks every minute that it is enabled and running
   and repairs the policy and flags; the rules file is only accepted when the change is a
   tightening or is signed with the password-protected key. Anything else is reverted, logged
   and shown as a desktop notification.

## Honesty about root

Linux gives root the final word, and you are root on your own machine. Calma is built to
defeat impulse, not a determined administrator with an hour to spare. Every known bypass takes
several deliberate steps as root: stopping a service that refuses to stop, editing files the
daemon rewrites, deleting the daemon's own trust store, or uninstalling Chromium's policy
directory. None of them happens by accident, none of them is one command, and
`calma doctor` tells you when any of them has been done. The full list, with step counts,
is in [docs/threat-model.md](docs/threat-model.md).

## FAQ

**I forgot the password.** Then nothing can be loosened. That is the point. Tightening still
works, sessions still work, and the rules you set stay as they are.

**Can I use Firefox or another browser?** While a rule is blocked, the hosts layer makes its
domains unreachable from every program on the machine. During a session, other browsers get the
full site, because the minimal versions only exist in Chromium's policy and extension. If that
matters to you, do not keep another browser installed.

**What about my phone?** Nothing. Calma only knows about the machine it runs on.

**Does it phone home?** No. There is no telemetry and the only network traffic is between the
extension and the daemon on `127.0.0.1`.

**Why does `systemctl stop calma` fail?** The unit is marked `RefuseManualStop`. Stopping the
daemon would be the easiest loosening of all, so it is not offered.

**Can I change when the day resets?** `settings.day_starts_at` in `/etc/calma/rules.json`
(default 4). Changing it in either direction counts as loosening and needs the password, since
moving the reset is a way to get an extra day.

## Scripted installs

The repo doubles as the plugin's `setup` hook: after `omarchy plugin add`, running
`~/.config/omarchy/plugins/io.github.joanreyero.calma/setup` installs the system part with
`sudo` (dotfiles that install Omarchy plugins from a list and run each plugin's `setup` get
Calma for free). Rules and the password are per machine; set them afterwards.

## Uninstalling

```sh
sudo calma uninstall
```

It asks for the password if one is set, stops and disables the units, removes the installed
files, and restores `/etc/hosts`, the Chromium policy and `/etc/chromium-flags.conf`. Your
other Chromium flags and other `--load-extension` paths are left alone.

## Architecture

The design, file layout, HTTP API and the rules for what counts as loosening are in
[ARCHITECTURE.md](ARCHITECTURE.md). In short: a Python daemon (stdlib only) running as root
enforces the rules and serves a loopback HTTP API; a Chromium MV3 extension and the Omarchy bar
plugin (this repository, at the root) talk to that API; the `calma` CLI edits the rules file, which the daemon only accepts when
the change is not a loosening or carries a valid signature. Contributions: see
[CONTRIBUTING.md](CONTRIBUTING.md). License: MIT.
