"""The `calma` command: status, sessions, rule editing, password, doctor, log, uninstall."""

from __future__ import annotations

import argparse
import datetime as dt
import getpass
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from calma import __version__, crypto, paths, presets
from calma import rules as rules_mod
from calma import state as state_mod
from calma.enforce import chromium, hosts

# Colours: the terminal's own 16 ANSI colours, so the Omarchy theme applies ---------------------


class Palette:
    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def _c(self, code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.enabled else text

    def bold(self, t: str) -> str:
        return self._c("1", t)

    def dim(self, t: str) -> str:
        return self._c("2", t)

    def green(self, t: str) -> str:
        return self._c("32", t)

    def yellow(self, t: str) -> str:
        return self._c("33", t)

    def red(self, t: str) -> str:
        return self._c("31", t)

    def blue(self, t: str) -> str:
        return self._c("34", t)

    def cyan(self, t: str) -> str:
        return self._c("36", t)


def _palette() -> Palette:
    if os.environ.get("NO_COLOR"):
        return Palette(False)
    if os.environ.get("CALMA_COLOR") == "1":
        return Palette(True)
    return Palette(sys.stdout.isatty())


C = _palette()


def say(text: str = "") -> None:
    print(text)


def warn(text: str) -> None:
    print(C.yellow(text), file=sys.stderr)


def fail(text: str, code: int = 1) -> int:
    print(C.red(text), file=sys.stderr)
    return code


def _width(text: str) -> int:
    """Printable width, ignoring ANSI sequences (glyphs count as one cell, which is close enough)."""
    out = 0
    i = 0
    while i < len(text):
        if text[i] == "\033":
            j = text.find("m", i)
            i = len(text) if j < 0 else j + 1
            continue
        out += 1
        i += 1
    return out


def table(headers: list[str], rows: list[list[str]], right: set[int] = frozenset()) -> str:
    cols = len(headers)
    widths = [_width(h) for h in headers]
    for row in rows:
        for i in range(cols):
            widths[i] = max(widths[i], _width(row[i]))

    def fmt(cells: list[str], header: bool = False) -> str:
        parts = []
        for i, cell in enumerate(cells):
            pad = " " * (widths[i] - _width(cell))
            parts.append(pad + cell if i in right else cell + pad)
        line = "  ".join(parts).rstrip()
        return C.dim(line) if header else line

    lines = [fmt(headers, header=True)]
    lines += [fmt(r) for r in rows]
    return "\n".join("  " + line for line in lines)


# Time formatting ----------------------------------------------------------------------------


def mmss(seconds: int) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


def clock(ts: float | None, now: float | None = None) -> str:
    if ts is None:
        return "—"
    when = dt.datetime.fromtimestamp(ts)
    today = dt.datetime.fromtimestamp(now if now is not None else ts).date()
    hhmm = when.strftime("%H:%M")
    if when.date() == today:
        return hhmm
    if when.date() == today + dt.timedelta(days=1):
        return f"tomorrow {hhmm}"
    return when.strftime("%a %H:%M")


def minutes_phrase(m: int) -> str:
    return f"{m} minute" if m == 1 else f"{m} minutes"


# API client ---------------------------------------------------------------------------------


class DaemonDown(Exception):
    pass


class ApiError(Exception):
    def __init__(self, code: int, body: dict[str, Any]):
        super().__init__(body.get("error") or f"HTTP {code}")
        self.code = code
        self.body = body


def api(method: str, path: str, body: dict | None = None, timeout: float = 5.0) -> dict[str, Any]:
    url = f"http://127.0.0.1:{paths.port()}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Calma", "1")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read() or b"{}")
        except ValueError:
            payload = {}
        raise ApiError(e.code, payload) from None
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as e:
        raise DaemonDown(str(e)) from None


NOT_RUNNING = "Calma isn't running."
NOT_RUNNING_HINT = "Start it with: sudo systemctl start calma.service  (or check: calma doctor)"


