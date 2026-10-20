"""Chromium managed policy generation and writing; chromium-flags.conf repair."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from calma import paths
from calma import rules as rules_mod


def _add(lst: list[str], item: str) -> None:
    if item not in lst:
        lst.append(item)


def policy(rules: dict, open_rules: set[str] | frozenset[str]) -> dict:
    """The policy document for these rules, given which rules currently have a session open."""
    blocklist: list[str] = []
    allowlist: list[str] = []
    expanded = {name: rules_mod.expand(name, rule) for name, rule in (rules.get("rules") or {}).items()}
    # blocked rules first (whole domains), then the path exceptions of open rules
    for name, ex in expanded.items():
        if name not in open_rules:
            for d in ex["domains"]:
                _add(blocklist, d)
    for name, ex in expanded.items():
        if name in open_rules:
            for a in ex["allow"]:
                _add(allowlist, a)
            for b in ex["block"]:
                _add(blocklist, b)
            for d in ex["extra_domains"]:
                _add(allowlist, d)
    return {"URLBlocklist": blocklist, "URLAllowlist": allowlist}


def render(rules: dict, open_rules: set[str] | frozenset[str]) -> bytes:
    return (json.dumps(policy(rules, open_rules), indent=2) + "\n").encode()


def write_policy(rules: dict, open_rules: set[str] | frozenset[str]) -> bool:
    """Write the policy file when its content changed. Returns True when written."""
    return paths.write_if_changed(paths.policy_file(), render(rules, open_rules), 0o644)


def policy_ok(rules: dict, open_rules: set[str] | frozenset[str]) -> bool:
    try:
        return paths.policy_file().read_bytes() == render(rules, open_rules)
    except OSError:
        return False


# chromium-flags.conf -----------------------------------------------------

_LOAD_RE = re.compile(r"^(\s*--load-extension=)(.*?)\s*$")


def repair_flags_text(text: str, is_system: bool) -> str | None:
    """Return the repaired file content, or None when nothing needs to change.

    System file: ensure a `--load-extension=` line containing our path exists (append one if
    none). User file: only amend an existing `--load-extension=` line (a user file with one but
    without ours would override the system one); otherwise leave it alone.
    """
    ext = str(paths.extension_dir())
    lines = text.split("\n")
    found = False
    changed = False
    for i, line in enumerate(lines):
        m = _LOAD_RE.match(line)
        if not m:
            continue
        found = True
        parts = [p for p in m.group(2).split(",") if p]
        if ext not in parts:
            parts.append(ext)
            lines[i] = m.group(1) + ",".join(parts)
            changed = True
    if not found and is_system:
        if lines and lines[-1] == "":
            lines.insert(len(lines) - 1, f"--load-extension={ext}")
        else:
            lines.append(f"--load-extension={ext}")
            lines.append("")
        changed = True
    if not changed:
        return None
    out = "\n".join(lines)
    if not out.endswith("\n"):
        out += "\n"
    return out


def repair_flags() -> list[Path]:
    """Repair the system and user flags files; return the files that were rewritten."""
    fixed: list[Path] = []
    system = paths.chromium_flags_file()
    try:
        text = system.read_text()
    except FileNotFoundError:
        text = ""
    except OSError:
        text = None
    if text is not None:
        new = repair_flags_text(text, is_system=True)
        if new is not None:
            try:
                paths.atomic_write(system, new, 0o644)
                fixed.append(system)
            except OSError:
                pass
    for f in paths.user_flags_files():
        try:
            real = f.resolve()
            new = repair_flags_text(real.read_text(), is_system=False)
        except OSError:
            continue
        if new is not None:
            try:
                st = real.stat()
                paths.atomic_write(real, new, st.st_mode & 0o777)
                try:
                    os.chown(real, st.st_uid, st.st_gid)
                except OSError:
                    pass
                fixed.append(f)
            except OSError:
                pass
    return fixed


def extension_problems() -> list[str]:
    """Problems with the unpacked extension files (shared by the daemon self-check and `calma doctor`)."""
    ext = paths.extension_dir()
    if not (ext / "manifest.json").is_file():
        return [f"extension files missing ({ext})"]
    for name in ("background.js", "blocked.html"):
        if not (ext / name).is_file():
            return [f"extension files incomplete ({ext} lacks {name})"]
    return []


def flags_problems() -> list[str]:
    """Human-readable problems with the flags files (for doctor and /status)."""
    ext = str(paths.extension_dir())
    out: list[str] = []
    system = paths.chromium_flags_file()
    try:
        if ext not in system.read_text():
            out.append(f"{system} does not load the extension")
    except OSError:
        out.append(f"{system} is missing")
    for f in paths.user_flags_files():
        try:
            text = f.resolve().read_text()
        except OSError:
            continue
        for line in text.splitlines():
            m = _LOAD_RE.match(line)
            if m and ext not in m.group(2).split(","):
                out.append(f"{f} has a --load-extension line without the Calma extension")
    return out
