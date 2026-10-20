"""calmad: the 1 s enforcement loop, rules acceptance with the trust store, the HTTP API, --watchdog."""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from calma import __version__, crypto, notify, paths
from calma import rules as rules_mod
from calma import state as state_mod
from calma import theme as theme_mod
from calma.enforce import chromium, hosts, procs

log = logging.getLogger("calma")

EXTENSION_STALE_SECONDS = 120
LONG_POLL_SECONDS = 25
TAMPER_TITLE = "Calma: rules were loosened without the password; ignored"


def _stat_sig(path) -> tuple | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_ino, st.st_mtime_ns, st.st_size)


class Daemon:
    """All daemon state. Every public method takes the lock; the loop and HTTP threads share it."""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.cond = threading.Condition(self.lock)
        self.rules: dict = rules_mod.empty()
        self.state = state_mod.State.load()
        self.stop = threading.Event()
        self.last_change = time.time()
        self._rules_sig: tuple | None = None
        self._key_sig: tuple | None = None
        self._open_rendered: frozenset[str] | None = None
        self._problems: list[str] = []
        self._last_check = 0.0

    # Rules acceptance ---------------------------------------------------

    def load_rules(self, now_wall: float | None = None) -> dict:
        """Run the acceptance procedure against the trust store and return the active rules."""
        now_wall = time.time() if now_wall is None else now_wall
        with self.lock:
            self._accept_key(now_wall)
            rules = self._accept_rules(now_wall)
            self._rules_sig = _stat_sig(paths.rules_file())
            self._key_sig = _stat_sig(paths.key_pub())
            if rules != self.rules:
                self.rules = rules
                self._on_rules_changed(now_wall)
            return self.rules

    def _accept_key(self, now_wall: float) -> None:
        pub_path, acc_path = paths.key_pub(), paths.accepted_key_pub()
        new = pub_path.read_bytes() if pub_path.exists() else None
        accepted = acc_path.read_bytes() if acc_path.exists() else None
        if new == accepted:
            return
        if accepted is None:
            if new is not None:
                paths.atomic_write(acc_path, new, 0o644)
                log.info("key.pub accepted (first key)")
            return
        if new is not None:
            sig = _read_sig(paths.key_pub_sig())
            if sig and crypto.verify(new, sig, accepted):
                paths.atomic_write(acc_path, new, 0o644)
                log.info("key.pub accepted: signed by the previous key")
                return
            what = "key.pub replaced without the previous password; restored the accepted key"
        else:
            what = "key.pub was deleted; restored the accepted key"
        log.warning("tamper: %s", what)
        self.state.record_tamper(what, now_wall)
        paths.atomic_write(pub_path, accepted, 0o644)
        notify.send(
            "Calma: the password key was replaced without the old password",
            "The previous key stays in force.",
            "critical",
        )

    def _accept_rules(self, now_wall: float) -> dict:
        acc_path = paths.accepted_rules()
        file_path = paths.rules_file()
        try:
            data = file_path.read_bytes()
        except FileNotFoundError:
            data = None
        if not acc_path.exists():
            try:
                new = rules_mod.loads(data) if data is not None else rules_mod.empty()
            except rules_mod.RulesError as e:
                log.warning("rules.json invalid on first run (%s); starting with no rules", e)
                new = rules_mod.empty()
            paths.atomic_write(acc_path, rules_mod.dumps(new), 0o644)
            if data is None:
                paths.atomic_write(file_path, rules_mod.dumps(new), 0o644)
            log.info("rules accepted (first run): %d rules", len(new["rules"]))
            return new
        try:
            accepted = rules_mod.loads(acc_path.read_bytes())
        except rules_mod.RulesError as e:
            log.error("trust store rules.json is invalid (%s); treating as empty", e)
            accepted = rules_mod.empty()
        if data is None:
            self._reject(accepted, "rules.json was deleted; restored the accepted rules", now_wall)
            return accepted
        try:
            new = rules_mod.loads(data)
        except rules_mod.RulesError as e:
            self._reject(accepted, f"rules.json is invalid ({e}); restored the accepted rules", now_wall)
            return accepted
        reasons = rules_mod.is_loosening(accepted, new)
        if not reasons:
            if new != accepted:
                paths.atomic_write(acc_path, rules_mod.dumps(new), 0o644)
                log.info("rules accepted: %d rules", len(new["rules"]))
            return new
        sig = _read_sig(paths.rules_sig())
        acc_key = paths.accepted_key_pub()
        if sig and acc_key.exists() and crypto.verify(data, sig, acc_key):
            paths.atomic_write(acc_path, rules_mod.dumps(new), 0o644)
            log.info("rules accepted with a valid signature: %s", "; ".join(reasons))
            return new
        self._reject(
            accepted,
            "rules.json loosened without a valid signature; kept previous rules (" + "; ".join(reasons) + ")",
            now_wall,
        )
        return accepted

    def _reject(self, accepted: dict, what: str, now_wall: float) -> None:
        log.warning("tamper: %s", what)
        self.state.record_tamper(what, now_wall)
        paths.atomic_write(paths.rules_file(), rules_mod.dumps(accepted), 0o644)
        try:
            paths.rules_sig().unlink()
        except FileNotFoundError:
            pass
        notify.send(TAMPER_TITLE, "", "critical")

    def _on_rules_changed(self, now_wall: float) -> None:
        self.state.prune(self.rules)
        now_mono = state_mod.boottime()
        for name, sess in list(self.state.sessions.items()):
            rule = self.rules["rules"].get(name)
            if rule is None:
                continue
            # a tightened budget applies to a running session too
            limit = float(sess["started_mono"]) + rule["session_minutes"] * 60
            remaining = self.state.remaining_seconds(name, rule)
            limit = min(limit, now_mono + remaining)
            if limit < float(sess["ends_mono"]):
                sess["ends_mono"] = max(limit, now_mono)
                self.state.dirty = True
        self._bump()

    def _bump(self) -> None:
        with self.cond:
            self.last_change = time.time()
            self.cond.notify_all()

    # Loop ---------------------------------------------------------------

    def tick(self, now_wall: float | None = None, now_mono: float | None = None) -> None:
        now_wall = time.time() if now_wall is None else now_wall
        now_mono = state_mod.boottime() if now_mono is None else now_mono
        with self.lock:
            if _stat_sig(paths.rules_file()) != self._rules_sig or _stat_sig(paths.key_pub()) != self._key_sig:
                self.load_rules(now_wall)
            events = []
            if self.state.check_day(now_wall, self.day_starts_at):
                log.info("new day %s: budgets reset", self.state.day)
                self._bump()
            events += self.state.tick(self.rules, now_wall, now_mono)
            changed = self.render()
            for ev in events:
                log.info("%s", ev.message)
                notify.send(ev.message, "", "normal" if ev.kind == "ended" else "low")
            if events:
                self._bump()
            # the self-check is cached (it scans /proc); refresh it every 10 s and right after a repair,
            # so /status never reports a problem that render() just fixed
            if changed or now_wall - self._last_check >= 10:
                self._check_now(now_wall)
            self.state.save()

    @property
    def day_starts_at(self) -> int:
        return int(self.rules.get("settings", {}).get("day_starts_at", rules_mod.DEFAULT_DAY_STARTS_AT))

    def open_rules(self) -> frozenset[str]:
        return frozenset(self.state.sessions)

    def render(self) -> bool:
        """Make the system match the rules: policy, hosts, processes, flags. Idempotent and cheap.

        Returns True when any file was (re)written.
        """
        with self.lock:
            return self._render()

    def _render(self) -> bool:
        open_rules = self.open_rules()
        changed = False
        if chromium.write_policy(self.rules, open_rules):
            changed = True
            desc = ", ".join(
                f"{n} {'open' if n in open_rules else 'blocked'}" for n in self.rules["rules"]
            ) or "no rules"
            log.info("policy: %s", desc)
        if hosts.write_hosts(self.rules, open_rules):
            changed = True
            log.info("hosts: %d hosts blocked", len(hosts.blocked_hosts(self.rules, open_rules)))
        for pid, name in procs.kill_blocked(self.rules, open_rules):
            log.info("%s %s (pid %d)", "killed" if paths.kill_enabled() else "would kill", name, pid)
        for f in chromium.repair_flags():
            changed = True
            log.info("flags: repaired %s", f)
        if open_rules != self._open_rendered:
            self._open_rendered = open_rules
            self._bump()
        return changed

    def _check_now(self, now_wall: float) -> None:
        self._last_check = now_wall
        self._problems = self._self_check(now_wall)

    def _self_check(self, now_wall: float) -> list[str]:
        problems: list[str] = []
        open_rules = self.open_rules()
        if not paths.policy_file().exists():
            problems.append("Chromium policy file is missing")
        elif not chromium.policy_ok(self.rules, open_rules):
            problems.append("Chromium policy file is out of date")
        problems += chromium.flags_problems()
        problems += chromium.extension_problems()
        problems += crypto.key_problems()
        if not hosts.hosts_ok(self.rules, open_rules):
            problems.append("hosts file is missing the Calma block")
        tampers = self.state.tamper_events_today(now_wall, self.day_starts_at)
        if tampers:
            n = len(tampers)
            problems.append(f"{n} tamper event{'s' if n != 1 else ''} today (see calma log)")
        if procs.chromium_running():
            seen = self.state.extension_seen
            if seen is None or now_wall - seen > EXTENSION_STALE_SECONDS:
                if seen is None:
                    problems.append("extension not seen while Chromium is running")
                else:
                    mins = int((now_wall - seen) // 60)
                    problems.append(f"extension not seen for {mins} min while Chromium is running")
        return problems

    # Views --------------------------------------------------------------

    def rule_view(self, name: str, now_wall: float, now_mono: float | None = None) -> dict[str, Any]:
        rule = self.rules["rules"][name]
        ex = rules_mod.expand(name, rule)
        u = self.state.usage(name)
        session = self.state.session_view(name, now_wall, now_mono)
        reason = self.state.cannot_open_reason(name, rule, now_wall)
        return {
            "label": ex["label"],
            "glyph": ex["glyph"],
            "preset": ex["preset"],
            "domains": ex["domains"],
            "processes": ex["processes"],
            "home": ex["home"],
            "sessions_per_day": rule["sessions_per_day"],
            "session_minutes": rule["session_minutes"],
            "daily_minutes": rule["daily_minutes"],
            "cooldown_minutes": rule["cooldown_minutes"],
            "sessions_used": int(u["sessions_used"]),
            "seconds_used": int(u["seconds_used"]),
            "sessions_left": max(0, rule["sessions_per_day"] - int(u["sessions_used"])),
            "seconds_left": self.state.remaining_seconds(name, rule),
            "state": "open" if session else "blocked",
            "session": session,
            "can_open": reason is None,
            "cannot_open_reason": reason,
            "next_opportunity": self.state.next_opportunity(name, rule, now_wall, self.day_starts_at),
        }

    def status(self) -> dict[str, Any]:
        with self.lock:
            now_wall = time.time()
            now_mono = state_mod.boottime()
            return {
                "version": __version__,
                "now": now_wall,
                "day": self.state.day or state_mod.day_for(now_wall, self.day_starts_at),
                "day_starts_at": self.day_starts_at,
                "next_reset": state_mod.next_reset(now_wall, self.day_starts_at),
                "password_set": crypto.password_set(),
                "healthy": not self._problems,
                "problems": list(self._problems),
                "last_change": self.last_change,
                "rules": {name: self.rule_view(name, now_wall, now_mono) for name in self.rules["rules"]},
            }

    # Actions ------------------------------------------------------------

    def open(self, name: str, minutes: int | None = None) -> tuple[int, dict[str, Any]]:
        with self.lock:
            if name not in self.rules["rules"]:
                return 404, {"ok": False, "error": "unknown rule"}
            now_wall = time.time()
            rule = self.rules["rules"][name]
            err, _sess = self.state.open_session(name, rule, now_wall, minutes=minutes)
            if err:
                return 409, {"ok": False, "error": err, "rule": self.rule_view(name, now_wall)}
            view = self.rule_view(name, now_wall)
            log.info("%s · session started · %s min", view["label"], view["session"]["minutes"])
            if self.render():
                self._check_now(now_wall)
            self.state.save()
            self._bump()
            return 200, {"ok": True, "rule": view}

    def close(self, name: str | None) -> tuple[int, dict[str, Any]]:
        with self.lock:
            now_wall = time.time()
            names = [name] if name else list(self.state.sessions)
            if name and name not in self.rules["rules"]:
                return 404, {"ok": False, "error": "unknown rule"}
            closed = []
            for n in names:
                ev = self.state.close_session(n, self.rules, now_wall)
                if ev:
                    closed.append(n)
                    log.info("%s", ev.message)
                    notify.send(ev.message)
            if closed:
                if self.render():
                    self._check_now(now_wall)
                self.state.save()
                self._bump()
            out: dict[str, Any] = {"ok": True, "closed": closed}
            if name:
                out["rule"] = self.rule_view(name, now_wall)
            return 200, out

    def ping(self, version: str | None) -> None:
        with self.lock:
            self.state.extension_seen = time.time()
            if version:
                self.state.extension_version = str(version)
            self.state.dirty = True

    def wait_for_change(self, since: float, timeout: float = LONG_POLL_SECONDS) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        with self.cond:
            while self.last_change <= since and not self.stop.is_set():
                left = deadline - time.monotonic()
                if left <= 0:
                    return {"changed": False}
                self.cond.wait(min(left, 1.0))
            if self.last_change <= since:
                return {"changed": False}
        return {"changed": True, "status": self.status()}

    def theme(self) -> dict[str, Any]:
        with self.lock:
            settings = dict(self.rules.get("settings", {}))
        return theme_mod.theme(settings)


def _read_sig(path) -> bytes | None:
    try:
        return base64.b64decode(path.read_bytes().strip(), validate=False) or None
    except (OSError, ValueError):
        return None


# HTTP ---------------------------------------------------------------------


def make_handler(daemon: Daemon):
    class Handler(BaseHTTPRequestHandler):
        server_version = f"calmad/{__version__}"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):  # quiet
            log.debug("http: " + fmt, *args)

        def _send(self, code: int, body: dict[str, Any]) -> None:
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Calma")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_OPTIONS(self):  # CORS preflight for the extension
            self._send(200, {"ok": True})

        def do_GET(self):
            url = urlparse(self.path)
            if url.path == "/status":
                self._send(200, daemon.status())
            elif url.path == "/theme":
                self._send(200, daemon.theme())
            elif url.path == "/events":
                q = parse_qs(url.query)
                try:
                    since = float(q.get("since", ["0"])[0])
                except ValueError:
                    since = 0.0
                self._send(200, daemon.wait_for_change(since, LONG_POLL_SECONDS))
            elif url.path in ("/", "/health"):
                self._send(200, {"ok": True, "version": __version__})
            else:
                self._send(404, {"ok": False, "error": "not found"})

        def do_POST(self):
            url = urlparse(self.path)
            if self.headers.get("X-Calma") != "1":
                self._send(403, {"ok": False, "error": "missing X-Calma header"})
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            raw = self.rfile.read(length) if length else b""
            try:
                body = json.loads(raw) if raw else {}
                if not isinstance(body, dict):
                    raise ValueError
            except ValueError:
                self._send(400, {"ok": False, "error": "body must be a JSON object"})
                return
            if url.path == "/open":
                rule = body.get("rule")
                minutes = body.get("minutes")
                if not isinstance(rule, str):
                    self._send(400, {"ok": False, "error": "rule is required"})
                    return
                bad_minutes = isinstance(minutes, bool) or not isinstance(minutes, int | float) or minutes < 1
                if minutes is not None and bad_minutes:
                    self._send(400, {"ok": False, "error": "minutes must be a positive number"})
                    return
                code, out = daemon.open(rule, int(minutes) if minutes is not None else None)
                self._send(code, out)
            elif url.path == "/close":
                rule = body.get("rule")
                if rule is not None and not isinstance(rule, str):
                    self._send(400, {"ok": False, "error": "rule must be a string"})
                    return
                code, out = daemon.close(rule)
                self._send(code, out)
            elif url.path == "/ping":
                ext = body.get("extension")
                daemon.ping(str(ext) if ext is not None else None)
                self._send(200, {"ok": True})
            else:
                self._send(404, {"ok": False, "error": "not found"})

    return Handler