def not_running() -> int:
    say(C.yellow(NOT_RUNNING))
    hint = NOT_RUNNING_HINT
    if paths.dev_mode():
        hint = f"Dev mode: CALMA_ROOT={paths.root()} CALMA_PORT={paths.port()}"
    say(C.dim(hint))
    return 2


# Root and passwords ---------------------------------------------------------------------------


def need_root(argv: list[str], why: str) -> None:
    """Re-exec with sudo when not root (never in dev mode). Explains why first."""
    if paths.dev_mode() or os.geteuid() == 0:
        return
    say(C.dim(f"{why} That file belongs to root, so this runs with sudo."))
    sudo = shutil.which("sudo")
    if not sudo:
        raise SystemExit(fail("sudo is not available; run this command as root."))
    cli = paths.cli_bin()
    if cli.exists():
        cmd = [sudo, "--", str(cli), *argv]
    else:
        pkg_parent = str(Path(__file__).resolve().parent.parent)
        cmd = [sudo, "--", "env", f"PYTHONPATH={pkg_parent}", sys.executable, "-m", "calma", *argv]
    os.execv(sudo, cmd)


def read_password(args: argparse.Namespace, prompt: str = "Calma password: ") -> str | None:
    pf = getattr(args, "password_file", None)
    if pf:
        try:
            return Path(pf).read_text().rstrip("\r\n")
        except OSError as e:
            raise SystemExit(fail(f"Can't read the password file: {e}")) from None
    if not sys.stdin.isatty():
        return None
    try:
        return getpass.getpass(prompt)
    except (EOFError, KeyboardInterrupt):
        say()
        return None


def write_with_password(new: dict, args: argparse.Namespace, done: str) -> int:
    """Write rules; on loosening ask for the password (once). Prints the outcome."""
    try:
        reasons = rules_mod.write_rules(new)
    except rules_mod.NoPassword:
        return fail("Calma has no password, so nothing can be loosened. Ever.")
    except rules_mod.PasswordRequired as e:
        say(C.yellow("This loosens the rules:"))
        for r in e.reasons:
            say(f"  · {r}")
        password = read_password(args)
        if not password:
            return fail("No password given; nothing changed.")
        try:
            reasons = rules_mod.write_rules(new, password)
        except rules_mod.WrongPassword:
            return fail("Wrong password; nothing changed.")
        except rules_mod.NoPassword:
            return fail("Calma has no password, so nothing can be loosened. Ever.")
    except rules_mod.RulesError as e:
        return fail(str(e))
    say(C.green(done) + ("" if not reasons else C.dim("  (signed)")))
    say(C.dim("Calma applies it within a second."))
    return 0


# Commands -----------------------------------------------------------------------------------


def cmd_status(args: argparse.Namespace) -> int:
    try:
        st = api("GET", "/status")
    except DaemonDown:
        if args.json:
            say(json.dumps({"running": False}))
            return 2
        return not_running()
    if args.json:
        say(json.dumps(st, indent=2, ensure_ascii=False))
        return 0
    rules = st.get("rules", {})
    now = st.get("now")
    if not rules:
        say("No rules yet.")
        say(C.dim("Add one with: sudo calma add x --preset x --sessions 2 --minutes 5"))
    else:
        rows = []
        for name, r in rules.items():
            glyph = r.get("glyph") or ""
            label = f"{glyph} {r['label']}".strip()
            if name != r["label"].lower():
                label += C.dim(f" ({name})")
            if r["state"] == "open":
                left = r["session"]["seconds_left"]
                state = C.green(f"open · {mmss(left)} left")
                nxt = C.dim("—")
            else:
                state = C.dim("blocked")
                if r["can_open"]:
                    nxt = C.green("now")
                elif r["cannot_open_reason"] == "always blocked":
                    nxt = C.dim("never")
                elif r["cannot_open_reason"] == "cooling down":
                    nxt = C.yellow(clock(r["next_opportunity"], now))
                else:
                    nxt = clock(r["next_opportunity"], now)
            sessions = f"{r['sessions_used']}/{r['sessions_per_day']}"
            minutes = f"{r['seconds_used'] // 60}/{r['daily_minutes']} min"
            rows.append([label, state, sessions, minutes, nxt])
        say()
        say(table(["Rule", "State", "Sessions", "Time", "Next"], rows, right={2, 3}))
    say()
    bits = [f"day resets at {st['day_starts_at']:02d}:00"]
    bits.append("password set" if st.get("password_set") else C.yellow("no password: nothing can ever be loosened"))
    say(C.dim("  " + " · ".join(bits)))
    for p in st.get("problems", []):
        say("  " + C.yellow("problem: ") + p)
    say()
    return 0


