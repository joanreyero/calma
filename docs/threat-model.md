# Calma threat model

Calma's adversary is the user's own impulse: the five seconds between "I'll just check X" and
the tab being open. It is not an adversary with root, time and intent, because on a Linux
machine that adversary is the user, and root always wins. This document lists what each layer
stops, every bypass we know of with the number of deliberate root steps it takes, and what is
out of scope. "Step" means one distinct command or edit that a person has to decide to do; none
of them can happen by accident or by habit.

## What each layer stops

| Layer | Stops | Does not stop |
|---|---|---|
| 1. Chromium managed policy (`/etc/chromium/policies/managed/calma.json`) | Any Chromium window of any user opening a blocked domain; opening non-minimal paths (home feed, Shorts, Explore) during a session. Cannot be changed from `chrome://settings`, by an extension, or by a profile. Chromium reloads it within seconds when the daemon rewrites it. | Other browsers. Chromium binaries started with a different `--user-data-dir` still obey it (policy is machine-wide). |
| 2. Hosts file (`/etc/hosts`, block between `# calma:begin` and `# calma:end`) | Every program on the machine resolving a blocked domain by name while the rule is blocked: Firefox, curl, Electron apps, the terminal. | Access by IP; a DNS-over-HTTPS resolver inside a browser that bypasses the system resolver (Chromium is covered by layer 1 regardless); full access to the site from other browsers during a session. |
| 3. Process killing | Listed processes (by `comm` or executable basename) living longer than about a second while blocked. | Renamed or copied binaries; processes inside containers or VMs with their own PID namespace; web versions of the app (use `domains` for those). |
| 4. Browser extension (`--load-extension`) | Seeing the raw Chromium error page instead of the calm blocked page; keeping a tab open after a session ends; seeing feeds, Shorts, trends, recommendations, autoplay and comments during a session. | It is a convenience and cosmetic layer. Disabling it in `chrome://extensions` is possible, but layer 1 still blocks the domains and the minimal paths; only the cosmetics and tab-closing are lost. The daemon reports a missing extension ping in `calma doctor`. |
| 5. Self-protection (units, watchdog, signed rules, trust store) | `systemctl stop` and `systemctl restart` (refused); a killed daemon staying dead (restarted in 1 s); a disabled or masked daemon staying disabled (watchdog re-enables and starts it every minute); an edited rules file loosening anything without a signature (reverted, logged, notified); a replaced `key.pub` without a signature from the previous key (reverted, logged). | Root undoing all of the above deliberately, as listed below. |

## Known bypasses, as root

Everything below requires `sudo`. For each, the count is the minimum number of deliberate
steps, and what Calma does about it.

1. **Stop the daemon and keep it stopped.** `systemctl stop` is refused. One must
   `systemctl mask calma.service`, then `systemctl kill calma.service` (or `kill -9` the pid),
   and then also `systemctl mask calma-watchdog.timer` or the watchdog brings it back within a
   minute. Three steps. Chromium's policy file and the hosts block stay in place while the
   daemon is down, so blocked rules stay blocked and open sessions cannot be started: the
   failure mode is more blocked, not less.
2. **Delete the policy file.** `rm /etc/chromium/policies/managed/calma.json`. One step, but
   the daemon rewrites it on the next tick (1 s) and the watchdog repairs it every minute, so
   it needs bypass 1 first: four steps. Until then, the hosts layer still blocks the domains.