def serve(daemon: Daemon) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", paths.port()), make_handler(daemon))
    server.daemon_threads = True
    t = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.5}, daemon=True, name="http")
    t.start()
    return server


# Entry points ---------------------------------------------------------------


def setup_logging(level: int = logging.INFO) -> None:
    fmt = "%(asctime)s %(levelname)s %(message)s" if paths.dev_mode() else "%(levelname)s %(message)s"
    logging.basicConfig(stream=sys.stderr, level=level, format=fmt, datefmt="%H:%M:%S")


def run(stop: threading.Event | None = None) -> int:
    """The daemon's main entry: load, enforce, serve, loop until SIGTERM (or `stop` is set, for tests)."""
    if not paths.dev_mode() and os.geteuid() != 0:
        print("calmad must run as root (or set CALMA_ROOT for dev mode)", file=sys.stderr)
        return 1
    paths.ensure_dirs()
    daemon = Daemon()
    if stop is not None:
        daemon.stop = stop
    now_wall = time.time()
    daemon.load_rules(now_wall)
    for ev in daemon.state.check_boot(daemon.rules, now_wall):
        log.info("%s", ev.message)
    daemon.state.check_day(now_wall, daemon.day_starts_at)
    daemon.render()
    daemon.state.save(force=True)
    try:
        paths.atomic_write(paths.pid_file(), f"{os.getpid()}\n")
    except OSError:
        pass

    def on_signal(signum, _frame):
        log.info("signal %s: stopping", signal.Signals(signum).name)
        daemon.stop.set()
        with daemon.cond:
            daemon.cond.notify_all()

    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, on_signal)
        signal.signal(signal.SIGINT, on_signal)
    try:
        server = serve(daemon)
    except OSError as e:
        log.error("cannot listen on 127.0.0.1:%d: %s", paths.port(), e)
        return 1
    log.info("calma %s started: %d rules (port %d%s)", __version__, len(daemon.rules["rules"]), paths.port(),
             ", dev mode" if paths.dev_mode() else "")
    try:
        while not daemon.stop.is_set():
            started = time.monotonic()
            try:
                daemon.tick()
            except Exception:  # keep the loop alive whatever happens
                log.exception("tick failed")
            daemon.stop.wait(max(0.0, 1.0 - (time.monotonic() - started)))
    finally:
        server.shutdown()
        with daemon.lock:
            daemon.state.save(force=True)
        try:
            paths.pid_file().unlink()
        except OSError:
            pass
        log.info("stopped")
    return 0


