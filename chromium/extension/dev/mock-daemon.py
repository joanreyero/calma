#!/usr/bin/env python3
"""Mock Calma daemon for exercising the extension without the real one.

Serves the HTTP API from ARCHITECTURE.md on 127.0.0.1:<port> (argv[1], default 7390)
with fixture data: rule ``x`` open (a 5-minute session running), rule ``youtube`` blocked
and openable, rule ``steam`` blocked with no sessions left. ``POST /open`` and
``POST /close`` mutate the fixture so the blocked page round-trip can be tested.

    python3 chromium/extension/dev/mock-daemon.py 7390
    python3 chromium/extension/dev/mock-daemon.py 7390 --theme light   # light fallback palette
    python3 chromium/extension/dev/mock-daemon.py 7390 --all-blocked   # no session running

Stdlib only.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

VERSION = "0.1.0"

DARK_THEME = {
    "mode": "dark",
    "name": "tokyo-night",
    "font": "JetBrainsMono Nerd Font",
    "colors": {
        "accent": "#7aa2f7",
        "selection": "#292e42",
        "muted": "#414868",
        "background": "#1a1b26",
        "dark_background": "#13141c",
        "darker_background": "#0e0e14",
        "lighter_background": "#24283b",
        "foreground": "#a9b1d6",
        "dark_foreground": "#565f89",
        "light_foreground": "#b4bee6",
        "bright_foreground": "#c0caf5",
        "red": "#f7768e",
        "yellow": "#e0af68",
        "orange": "#eb927b",
        "green": "#9ece6a",
        "cyan": "#449dab",
        "blue": "#7aa2f7",
        "magenta": "#ad8ee6",
        "brown": "#75493d",
    },
}

LIGHT_THEME = {
    "mode": "light",
    "name": "rose-pine-dawn",
    "font": "JetBrainsMono Nerd Font",
    "colors": {
        "accent": "#907aa9",
        "selection": "#dfdad9",
        "muted": "#9893a5",
        "background": "#faf4ed",
        "dark_background": "#f2e9e1",
        "lighter_background": "#fffaf3",
        "foreground": "#575279",
        "dark_foreground": "#797593",
        "bright_foreground": "#26233a",
        "red": "#b4637a",
        "yellow": "#ea9d34",
        "green": "#286983",
        "blue": "#56949f",
        "magenta": "#907aa9",
    },
}

PRESETS = {
    "x": {"label": "X", "glyph": "𝕏", "domains": ["x.com", "twitter.com"], "home": "https://x.com/home"},
    "youtube": {
        "label": "YouTube",
        "glyph": "",
        "domains": ["youtube.com", "youtu.be", "youtube-nocookie.com"],
        "home": "https://www.youtube.com/feed/subscriptions",
    },
}


class Fixture:
    """Mutable in-memory state mirroring the daemon's view of the day."""

    def __init__(self, all_blocked: bool = False) -> None:
        self.lock = threading.Lock()
        self.last_change = time.time()
        self.day_starts_at = 4
        self.rules: dict[str, dict] = {
            "x": {
                "preset": "x",
                "label": "X",
                "glyph": "𝕏",
                "domains": PRESETS["x"]["domains"],
                "processes": [],
                "home": PRESETS["x"]["home"],
                "sessions_per_day": 2,
                "session_minutes": 5,
                "daily_minutes": 10,
                "cooldown_minutes": 0,
                "sessions_used": 1,
                "seconds_used": 142,
                "session": None,
            },
            "youtube": {
                "preset": "youtube",
                "label": "YouTube",
                "glyph": "",
                "domains": PRESETS["youtube"]["domains"],
                "processes": [],
                "home": PRESETS["youtube"]["home"],
                "sessions_per_day": 1,
                "session_minutes": 20,
                "daily_minutes": 20,
                "cooldown_minutes": 0,
                "sessions_used": 0,
                "seconds_used": 0,
                "session": None,
            },
            "steam": {
                "preset": None,
                "label": "Steam",
                "glyph": None,
                "domains": ["store.steampowered.com"],
                "processes": ["steam"],
                "home": None,
                "sessions_per_day": 1,
                "session_minutes": 60,
                "daily_minutes": 60,
                "cooldown_minutes": 0,
                "sessions_used": 1,
                "seconds_used": 3600,
                "session": None,
            },
        }
        if not all_blocked:
            now = time.time()
            self.rules["x"]["session"] = {"started": now, "ends_at": now + 300, "minutes": 5}

    # -- helpers -------------------------------------------------------------------

    def next_reset(self) -> float:
        lt = time.localtime()
        reset = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, self.day_starts_at, 0, 0, 0, 0, -1))
        if reset <= time.time():
            reset += 86400
        return reset

    def today(self) -> str:
        """Local date of now - day_starts_at hours, as the daemon computes it."""
        return time.strftime("%Y-%m-%d", time.localtime(time.time() - self.day_starts_at * 3600))

    def _expire(self) -> None:
        now = time.time()
        for rule in self.rules.values():
            s = rule["session"]
            if s and s["ends_at"] <= now:
                rule["seconds_used"] += int(s["ends_at"] - s["started"])
                rule["session"] = None
                rule["last_session_end"] = now
                self.last_change = now

    def rule_view(self, name: str) -> dict:
        r = self.rules[name]
        now = time.time()
        seconds_left = max(0, r["daily_minutes"] * 60 - r["seconds_used"])
        sessions_left = max(0, r["sessions_per_day"] - r["sessions_used"])
        session = None
        state = "blocked"
        if r["session"]:
            state = "open"
            session = {
                "ends_at": r["session"]["ends_at"],
                "seconds_left": max(0, int(r["session"]["ends_at"] - now)),
                "minutes": r["session"]["minutes"],
            }
            seconds_left = max(0, seconds_left - int(now - r["session"]["started"]))
        reason = None
        next_opportunity = None
        last_end = r.get("last_session_end")
        cooldown_until = (last_end or 0) + r["cooldown_minutes"] * 60
        if state == "open":
            reason = "already open"
        elif r["sessions_per_day"] == 0:
            reason = "always blocked"
        elif sessions_left == 0:
            reason = "no sessions left today"
            next_opportunity = self.next_reset()
        elif seconds_left <= 0:
            reason = "no time left today"
            next_opportunity = self.next_reset()
        elif last_end and cooldown_until > now:
            reason = "cooling down"
            next_opportunity = cooldown_until
        return {
            "label": r["label"],
            "glyph": r["glyph"],
            "preset": r["preset"],
            "domains": r["domains"],
            "processes": r["processes"],
            "home": r["home"],
            "sessions_per_day": r["sessions_per_day"],
            "session_minutes": r["session_minutes"],
            "daily_minutes": r["daily_minutes"],
            "cooldown_minutes": r["cooldown_minutes"],
            "sessions_used": r["sessions_used"],
            "seconds_used": r["seconds_used"],
            "sessions_left": sessions_left,
            "seconds_left": seconds_left,
            "state": state,
            "session": session,
            "can_open": reason is None,
            "cannot_open_reason": reason,
            "next_opportunity": next_opportunity,
        }

    def status(self) -> dict:
        with self.lock:
            self._expire()
            return {
                "version": VERSION,
                "now": time.time(),
                "day": self.today(),
                "day_starts_at": self.day_starts_at,
                "next_reset": self.next_reset(),
                "password_set": True,
                "healthy": True,
                "problems": [],
                "rules": {name: self.rule_view(name) for name in self.rules},
            }

    def open(self, name: str, minutes: int | None) -> tuple[int, dict]:
        with self.lock:
            self._expire()
            if name not in self.rules:
                return 404, {"ok": False, "error": "unknown rule"}
            view = self.rule_view(name)
            if not view["can_open"]:
                return 409, {"ok": False, "error": view["cannot_open_reason"], "rule": view}
            r = self.rules[name]
            length = min(minutes or r["session_minutes"], r["session_minutes"], max(1, view["seconds_left"] // 60))
            now = time.time()
            r["session"] = {"started": now, "ends_at": now + length * 60, "minutes": length}
            r["sessions_used"] += 1
            self.last_change = now
            print(f"mock: opened {name} for {length} min", file=sys.stderr)
            return 200, {"ok": True, "rule": self.rule_view(name)}

    def close(self, name: str) -> tuple[int, dict]:
        with self.lock:
            self._expire()
            if name not in self.rules:
                return 404, {"ok": False, "error": "unknown rule"}
            r = self.rules[name]
            if r["session"]:
                now = time.time()
                r["seconds_used"] += int(now - r["session"]["started"])
                r["session"] = None
                r["last_session_end"] = now
                self.last_change = now
                print(f"mock: closed {name}", file=sys.stderr)
            return 200, {"ok": True, "rule": self.rule_view(name)}


FIXTURE = Fixture()
THEME = copy.deepcopy(DARK_THEME)


class Handler(BaseHTTPRequestHandler):
    server_version = "calma-mock/" + VERSION

    def log_message(self, fmt: str, *args: object) -> None:  # quieter
        print(f"mock: {self.command} {self.path} -> {args[1] if len(args) > 1 else ''}", file=sys.stderr)

    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Calma")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send(204, {})

    def do_GET(self) -> None:  # noqa: N802
        url = urlparse(self.path)
        if url.path == "/status":
            self._send(200, FIXTURE.status())
        elif url.path == "/theme":
            self._send(200, THEME)
        elif url.path == "/events":
            since = 0.0
            try:
                since = float(parse_qs(url.query).get("since", ["0"])[0])
            except ValueError:
                pass
            # Long-poll up to 25 s, as the real daemon does.
            deadline = time.time() + 25.0
            while time.time() < deadline:
                if FIXTURE.last_change > since:
                    self._send(200, {"changed": True, "status": FIXTURE.status()})
                    return
                time.sleep(0.1)
            self._send(200, {"changed": False})
        else:
            self._send(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.headers.get("X-Calma") != "1":
            self._send(403, {"ok": False, "error": "missing X-Calma header"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._send(400, {"ok": False, "error": "invalid JSON"})
            return
        url = urlparse(self.path)
        if url.path == "/ping":
            print(f"mock: ping from extension {body.get('extension')}", file=sys.stderr)
            self._send(200, {"ok": True})
        elif url.path == "/open":
            minutes = body.get("minutes")
            code, payload = FIXTURE.open(str(body.get("rule", "")), int(minutes) if minutes else None)
            self._send(code, payload)
        elif url.path == "/close":
            code, payload = FIXTURE.close(str(body.get("rule", "")))
            self._send(code, payload)
        else:
            self._send(404, {"ok": False, "error": "not found"})


def main() -> None:
    global FIXTURE, THEME
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("port", nargs="?", type=int, default=7390)
    parser.add_argument("--theme", choices=["dark", "light"], default="dark")
    parser.add_argument("--all-blocked", action="store_true", help="start with no session running")
    parser.add_argument("--cooldown", type=int, default=0, help="cooldown_minutes for every rule")
    args = parser.parse_args()
    FIXTURE = Fixture(all_blocked=args.all_blocked)
    for rule in FIXTURE.rules.values():
        rule["cooldown_minutes"] = args.cooldown
    THEME = copy.deepcopy(DARK_THEME if args.theme == "dark" else LIGHT_THEME)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    who = "all blocked" if args.all_blocked else "x open, youtube blocked, steam exhausted"
    print(f"mock calma daemon on http://127.0.0.1:{args.port} ({who})", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
