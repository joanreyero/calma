#!/usr/bin/env python3
"""Mock calmad for developing the bar plugin without the real daemon.

Serves the HTTP API from ARCHITECTURE.md with fixture rules: `x` has a
session open, `youtube` is blocked and can be opened, `steam` has no sessions
left today. /open and /close change the in-memory state like the daemon
would (budgets, cooldown, expiry) so the plugin can be exercised end to end.

    python3 plugin/dev/mock-daemon.py [port]      # default 7390

Loopback only, stdlib only. Not for production use.
"""

from __future__ import annotations

import json
import sys
import threading
import time
import tomllib
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

VERSION = "0.1.0-mock"
DAY_STARTS_AT = 4

FALLBACK_COLORS = {
    "accent": "#7aa2f7",
    "background": "#1a1b26",
    "foreground": "#c0caf5",
    "color0": "#15161e",
    "color1": "#f7768e",
    "color2": "#9ece6a",
    "color3": "#e0af68",
    "color4": "#7aa2f7",
    "color5": "#bb9af7",
    "color6": "#7dcfff",
    "color7": "#a9b1d6",
}

PRESETS = {
    "x": {
        "label": "X",
        "glyph": "𝕏",
        "domains": ["x.com", "twitter.com"],
        "home": "https://x.com/home",
    },
    "youtube": {
        "label": "YouTube",
        "glyph": "",
        "domains": ["youtube.com", "youtu.be", "youtube-nocookie.com"],
        "home": "https://www.youtube.com/feed/subscriptions",
    },
}

RULES = {
    "x": {
        "preset": "x",
        "domains": [],
        "processes": [],
        "sessions_per_day": 2,
        "session_minutes": 5,
        "daily_minutes": 10,
        "cooldown_minutes": 0,
    },
    "youtube": {
        "preset": "youtube",
        "domains": [],
        "processes": [],
        "sessions_per_day": 1,
        "session_minutes": 20,
        "daily_minutes": 20,
        "cooldown_minutes": 0,
    },
    "steam": {
        "label": "Steam",
        "domains": ["store.steampowered.com"],
        "processes": ["steam"],
        "sessions_per_day": 1,
        "session_minutes": 60,
        "daily_minutes": 60,
        "cooldown_minutes": 0,
    },
}


def next_reset(now: float) -> float:
    local = datetime.fromtimestamp(now)
    reset = local.replace(hour=DAY_STARTS_AT, minute=0, second=0, microsecond=0)
    if reset <= local:
        reset += timedelta(days=1)
    return reset.timestamp()


def day_of(now: float) -> str:
    return (datetime.fromtimestamp(now) - timedelta(hours=DAY_STARTS_AT)).strftime("%Y-%m-%d")