3. **Edit the rules file to loosen a rule.** Edit `/etc/calma/rules.json`. One step, but the
   daemon only accepts a loosening with a valid `rules.sig` from the accepted `key.pub`, so
   the file is overwritten with the accepted copy, a tamper event is recorded and a
   notification is shown. To make it stick one must bypass 1 first, then edit the file, then
   also replace `/var/lib/calma/accepted/rules.json` (the daemon's trust store) so that the
   restart does not revert it: five steps.
4. **Replace the key pair.** Write a new `key.pem`/`key.pub` in `/etc/calma`. The daemon
   restores the accepted `key.pub` unless `key.pub.sig` is a valid signature of the new public
   key by the old one. Making a swapped key stick means bypass 1, then replacing
   `/var/lib/calma/accepted/key.pub` as well: five steps.
5. **Delete the trust store with the daemon down.** `rm -rf /var/lib/calma/accepted` after
   bypass 1, then loosen the rules file: the daemon's "first run" path accepts whatever it
   finds. Five steps. This is the cleanest known bypass and it is documented on purpose: the
   trust store is what makes the password meaningful, and hiding it would be security by
   obscurity against the one person who owns the machine.
6. **Uninstall Chromium's policy support.** Chromium always reads
   `/etc/chromium/policies/managed/`; there is no flag to turn it off. Removing the directory is
   bypass 2. Installing a differently named Chromium build (e.g. a Flatpak) that reads a
   different policy directory: one step, but it is still subject to the hosts block while a
   rule is blocked, and the extension is not loaded in it, so during a session it gets the full
   site. Covered partially; see out of scope.
7. **Remove the hosts block.** Edit `/etc/hosts`. One step, rewritten by the daemon within a
   second. Needs bypass 1 first: four steps. Chromium is still blocked by the policy.
8. **Remove the extension flag.** Edit `/etc/chromium-flags.conf` or
   `~/.config/chromium-flags.conf`. Rewritten by the daemon and the watchdog. Even when it
   sticks, only layer 4 is lost.
9. **Change the clock.** Setting the date forward to get a new day's budget: the daemon resets
   usage when the local date advances, so this works once per `date -s` as root (one step), and
   `timedatectl` fights with NTP afterwards. Setting the clock back grants nothing: dates are
   compared as strings and usage is only reset when today is later than the recorded day.
   Sessions are timed with `CLOCK_BOOTTIME`, which wall-clock changes do not affect. Suspend
   counts against a session (BOOTTIME keeps running). After a reboot every running session is
   charged in full.
10. **Reboot into another system, live USB, or a rescue shell.** Full access to the disk; any
    file can be edited. Physical possession beats everything. Out of scope.
11. **Uninstall with `calma uninstall`.** Requires the password when one is set. Without the
    password, uninstalling is bypass 1 plus deleting the files by hand.
12. **Use a VM, container, or another machine.** Nothing Calma can do. Out of scope.
13. **Use a phone.** Out of scope.

What counts: no bypass is a single habitual command. Each one leaves traces (`tamper_events`
in the state, journal lines, `calma doctor` failures, a desktop notification at the moment it
happens), so the person holding the password can see that it happened.

## Non-goals and things out of scope

- **Determined root.** Anyone with root and an hour can remove Calma. The goal is that the
  removal is a conscious project, not a reflex.
- **Other browsers during a session.** The minimal versions exist only in Chromium. Firefox gets
  the full site while a rule is open. The hosts layer still blocks it when the rule is blocked.
- **Mobile devices and other computers.** Calma knows about one machine.
- **Network-level enforcement.** No DNS server, no firewall rules, no proxy. A hosts block is
  what you get; a browser doing its own DNS-over-HTTPS bypasses it (Chromium is still covered
  by the policy layer).
- **Secrecy.** The HTTP API on `127.0.0.1:7390` is readable by any local process and has
  `Access-Control-Allow-Origin: *`; nothing in it is secret, and starting a session is never a
  loosening. POST requests need the `X-Calma: 1` header so a web page cannot start a session
  through a simple cross-origin request. A local program that wants to start a session can; it
  is allowed to, the same as the user.
- **Privacy from other users of the machine.** Rules and state are world-readable (0644) so
  that `calma status` works without root.
- **Protecting the password itself.** The key is encrypted with AES-256 by `openssl` using the
  password. A weak password can be brute-forced offline by anyone who copies `key.pem`. Pick a
  reasonable one, or better, let someone else pick it.
- **Guarding against the Calma developers.** You are running our code as root. Read it; it is
  short and has no dependencies.