def cmd_open(args: argparse.Namespace) -> int:
    body: dict[str, Any] = {"rule": args.rule}
    if args.minutes is not None:
        body["minutes"] = args.minutes
    try:
        out = api("POST", "/open", body)
    except DaemonDown:
        return not_running()
    except ApiError as e:
        rule = e.body.get("rule") or {}
        label = rule.get("label", args.rule)
        reason = e.body.get("error", "unknown error")
        if e.code == 404:
            return fail(f"No rule called {args.rule!r}. See: calma list")
        extra = ""
        if rule.get("next_opportunity"):
            extra = f" Back at {clock(rule['next_opportunity'])}."
        elif reason == "already open" and rule.get("session"):
            extra = f" {mmss(rule['session']['seconds_left'])} left."
        say(C.yellow(f"{label}: {reason}.") + extra)
        return 1
    r = out["rule"]
    s = r["session"]
    say(C.green(f"{r['label']} is open for {minutes_phrase(s['minutes'])}, until {clock(s['ends_at'])}."))
    left = r["sessions_left"]
    say(C.dim(f"{left} session{'s' if left != 1 else ''} left today after this one."))
    return 0


def cmd_close(args: argparse.Namespace) -> int:
    try:
        out = api("POST", "/close", {"rule": args.rule} if args.rule else {})
    except DaemonDown:
        return not_running()
    except ApiError as e:
        if e.code == 404:
            return fail(f"No rule called {args.rule!r}. See: calma list")
        return fail(str(e))
    closed = out.get("closed", [])
    if not closed:
        say(C.dim("Nothing was open."))
        return 0
    if args.rule and out.get("rule"):
        r = out["rule"]
        say(C.green(f"{r['label']} is blocked again."))
        used = r["seconds_used"] // 60
        left = r["sessions_left"]
        say(C.dim(f"{used}/{r['daily_minutes']} min used today · {left} session{'s' if left != 1 else ''} left."))
    else:
        say(C.green(f"Closed {', '.join(closed)}."))
    return 0


def _rule_from_args(name: str, args: argparse.Namespace, base: dict | None = None) -> dict:
    rule: dict[str, Any] = dict(base or {})
    if getattr(args, "preset", None):
        rule["preset"] = args.preset
    if getattr(args, "label", None):
        rule["label"] = args.label
    if getattr(args, "domain", None):
        rule["domains"] = list(rule.get("domains", [])) + list(args.domain)
    if getattr(args, "process", None):
        rule["processes"] = list(rule.get("processes", [])) + list(args.process)
    if getattr(args, "sessions", None) is not None:
        rule["sessions_per_day"] = args.sessions
    if getattr(args, "minutes", None) is not None:
        rule["session_minutes"] = args.minutes
    if getattr(args, "daily", None) is not None:
        rule["daily_minutes"] = args.daily
    if getattr(args, "cooldown", None) is not None:
        rule["cooldown_minutes"] = args.cooldown
    return rule


def _load_rules_or_fail() -> dict:
    try:
        return rules_mod.load()
    except rules_mod.RulesError as e:
        raise SystemExit(fail(f"The rules file is invalid: {e}")) from None