class State:
    """Day usage and sessions, guarded by one lock; a condition for /events."""

    def __init__(self) -> None:
        self.lock = threading.Condition()
        now = time.time()
        self.usage = {name: {"sessions_used": 0, "seconds_used": 0, "last_session_end": None} for name in RULES}
        self.sessions: dict[str, dict] = {}
        self.last_change = now
        # Fixtures: x mid-session (2:22 used of 5 min), steam spent for today.
        self.usage["x"] = {"sessions_used": 1, "seconds_used": 142, "last_session_end": None}
        self.sessions["x"] = {"started": now - 142, "ends_at": now - 142 + 300, "minutes": 5}
        self.usage["steam"] = {"sessions_used": 1, "seconds_used": 3600, "last_session_end": now - 600}
        self.extension_seen: float | None = None

    # ---- bookkeeping --------------------------------------------------
    def tick(self) -> None:
        with self.lock:
            now = time.time()
            for name, session in list(self.sessions.items()):
                if now >= session["ends_at"]:
                    self._end(name, now)

    def _end(self, name: str, now: float) -> None:
        session = self.sessions.pop(name, None)
        if session is None:
            return
        used = min(now, session["ends_at"]) - session["started"]
        usage = self.usage[name]
        usage["seconds_used"] += int(used)
        usage["last_session_end"] = now
        self._changed(now)

    def _changed(self, now: float) -> None:
        self.last_change = now
        self.lock.notify_all()

    # ---- views ---------------------------------------------------------
    def rule_view(self, name: str, now: float) -> dict:
        rule = RULES[name]
        preset = PRESETS.get(rule.get("preset", ""), {})
        usage = self.usage[name]
        session = self.sessions.get(name)
        seconds_used = usage["seconds_used"]
        if session is not None:
            seconds_used += int(now - session["started"])
        daily_seconds = rule["daily_minutes"] * 60
        seconds_left = max(0, daily_seconds - seconds_used)
        sessions_left = max(0, rule["sessions_per_day"] - usage["sessions_used"])
        reason = None
        opportunity = None
        if session is not None:
            reason = "already open"
        elif rule["sessions_per_day"] == 0:
            reason = "always blocked"
        elif sessions_left == 0:
            reason = "no sessions left today"
            opportunity = next_reset(now)
        elif seconds_left == 0:
            reason = "no time left today"
            opportunity = next_reset(now)
        elif usage["last_session_end"] and now - usage["last_session_end"] < rule["cooldown_minutes"] * 60:
            reason = "cooling down"
            opportunity = usage["last_session_end"] + rule["cooldown_minutes"] * 60
        return {
            "label": rule.get("label", preset.get("label", name)),
            "glyph": preset.get("glyph", ""),
            "preset": rule.get("preset"),
            "domains": preset.get("domains", []) + rule["domains"],
            "processes": rule["processes"],
            "home": preset.get("home"),
            "sessions_per_day": rule["sessions_per_day"],
            "session_minutes": rule["session_minutes"],
            "daily_minutes": rule["daily_minutes"],
            "cooldown_minutes": rule["cooldown_minutes"],
            "sessions_used": usage["sessions_used"],
            "seconds_used": seconds_used,
            "sessions_left": sessions_left,
            "seconds_left": seconds_left,
            "state": "open" if session is not None else "blocked",
            "session": None
            if session is None
            else {
                "ends_at": session["ends_at"],
                "seconds_left": max(0, int(session["ends_at"] - now)),
                "minutes": session["minutes"],
            },
            "can_open": reason is None,
            "cannot_open_reason": reason,
            "next_opportunity": opportunity,
        }

    def status(self) -> dict:
        self.tick()
        with self.lock:
            now = time.time()
            problems: list[str] = []
            return {
                "version": VERSION,
                "now": now,
                "day": day_of(now),
                "day_starts_at": DAY_STARTS_AT,
                "next_reset": next_reset(now),
                "password_set": True,
                "healthy": not problems,
                "problems": problems,
                "rules": {name: self.rule_view(name, now) for name in RULES},
            }

    # ---- actions -------------------------------------------------------
    def open(self, name: str, minutes: int | None) -> tuple[int, dict]:
        self.tick()
        with self.lock:
            now = time.time()
            if name not in RULES:
                return 404, {"ok": False, "error": "unknown rule"}
            view = self.rule_view(name, now)
            if not view["can_open"]:
                return 409, {"ok": False, "error": view["cannot_open_reason"], "rule": view}
            rule = RULES[name]
            requested = minutes or rule["session_minutes"]
            length = min(requested, rule["session_minutes"], max(1, view["seconds_left"] // 60))
            self.usage[name]["sessions_used"] += 1
            self.sessions[name] = {"started": now, "ends_at": now + length * 60, "minutes": length}
            self._changed(now)
            return 200, {"ok": True, "rule": self.rule_view(name, now)}

    def close(self, name: str) -> tuple[int, dict]:
        with self.lock:
            now = time.time()
            if name not in RULES:
                return 404, {"ok": False, "error": "unknown rule"}
            if name in self.sessions:
                self._end(name, now)
            return 200, {"ok": True, "rule": self.rule_view(name, now)}

    def wait_for_change(self, since: float, timeout: float = 25.0) -> bool:
        with self.lock:
            if self.last_change > since:
                return True
            deadline = time.monotonic() + timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return self.last_change > since
                self.lock.wait(min(remaining, 1.0))
                if self.last_change > since:
                    return True
                # Expiry happens on tick, which /events must drive itself.
                for name, session in list(self.sessions.items()):
                    if time.time() >= session["ends_at"]:
                        self._end(name, time.time())


STATE = State()


def theme() -> dict:
    home = Path.home()
    base = home / ".local/state/omarchy/current"
    colors = dict(FALLBACK_COLORS)
    name = "tokyo-night"
    try:
        with open(base / "theme/colors.toml", "rb") as fh:
            data = tomllib.load(fh)
        colors = {k: v for k, v in data.items() if isinstance(v, str)}
        for section in data.values():
            if isinstance(section, dict):
                for k, v in section.items():
                    if isinstance(v, str):
                        colors.setdefault(k, v)
    except (OSError, tomllib.TOMLDecodeError):
        pass
    try:
        name = (base / "theme.name").read_text().strip() or name
    except OSError:
        pass
    background = colors.get("background", FALLBACK_COLORS["background"]).lstrip("#")
    try:
        luminance = sum(int(background[i : i + 2], 16) for i in (0, 2, 4)) / 3
    except ValueError:
        luminance = 0
    return {
        "mode": "light" if luminance > 128 else "dark",
        "name": name,
        "colors": colors,
        "font": "JetBrainsMono Nerd Font",
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "calma-mock/" + VERSION

    def log_message(self, fmt: str, *args: object) -> None:
        sys.stderr.write(f"mock-daemon: {self.command} {fmt % args}\n")

    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Calma")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send(204, {})

    def do_GET(self) -> None:  # noqa: N802
        url = urlparse(self.path)
        if url.path == "/status":
            self._send(200, STATE.status())
        elif url.path == "/theme":
            self._send(200, theme())
        elif url.path == "/events":
            since = 0.0
            try:
                since = float(parse_qs(url.query).get("since", ["0"])[0])
            except ValueError:
                pass
            if STATE.wait_for_change(since):
                self._send(200, {"changed": True, "status": STATE.status()})
            else:
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
        if url.path == "/open":
            minutes = body.get("minutes")
            code, payload = STATE.open(str(body.get("rule", "")), int(minutes) if minutes else None)
            self._send(code, payload)
        elif url.path == "/close":
            code, payload = STATE.close(str(body.get("rule", "")))
            self._send(code, payload)
        elif url.path == "/ping":
            STATE.extension_seen = time.time()
            self._send(200, {"ok": True})
        else:
            self._send(404, {"ok": False, "error": "not found"})


def main(argv: list[str]) -> int:
    port = int(argv[1]) if len(argv) > 1 else 7390
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    sys.stderr.write(f"mock-daemon: calma {VERSION} listening on http://127.0.0.1:{port}\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