def _systemctl(*args: str) -> tuple[int, str]:
    try:
        r = subprocess.run(["systemctl", *args], capture_output=True, text=True, timeout=30)
        return r.returncode, (r.stdout + r.stderr).strip()
    except (OSError, subprocess.SubprocessError) as e:
        return 1, str(e)


def watchdog() -> int:
    """Repair the service, the policy and the flags. Prints what it repaired."""
    paths.ensure_dirs()
    repaired: list[str] = []
    if not paths.dev_mode() and shutil.which("systemctl"):
        for unit in ("calma.service", "calma-watchdog.timer"):
            rc, out = _systemctl("is-enabled", unit)
            if out.strip() == "masked":
                _systemctl("unmask", unit)
                repaired.append(f"unmasked {unit}")
            if rc != 0 or out.strip() not in ("enabled", "static"):
                if _systemctl("enable", unit)[0] == 0:
                    repaired.append(f"enabled {unit}")
            rc, out = _systemctl("is-active", unit)
            if rc != 0:
                if _systemctl("start", unit)[0] == 0:
                    repaired.append(f"started {unit}")
    # policy and hosts from the trust store (what the daemon last accepted), fail closed
    try:
        rules = rules_mod.loads(paths.accepted_rules().read_bytes())
    except (OSError, rules_mod.RulesError):
        try:
            rules = rules_mod.load()
        except rules_mod.RulesError:
            rules = rules_mod.empty()
    st = state_mod.State.load()
    open_rules = frozenset(st.sessions) if st.boot_id == state_mod.boot_id() else frozenset()
    if not paths.policy_file().exists():
        chromium.write_policy(rules, open_rules)
        repaired.append(f"wrote {paths.policy_file()}")
    elif chromium.write_policy(rules, open_rules):
        repaired.append(f"refreshed {paths.policy_file()}")
    if hosts.write_hosts(rules, open_rules):
        repaired.append(f"repaired the Calma block in {paths.hosts_file()}")
    for f in chromium.repair_flags():
        repaired.append(f"repaired {f}")
    if repaired:
        for r in repaired:
            print(f"watchdog: {r}")
    else:
        print("watchdog: nothing to repair")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="calmad", description="Calma daemon")
    parser.add_argument("--watchdog", action="store_true", help="repair the service, policy and flags, then exit")
    parser.add_argument("--debug", action="store_true", help="verbose logging")
    parser.add_argument("--version", action="version", version=f"calmad {__version__}")
    args = parser.parse_args(argv)
    setup_logging(logging.DEBUG if args.debug else logging.INFO)
    if args.watchdog:
        return watchdog()
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