def cmd_add(args: argparse.Namespace, argv: list[str]) -> int:
    need_root(argv, f"Adding {args.name!r} writes {paths.rules_file()}.")
    current = _load_rules_or_fail()
    if args.name in current["rules"]:
        return fail(f"There is already a rule called {args.name!r}. Use: calma set {args.name} …")
    rule = _rule_from_args(args.name, args)
    try:
        rules_mod.validate_rule(args.name, rule)
    except rules_mod.RulesError as e:
        return fail(str(e))
    new = json.loads(json.dumps(current))
    new["rules"][args.name] = rule
    ex = rules_mod.expand(args.name, rules_mod.validate_rule(args.name, rule))
    what = f"{ex['label']}: {ex['sessions_per_day']} × {ex['session_minutes']} min a day"
    return write_with_password(new, args, f"Added {what}.")


def cmd_set(args: argparse.Namespace, argv: list[str]) -> int:
    need_root(argv, f"Changing {args.name!r} writes {paths.rules_file()}.")
    current = _load_rules_or_fail()
    if args.name not in current["rules"]:
        return fail(f"No rule called {args.name!r}. See: calma list")
    if all(getattr(args, k) is None for k in ("sessions", "minutes", "daily", "cooldown", "label")):
        return fail("Nothing to change. Give --sessions, --minutes, --daily, --cooldown or --label.")
    new = json.loads(json.dumps(current))
    old_rule = new["rules"][args.name]
    rule = _rule_from_args(args.name, args, old_rule)
    # re-derive daily minutes when only sessions/minutes were given and daily was derived before
    if args.daily is None and (args.sessions is not None or args.minutes is not None):
        derived_before = old_rule.get("daily_minutes") == old_rule["sessions_per_day"] * old_rule["session_minutes"]
        if derived_before:
            rule["daily_minutes"] = max(1, rule["sessions_per_day"] * rule["session_minutes"])
    new["rules"][args.name] = rule
    return write_with_password(new, args, f"Updated {args.name}.")


def cmd_member(args: argparse.Namespace, argv: list[str], kind: str) -> int:
    field = "domains" if kind == "domain" else "processes"
    need_root(argv, f"Editing {kind}s of {args.name!r} writes {paths.rules_file()}.")
    current = _load_rules_or_fail()
    if args.name not in current["rules"]:
        return fail(f"No rule called {args.name!r}. See: calma list")
    value = rules_mod.normalize_domain(args.value) if kind == "domain" else args.value.strip()
    new = json.loads(json.dumps(current))
    members = list(new["rules"][args.name].get(field, []))
    if args.action == "add":
        if value in members:
            say(C.dim(f"{value} is already in {args.name}."))
            return 0
        members.append(value)
        done = f"Added {value} to {args.name}."
    else:
        if value not in members:
            return fail(f"{value} is not in {args.name}.")
        members.remove(value)
        done = f"Removed {value} from {args.name}."
    new["rules"][args.name][field] = members
    return write_with_password(new, args, done)


def cmd_remove(args: argparse.Namespace, argv: list[str]) -> int:
    need_root(argv, f"Removing {args.name!r} writes {paths.rules_file()}.")
    current = _load_rules_or_fail()
    if args.name not in current["rules"]:
        return fail(f"No rule called {args.name!r}. See: calma list")
    new = json.loads(json.dumps(current))
    del new["rules"][args.name]
    return write_with_password(new, args, f"Removed {args.name}.")


