"""Day budget and session state: persistence, day reset, session start/tick/close, reboot handling."""

from __future__ import annotations

import datetime as dt
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from calma import paths

BOOT_ID_FILE = Path("/proc/sys/kernel/random/boot_id")
MAX_TAMPER_EVENTS = 200


def boottime() -> float:
    """Seconds since boot, including suspend (CLOCK_BOOTTIME)."""
    return time.clock_gettime(time.CLOCK_BOOTTIME)


def boot_id() -> str:
    try:
        return BOOT_ID_FILE.read_text().strip()
    except OSError:
        return "unknown"


def day_for(now_wall: float, day_starts_at: int) -> str:
    """The Calma day (local date of now minus day_starts_at hours) as YYYY-MM-DD."""
    local = dt.datetime.fromtimestamp(now_wall) - dt.timedelta(hours=day_starts_at)
    return local.date().isoformat()


def next_reset(now_wall: float, day_starts_at: int) -> float:
    """Unix time of the next budget reset."""
    local = dt.datetime.fromtimestamp(now_wall)
    candidate = local.replace(hour=day_starts_at, minute=0, second=0, microsecond=0)
    if candidate <= local:
        candidate += dt.timedelta(days=1)
    return candidate.timestamp()


def empty_usage() -> dict[str, Any]:
    return {"sessions_used": 0, "seconds_used": 0, "last_session_end": None}


@dataclass
class Event:
    """Something the daemon should tell the user about."""

    rule: str
    kind: str  # "ended" | "one_minute" | "started"
    message: str


