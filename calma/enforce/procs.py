"""Scan /proc for processes of blocked rules and kill them (or report them, in dev mode)."""

from __future__ import annotations

import os
import signal
from pathlib import Path

from calma import paths

PROC = Path("/proc")


def _comm(pid_dir: Path) -> str | None:
    try:
        return (pid_dir / "comm").read_text().strip()
    except OSError:
        return None


def _exe_base(pid_dir: Path) -> str | None:
    try:
        return os.path.basename(os.readlink(pid_dir / "exe")).removesuffix(" (deleted)")
    except OSError:
        return None


def find(names: set[str]) -> list[tuple[int, str]]:
    """(pid, name) of every process whose comm or executable basename is in `names`."""
    if not names:
        return []
    out: list[tuple[int, str]] = []
    me = os.getpid()
    try:
        entries = os.listdir(PROC)
    except OSError:
        return out
    for entry in entries:
        if not entry.isdigit():
            continue
        pid = int(entry)
        if pid == me or pid == 1:
            continue
        d = PROC / entry
        comm = _comm(d)
        if comm in names:
            out.append((pid, comm))
            continue
        base = _exe_base(d)
        if base in names:
            out.append((pid, base))
    return out


def running(names: set[str]) -> bool:
    return bool(find(names))


def kill_blocked(rules: dict, open_rules: set[str] | frozenset[str]) -> list[tuple[int, str]]:
    """SIGKILL processes of blocked rules. Returns what was killed (or would be, in dev mode)."""
    names: set[str] = set()
    for name, rule in (rules.get("rules") or {}).items():
        if name not in open_rules:
            names.update(rule.get("processes", []))
    victims = find(names)
    if not paths.kill_enabled():
        return victims
    killed = []
    for pid, pname in victims:
        try:
            os.kill(pid, signal.SIGKILL)
            killed.append((pid, pname))
        except ProcessLookupError:
            pass
        except PermissionError:
            pass
    return killed


def chromium_running() -> bool:
    return running({"chromium", "chrome", "chromium-browser", "google-chrome"})