def cmd_password(args: argparse.Namespace, argv: list[str]) -> int:
    need_root(argv, f"The password key lives in {paths.etc_dir()}.")
    paths.ensure_dirs()
    old = None
    if crypto.password_set():
        say("Calma already has a password. To change it, give the current one.")
        old = read_password(args, "Current password: ")
        if not old:
            return fail("No password given; nothing changed.")
        if not crypto.check_password(old):
            return fail("Wrong password; nothing changed.")
    else:
        if paths.accepted_key_pub().exists():
            return fail(
                "Calma's trust store already holds a password key but key.pem is gone. "
                "A new key would be ignored by the daemon; restore the old key.pem instead."
            )
        say("Setting the Calma password. Once set, loosening any rule needs it.")
        say(C.dim("Give it to someone you trust and you won't be able to loosen anything yourself."))
    npf = getattr(args, "new_password_file", None)
    if npf:
        try:
            new = Path(npf).read_text().rstrip("\r\n")
        except OSError as e:
            return fail(f"Can't read the new password file: {e}")
    else:
        if not sys.stdin.isatty():
            return fail("Run this in a terminal (or pass --new-password-file).")
        try:
            new = getpass.getpass("New password: ")
            again = getpass.getpass("Again: ")
        except (EOFError, KeyboardInterrupt):
            say()
            return fail("Cancelled; nothing changed.")
        if new != again:
            return fail("The passwords differ; nothing changed.")
    if len(new) < 4:
        return fail("Use at least 4 characters; nothing changed.")
    try:
        crypto.set_password(new, old)
    except crypto.CryptoError as e:
        return fail(f"Couldn't write the key: {e}")
    say(C.green("Password changed." if old else "Password set."))
    if not old:
        say(C.dim("From now on, loosening a rule, removing one or uninstalling needs this password."))
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    try:
        rules = rules_mod.load()
    except rules_mod.RulesError as e:
        return fail(f"The rules file is invalid: {e}")
    if not rules["rules"]:
        say("No rules configured.")
        return 0
    rows = []
    for name, rule in rules["rules"].items():
        ex = rules_mod.expand(name, rule)
        budget = f"{ex['sessions_per_day']} × {ex['session_minutes']} min"
        if ex["daily_minutes"] != ex["sessions_per_day"] * ex["session_minutes"]:
            budget += f", {ex['daily_minutes']} min/day"
        if ex["cooldown_minutes"]:
            budget += f", {ex['cooldown_minutes']} min gap"
        members = []
        if ex["preset"]:
            members.append(C.cyan(f"preset {ex['preset']}"))
        members += ex["extra_domains"]
        members += [C.dim(f"process {p}") for p in ex["processes"]]
        rows.append([name, ex["label"], budget, ", ".join(members)])
    say()
    say(table(["Name", "Label", "Budget", "Blocks"], rows))
    say()
    say(C.dim(f"  day resets at {rules['settings']['day_starts_at']:02d}:00 · {paths.rules_file()}"))
    say()
    return 0


def cmd_presets(_args: argparse.Namespace) -> int:
    say()
    for name, p in presets.PRESETS.items():
        say(f"  {C.bold(name)}  {p['glyph']} {p['label']}")
        say(f"    domains   {', '.join(p['domains'])}")
        say(f"    open      {', '.join(p['allow'])}")
        if p["block"]:
            say(f"    still off {', '.join(p['block'])}")
        say(f"    home      {p['home']}")
        say()
    say(C.dim("  A preset gives you the minimal version of the site during a session; outside it, it's blocked."))
    say()
    return 0


def _systemctl(*args: str) -> tuple[int, str]:
    try:
        r = subprocess.run(["systemctl", *args], capture_output=True, text=True, timeout=20, check=False)
        return r.returncode, (r.stdout + r.stderr).strip()
    except (OSError, subprocess.SubprocessError) as e:
        return 1, str(e)