@dataclass
class State:
    day: str = ""
    boot_id: str = ""
    rules: dict[str, dict[str, Any]] = field(default_factory=dict)
    sessions: dict[str, dict[str, Any]] = field(default_factory=dict)
    tamper_events: list[dict[str, Any]] = field(default_factory=list)
    extension_seen: float | None = None
    extension_version: str | None = None
    dirty: bool = False
    _last_tick_mono: float | None = None
    _warned: set[str] = field(default_factory=set)
    _path: Path | None = None

    # Persistence --------------------------------------------------------

    @classmethod
    def load(cls, path: Path | None = None) -> State:
        path = path or paths.state_file()
        st = cls(_path=path)
        try:
            raw = json.loads(path.read_text())
        except (OSError, ValueError):
            raw = {}
        if isinstance(raw, dict):
            st.day = str(raw.get("day") or "")
            st.boot_id = str(raw.get("boot_id") or "")
            rules = raw.get("rules") or {}
            if isinstance(rules, dict):
                for name, u in rules.items():
                    if isinstance(u, dict):
                        usage = empty_usage()
                        usage["sessions_used"] = int(u.get("sessions_used") or 0)
                        usage["seconds_used"] = int(u.get("seconds_used") or 0)
                        lse = u.get("last_session_end")
                        usage["last_session_end"] = float(lse) if lse is not None else None
                        st.rules[name] = usage
            sessions = raw.get("sessions") or {}
            if isinstance(sessions, dict):
                st.sessions = {k: dict(v) for k, v in sessions.items() if isinstance(v, dict)}
            te = raw.get("tamper_events") or []
            if isinstance(te, list):
                st.tamper_events = [e for e in te if isinstance(e, dict)]
            es = raw.get("extension_seen")
            st.extension_seen = float(es) if es is not None else None
            ev = raw.get("extension_version")
            st.extension_version = str(ev) if ev else None
        return st

    def to_dict(self) -> dict[str, Any]:
        return {
            "day": self.day,
            "boot_id": self.boot_id,
            "rules": self.rules,
            "sessions": self.sessions,
            "tamper_events": self.tamper_events[-MAX_TAMPER_EVENTS:],
            "extension_seen": self.extension_seen,
            "extension_version": self.extension_version,
        }

    def save(self, force: bool = False) -> bool:
        if not (self.dirty or force):
            return False
        path = self._path or paths.state_file()
        paths.atomic_write(path, json.dumps(self.to_dict(), indent=2) + "\n", 0o644)
        self.dirty = False
        return True

    # Bookkeeping --------------------------------------------------------

    def usage(self, name: str) -> dict[str, Any]:
        u = self.rules.get(name)
        if u is None:
            u = self.rules[name] = empty_usage()
            self.dirty = True
        return u

    def record_tamper(self, what: str, now_wall: float | None = None) -> None:
        self.tamper_events.append({"at": now_wall if now_wall is not None else time.time(), "what": what})
        self.tamper_events = self.tamper_events[-MAX_TAMPER_EVENTS:]
        self.dirty = True

    def tamper_events_today(self, now_wall: float, day_starts_at: int) -> list[dict[str, Any]]:
        today = day_for(now_wall, day_starts_at)
        return [e for e in self.tamper_events if day_for(float(e.get("at", 0)), day_starts_at) == today]

    def check_boot(self, rules: dict, now_wall: float, current_boot_id: str | None = None) -> list[Event]:
        """After a reboot every running session ends and is charged in full (fail closed)."""
        current = current_boot_id if current_boot_id is not None else boot_id()
        events: list[Event] = []
        if self.boot_id and self.boot_id != current:
            for name in list(self.sessions):
                sess = self.sessions.pop(name)
                u = self.usage(name)
                full = int(sess.get("seconds") or int(sess.get("minutes", 0)) * 60)
                started = float(sess.get("started_mono", 0))
                already = max(0.0, float(sess.get("charged_mono", started)) - started)
                # charge whatever of the full length was not yet charged tick by tick
                u["seconds_used"] = int(u["seconds_used"]) + max(0, full - int(already))
                u["last_session_end"] = now_wall
                events.append(Event(name, "ended", f"{_label(rules, name)} · session ended by a reboot"))
        if self.boot_id != current:
            self.boot_id = current
            self.dirty = True
        self._last_tick_mono = None
        return events

    def check_day(self, now_wall: float, day_starts_at: int) -> bool:
        """Reset usage when the Calma day moved forward. Never on a backwards clock."""
        today = day_for(now_wall, day_starts_at)
        if not self.day:
            self.day = today
            self.dirty = True
            return False
        if today > self.day:
            self.day = today
            for name in list(self.rules):
                self.rules[name] = empty_usage()
            self.dirty = True
            return True
        return False

    def prune(self, rules: dict) -> None:
        """Drop usage and sessions of rules that no longer exist."""
        names = set((rules.get("rules") or {}).keys())
        for name in list(self.rules):
            if name not in names:
                del self.rules[name]
                self.dirty = True
        for name in list(self.sessions):
            if name not in names:
                del self.sessions[name]
                self.dirty = True

    # Sessions -----------------------------------------------------------

    def cannot_open_reason(self, name: str, rule: dict, now_wall: float) -> str | None:
        if name in self.sessions:
            return "already open"
        if rule["sessions_per_day"] <= 0:
            return "always blocked"
        u = self.usage(name)
        if u["sessions_used"] >= rule["sessions_per_day"]:
            return "no sessions left today"
        if u["seconds_used"] >= rule["daily_minutes"] * 60:
            return "no time left today"
        lse = u.get("last_session_end")
        if lse is not None and rule["cooldown_minutes"] > 0:
            if now_wall - float(lse) < rule["cooldown_minutes"] * 60:
                return "cooling down"
        return None

    def next_opportunity(self, name: str, rule: dict, now_wall: float, day_starts_at: int) -> float | None:
        reason = self.cannot_open_reason(name, rule, now_wall)
        if reason in (None, "already open", "always blocked"):
            return None
        if reason == "cooling down":
            u = self.usage(name)
            return float(u["last_session_end"]) + rule["cooldown_minutes"] * 60
        return next_reset(now_wall, day_starts_at)

    def remaining_seconds(self, name: str, rule: dict) -> int:
        u = self.usage(name)
        return max(0, rule["daily_minutes"] * 60 - int(u["seconds_used"]))

    def open_session(
        self,
        name: str,
        rule: dict,
        now_wall: float,
        now_mono: float | None = None,
        minutes: int | None = None,
    ) -> tuple[str | None, dict[str, Any] | None]:
        """Try to start a session. Returns (error, session)."""
        reason = self.cannot_open_reason(name, rule, now_wall)
        if reason:
            return reason, None
        now_mono = boottime() if now_mono is None else now_mono
        requested = rule["session_minutes"] if minutes is None else max(1, int(minutes))
        remaining = self.remaining_seconds(name, rule)
        length = min(requested * 60, rule["session_minutes"] * 60, remaining)
        if length <= 0:
            return "no time left today", None
        u = self.usage(name)
        u["sessions_used"] = int(u["sessions_used"]) + 1
        sess = {
            "started_wall": now_wall,
            "started_mono": now_mono,
            "ends_mono": now_mono + length,
            "charged_mono": now_mono,
            "minutes": max(1, round(length / 60)),
            "seconds": length,
        }
        self.sessions[name] = sess
        self._warned.discard(name)
        self.dirty = True
        return None, sess

    def close_session(self, name: str, rules: dict, now_wall: float, now_mono: float | None = None) -> Event | None:
        """End a session now, charging the real time used. Idempotent."""
        sess = self.sessions.get(name)
        if sess is None:
            return None
        now_mono = boottime() if now_mono is None else now_mono
        self._charge(name, sess, min(now_mono, float(sess["ends_mono"])))
        del self.sessions[name]
        u = self.usage(name)
        u["last_session_end"] = now_wall
        self.dirty = True
        return Event(name, "ended", self._ended_message(rules, name))

    def _charge(self, name: str, sess: dict[str, Any], upto_mono: float) -> None:
        last = float(sess.get("charged_mono", sess["started_mono"]))
        elapsed = upto_mono - last
        if elapsed <= 0:
            return
        u = self.usage(name)
        # keep fractional seconds in the session so the integer charge is exact over time
        acc = float(sess.get("_frac", 0.0)) + elapsed
        whole = int(acc)
        sess["_frac"] = acc - whole
        sess["charged_mono"] = upto_mono
        if whole:
            u["seconds_used"] = int(u["seconds_used"]) + whole
            self.dirty = True

    def tick(self, rules: dict, now_wall: float, now_mono: float | None = None) -> list[Event]:
        """One loop tick: charge running sessions, end expired ones, emit events."""
        now_mono = boottime() if now_mono is None else now_mono
        events: list[Event] = []
        for name in list(self.sessions):
            sess = self.sessions[name]
            ends = float(sess["ends_mono"])
            rule = (rules.get("rules") or {}).get(name)
            if rule is None:
                del self.sessions[name]
                self.dirty = True
                continue
            if now_mono >= ends:
                self._charge(name, sess, ends)
                del self.sessions[name]
                self.usage(name)["last_session_end"] = now_wall
                self.dirty = True
                events.append(Event(name, "ended", self._ended_message(rules, name)))
                continue
            self._charge(name, sess, now_mono)
            left = ends - now_mono
            if left <= 60 and name not in self._warned and float(sess.get("seconds", 0)) > 90:
                self._warned.add(name)
                events.append(Event(name, "one_minute", f"{_label(rules, name)} · 1 minute left"))
        self._last_tick_mono = now_mono
        return events

    def _ended_message(self, rules: dict, name: str) -> str:
        rule = (rules.get("rules") or {}).get(name) or {}
        u = self.usage(name)
        left = max(0, int(rule.get("sessions_per_day", 0)) - int(u["sessions_used"]))
        if rule.get("daily_minutes") and self.remaining_seconds(name, rule) <= 0:
            left = 0
        noun = "session" if left == 1 else "sessions"
        return f"{_label(rules, name)} · session over · {left} {noun} left today"

    def session_view(self, name: str, now_wall: float, now_mono: float | None = None) -> dict[str, Any] | None:
        sess = self.sessions.get(name)
        if sess is None:
            return None
        now_mono = boottime() if now_mono is None else now_mono
        left = max(0, int(round(float(sess["ends_mono"]) - now_mono)))
        return {"ends_at": now_wall + left, "seconds_left": left, "minutes": sess["minutes"]}


def _label(rules: dict, name: str) -> str:
    from calma import rules as rules_mod

    rule = (rules.get("rules") or {}).get(name)
    if rule is None:
        return name
    return rules_mod.expand(name, rule)["label"]