def doctor_checks() -> list[tuple[bool, str]]:
    """(ok, text) per layer. Pure reporting, never repairs."""
    checks: list[tuple[bool, str]] = []
    status = None
    try:
        status = api("GET", "/status", timeout=3)
        checks.append((True, f"daemon running (calma {status.get('version')}) on 127.0.0.1:{paths.port()}"))
    except DaemonDown:
        checks.append((False, f"daemon not running: no answer on 127.0.0.1:{paths.port()}"))
    try:
        rules = rules_mod.load()
        checks.append((True, f"rules file valid: {len(rules['rules'])} rules ({paths.rules_file()})"))
    except rules_mod.RulesError as e:
        rules = rules_mod.empty()
        checks.append((False, f"rules file invalid: {e}"))
    if paths.accepted_rules().exists():
        try:
            accepted = rules_mod.loads(paths.accepted_rules().read_bytes())
            if accepted == rules:
                checks.append((True, "rules file matches the daemon's trust store"))
            else:
                checks.append((False, "rules file differs from the trust store (the daemon will decide on it)"))
            rules = accepted
        except rules_mod.RulesError:
            checks.append((False, "trust store rules are invalid"))
    else:
        checks.append((False, f"no trust store yet ({paths.accepted_dir()}): the daemon has never run"))
    st = state_mod.State.load()
    open_rules = frozenset(st.sessions) if st.boot_id == state_mod.boot_id() else frozenset()
    if not paths.policy_file().exists():
        checks.append((False, f"Chromium policy missing: {paths.policy_file()}"))
    elif chromium.policy_ok(rules, open_rules):
        checks.append((True, f"Chromium policy in place ({paths.policy_file()})"))
    else:
        checks.append((False, f"Chromium policy out of date ({paths.policy_file()})"))
    if hosts.hosts_ok(rules, open_rules):
        n = len(hosts.blocked_hosts(rules, open_rules))
        checks.append((True, f"hosts block in place: {n} hosts ({paths.hosts_file()})"))
    else:
        checks.append((False, f"hosts block missing or stale ({paths.hosts_file()})"))
    fp = chromium.flags_problems()
    if fp:
        checks += [(False, p) for p in fp]
    else:
        checks.append((True, f"extension flag present in {paths.chromium_flags_file()}"))
    ext = paths.extension_dir()
    ep = chromium.extension_problems()
    if ep:
        checks += [(False, p) for p in ep]
    else:
        checks.append((True, f"extension files present ({ext})"))
    if crypto.password_set():
        checks.append((True, "password set: loosening needs it"))
    else:
        checks.append((True, "no password: nothing can ever be loosened"))
    checks += [(False, p) for p in crypto.key_problems()]
    if not paths.dev_mode() and shutil.which("systemctl"):
        for unit in ("calma.service", "calma-watchdog.timer"):
            rc_e, en = _systemctl("is-enabled", unit)
            rc_a, ac = _systemctl("is-active", unit)
            ok = rc_e == 0 and rc_a == 0
            checks.append((ok, f"{unit}: {en or '?'}, {ac or '?'}"))
    tampers = st.tamper_events_today(dt.datetime.now().timestamp(), rules["settings"]["day_starts_at"])
    if tampers:
        checks.append((False, f"{len(tampers)} tamper event(s) today; last: {tampers[-1].get('what')}"))
    else:
        checks.append((True, "no tamper events today"))
    if status is not None:
        # the daemon runs the same flags/extension checks; only show what it adds (policy, pings, tampers)
        seen = {text for ok, text in checks if not ok}
        probs = [p for p in status.get("problems", []) if p not in seen and not (tampers and "tamper event" in p)]
        if probs:
            checks += [(False, f"daemon reports: {p}") for p in probs]
        elif not status.get("problems"):
            checks.append((True, "daemon self-check clean"))
    return checks


def cmd_doctor(_args: argparse.Namespace) -> int:
    checks = doctor_checks()
    say()
    for ok, text in checks:
        mark = C.green("OK      ") if ok else C.red("problem ")
        say(f"  {mark} {text}")
    say()
    bad = [c for c in checks if not c[0]]
    if bad:
        plural = "s" if len(bad) != 1 else ""
        say(C.yellow(f"  {len(bad)} problem{plural}.") + C.dim("  The watchdog repairs most of them within a minute."))
    else:
        say(C.green("  Every layer is in place."))
    say(C.dim("  Calma defeats impulse, not a determined root user with an hour to spare. See docs/threat-model.md."))
    say()
    return 1 if bad else 0


def cmd_log(args: argparse.Namespace) -> int:
    if not shutil.which("journalctl"):
        return fail("journalctl is not available.")
    cmd = ["journalctl", "-u", "calma", "-u", "calma-watchdog", "--no-pager", "-n", str(args.n)]
    if args.follow:
        cmd.append("-f")
    try:
        return subprocess.call(cmd)
    except KeyboardInterrupt:
        return 0


def _remove_flag_from(path: Path) -> bool:
    ext = str(paths.extension_dir())
    try:
        text = path.read_text()
    except OSError:
        return False
    out = []
    changed = False
    for line in text.split("\n"):
        m = chromium._LOAD_RE.match(line)
        if m and ext in m.group(2).split(","):
            parts = [p for p in m.group(2).split(",") if p and p != ext]
            changed = True
            if parts:
                out.append(m.group(1) + ",".join(parts))
            continue
        out.append(line)
    if changed:
        paths.atomic_write(path, "\n".join(out))
    return changed


def cmd_uninstall(args: argparse.Namespace, argv: list[str]) -> int:
    need_root(argv, "Uninstalling removes files under /etc, /usr and /var.")
    if crypto.password_set():
        say("Uninstalling Calma needs the password.")
        pw = read_password(args)
        if not pw or not crypto.check_password(pw):
            return fail("Wrong or missing password; nothing changed.")
    elif paths.accepted_rules().exists() or paths.rules_file().exists():
        try:
            has_rules = bool(rules_mod.load()["rules"])
        except rules_mod.RulesError:
            has_rules = True
        if has_rules:
            return fail("Calma has no password, so nothing can be loosened. Ever. That includes uninstalling.")
    if not args.yes:
        if not sys.stdin.isatty():
            return fail("Pass --yes to uninstall without a prompt.")
        try:
            answer = input("Remove Calma completely? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = ""
        if answer not in ("y", "yes"):
            say("Nothing changed.")
            return 0
    removed: list[str] = []
    if not paths.dev_mode() and shutil.which("systemctl"):
        _systemctl("disable", "--now", "calma-watchdog.timer")
        _systemctl("disable", "calma.service")
        for unit in ("calma.service", "calma-watchdog.service", "calma-watchdog.timer"):
            p = paths.systemd_dir() / unit
            if p.exists():
                p.unlink()
                removed.append(str(p))
        _systemctl("daemon-reload")
        _systemctl("kill", "-s", "SIGTERM", "calma.service")
        _systemctl("reset-failed", "calma.service")
    try:
        pid = int(paths.pid_file().read_text().strip())
    except (OSError, ValueError):
        pid = None
    if pid is not None:
        try:
            os.kill(pid, 15)
        except OSError:
            pid = None
    # Wait for the daemon to flush and exit before removing anything it writes,
    # or its last tick re-creates /var/lib/calma, the policy and the hosts block.
    deadline = time.monotonic() + 8
    while pid is not None and time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            break
        time.sleep(0.1)
    if hosts.remove_block():
        removed.append(f"Calma block in {paths.hosts_file()}")
    if paths.policy_file().exists():
        paths.policy_file().unlink()
        removed.append(str(paths.policy_file()))
    if _remove_flag_from(paths.chromium_flags_file()):
        removed.append(f"flag in {paths.chromium_flags_file()}")
    for f in paths.user_flags_files():
        if _remove_flag_from(f.resolve()):
            removed.append(f"flag in {f}")
    for d in (paths.etc_dir(), paths.state_dir(), paths.run_dir(), paths.share_dir(), paths.lib_dir()):
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
            removed.append(str(d))
    if paths.cli_bin().exists():
        paths.cli_bin().unlink()
        removed.append(str(paths.cli_bin()))
    for r in removed:
        say(C.dim(f"  removed {r}"))
    say(C.green("Calma is gone.") + C.dim("  Restart Chromium to drop the policy and the extension."))
    return 0


# Parser -------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="calma",
        description="A calm, bulletproof distraction blocker.",
        epilog="Without a command, calma shows the status.",
    )
    p.add_argument("--version", action="version", version=f"calma {__version__}")
    sub = p.add_subparsers(dest="command")

    s = sub.add_parser("status", help="rules, sessions and budgets for today")
    s.add_argument("--json", action="store_true", help="print the raw /status document")

    s = sub.add_parser("open", help="start a session")
    s.add_argument("rule")
    s.add_argument("minutes", nargs="?", type=int)

    s = sub.add_parser("close", help="end a session (all of them without a rule)")
    s.add_argument("rule", nargs="?")

    def budget_opts(sp: argparse.ArgumentParser, required: bool) -> None:
        sp.add_argument("--sessions", "-s", type=int, required=required, metavar="N", help="sessions per day")
        sp.add_argument("--minutes", "-m", type=int, required=required, metavar="M", help="minutes per session")
        sp.add_argument("--daily", "-d", type=int, metavar="D", help="minutes per day (default sessions × minutes)")
        sp.add_argument("--cooldown", "-c", type=int, metavar="C", help="minutes between sessions")
        sp.add_argument("--label", "-l", metavar="L", help="display name")
        sp.add_argument("--password-file", metavar="FILE", help="read the password from a file instead of asking")

    s = sub.add_parser("add", help="add a rule")
    s.add_argument("name")
    s.add_argument("--preset", "-p", choices=presets.names())
    s.add_argument("--domain", "-D", action="append", metavar="DOMAIN")
    s.add_argument("--process", "-P", action="append", metavar="PROC")
    budget_opts(s, required=True)

    s = sub.add_parser("set", help="change a rule's budget")
    s.add_argument("name")
    budget_opts(s, required=False)

    for kind in ("domain", "process"):
        s = sub.add_parser(kind, help=f"add or remove a {kind} of a rule")
        s.add_argument("action", choices=["add", "remove"])
        s.add_argument("name")
        s.add_argument("value", metavar=kind.upper())
        s.add_argument("--password-file", metavar="FILE")

    s = sub.add_parser("remove", help="remove a rule (needs the password)")
    s.add_argument("name")
    s.add_argument("--password-file", metavar="FILE")

    s = sub.add_parser("password", help="set or change the password")
    s.add_argument("--password-file", metavar="FILE", help="current password (when changing)")
    s.add_argument("--new-password-file", metavar="FILE")

    sub.add_parser("list", help="rules as configured")
    sub.add_parser("presets", help="the built-in presets")
    sub.add_parser("doctor", help="check every layer")

    s = sub.add_parser("log", help="the daemon's journal")
    s.add_argument("-n", type=int, default=50)
    s.add_argument("-f", "--follow", action="store_true")

    s = sub.add_parser("uninstall", help="remove Calma completely (needs the password)")
    s.add_argument("--password-file", metavar="FILE")
    s.add_argument("--yes", "-y", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(argv)
    cmd = args.command or "status"
    if cmd == "status" and not hasattr(args, "json"):
        args.json = False
    try:
        if cmd == "status":
            return cmd_status(args)
        if cmd == "open":
            return cmd_open(args)
        if cmd == "close":
            return cmd_close(args)
        if cmd == "add":
            return cmd_add(args, argv)
        if cmd == "set":
            return cmd_set(args, argv)
        if cmd in ("domain", "process"):
            return cmd_member(args, argv, cmd)
        if cmd == "remove":
            return cmd_remove(args, argv)
        if cmd == "password":
            return cmd_password(args, argv)
        if cmd == "list":
            return cmd_list(args)
        if cmd == "presets":
            return cmd_presets(args)
        if cmd == "doctor":
            return cmd_doctor(args)
        if cmd == "log":
            return cmd_log(args)
        if cmd == "uninstall":
            return cmd_uninstall(args, argv)
    except KeyboardInterrupt:
        say()
        return 130
    parser.print_help()
    return 0
