"""Rules model: load, validate, normalise, compare for loosening, expand presets, write."""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Any

from calma import crypto, paths, presets

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
DOMAIN_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9-]{2,63}$")
PROCESS_RE = re.compile(r"^[^\s/\x00]{1,64}$")
VERSION = 1
DEFAULT_DAY_STARTS_AT = 4

RULE_INT_FIELDS = ("sessions_per_day", "session_minutes", "daily_minutes", "cooldown_minutes")


class RulesError(ValueError):
    """Invalid rules file or rule definition."""


class PasswordRequired(Exception):
    """A loosening change needs the Calma password."""

    def __init__(self, reasons: list[str]):
        super().__init__("; ".join(reasons))
        self.reasons = reasons


class NoPassword(Exception):
    """Loosening requested but no key exists: nothing can ever be loosened."""


class WrongPassword(Exception):
    """The password did not decrypt the key."""


def empty() -> dict:
    return {"version": VERSION, "settings": {"day_starts_at": DEFAULT_DAY_STARTS_AT}, "rules": {}}


def normalize_domain(d: str) -> str:
    d = d.strip().lower()
    d = re.sub(r"^[a-z]+://", "", d)
    d = d.split("/", 1)[0].split(":", 1)[0]
    if d.startswith("www."):
        d = d[4:]
    return d.rstrip(".")


def _check_int(name: str, rule: dict, field: str, minimum: int) -> int:
    v = rule.get(field)
    if isinstance(v, bool) or not isinstance(v, int):
        raise RulesError(f"rule {name!r}: {field} must be an integer")
    if v < minimum:
        raise RulesError(f"rule {name!r}: {field} must be >= {minimum}")
    return v


def validate_rule(name: str, rule: Any) -> dict:
    """Validate one rule and return a normalised copy (all fields present)."""
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise RulesError(f"invalid rule name {name!r} (use [a-z0-9][a-z0-9-]{{0,31}})")
    if not isinstance(rule, dict):
        raise RulesError(f"rule {name!r} must be an object")
    out: dict[str, Any] = {}
    preset = rule.get("preset")
    if preset is not None:
        if not isinstance(preset, str) or preset not in presets.PRESETS:
            raise RulesError(f"rule {name!r}: unknown preset {preset!r}")
        out["preset"] = preset
    label = rule.get("label")
    if label is not None:
        if not isinstance(label, str) or not label.strip():
            raise RulesError(f"rule {name!r}: label must be a non-empty string")
        out["label"] = label.strip()
    domains = rule.get("domains", [])
    if not isinstance(domains, list) or not all(isinstance(d, str) for d in domains):
        raise RulesError(f"rule {name!r}: domains must be a list of strings")
    norm: list[str] = []
    for d in domains:
        nd = normalize_domain(d)
        if not DOMAIN_RE.match(nd):
            raise RulesError(f"rule {name!r}: invalid domain {d!r}")
        if nd not in norm:
            norm.append(nd)
    out["domains"] = norm
    procs = rule.get("processes", [])
    if not isinstance(procs, list) or not all(isinstance(p, str) for p in procs):
        raise RulesError(f"rule {name!r}: processes must be a list of strings")
    nprocs: list[str] = []
    for p in procs:
        p = p.strip()
        if not PROCESS_RE.match(p):
            raise RulesError(f"rule {name!r}: invalid process name {p!r}")
        if p not in nprocs:
            nprocs.append(p)
    out["processes"] = nprocs
    if preset is None and not norm and not nprocs:
        raise RulesError(f"rule {name!r}: needs a preset, a domain or a process")
    for field in ("sessions_per_day", "session_minutes"):
        if field not in rule:
            raise RulesError(f"rule {name!r}: {field} is required")
    out["sessions_per_day"] = _check_int(name, rule, "sessions_per_day", 0)
    out["session_minutes"] = _check_int(name, rule, "session_minutes", 1)
    if "daily_minutes" not in rule or rule.get("daily_minutes") is None:
        out["daily_minutes"] = max(1, out["sessions_per_day"] * out["session_minutes"])
    else:
        out["daily_minutes"] = _check_int(name, rule, "daily_minutes", 1)
    if "cooldown_minutes" not in rule or rule.get("cooldown_minutes") is None:
        out["cooldown_minutes"] = 0
    else:
        out["cooldown_minutes"] = _check_int(name, rule, "cooldown_minutes", 0)
    unknown = set(rule) - {"preset", "label", "domains", "processes", *RULE_INT_FIELDS}
    if unknown:
        raise RulesError(f"rule {name!r}: unknown fields {sorted(unknown)}")
    return out


def validate(data: Any) -> dict:
    """Validate a whole rules document; return a normalised deep copy."""
    if not isinstance(data, dict):
        raise RulesError("rules file must be a JSON object")
    version = data.get("version", VERSION)
    if version != VERSION:
        raise RulesError(f"unsupported rules version {version!r}")
    settings = data.get("settings", {})
    if settings is None:
        settings = {}
    if not isinstance(settings, dict):
        raise RulesError("settings must be an object")
    dsa = settings.get("day_starts_at", DEFAULT_DAY_STARTS_AT)
    if isinstance(dsa, bool) or not isinstance(dsa, int) or not 0 <= dsa <= 23:
        raise RulesError("settings.day_starts_at must be an hour 0-23")
    out_settings: dict[str, Any] = {"day_starts_at": dsa}
    user = settings.get("user")
    if user is not None:
        if not isinstance(user, str) or not user:
            raise RulesError("settings.user must be a user name")
        out_settings["user"] = user
    rules = data.get("rules", {})
    if rules is None:
        rules = {}
    if not isinstance(rules, dict):
        raise RulesError("rules must be an object")
    out_rules = {name: validate_rule(name, rule) for name, rule in rules.items()}
    return {"version": VERSION, "settings": out_settings, "rules": out_rules}


def dumps(rules: dict) -> bytes:
    return (json.dumps(rules, indent=2, ensure_ascii=False, sort_keys=False) + "\n").encode()


def loads(data: bytes | str) -> dict:
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError as e:
        raise RulesError(f"rules file is not valid JSON: {e}") from e
    return validate(parsed)


def load(path: Path | None = None) -> dict:
    """Load and validate the rules file; a missing file is an empty rule set."""
    path = path or paths.rules_file()
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return empty()
    return loads(data)


# Loosening ---------------------------------------------------------------


def is_loosening(old: dict, new: dict) -> list[str]:
    """Human-readable reasons why `new` is looser than `old`; empty when it is not."""
    reasons: list[str] = []
    if old.get("version", VERSION) != new.get("version", VERSION):
        reasons.append("rules version changed")
    old_dsa = (old.get("settings") or {}).get("day_starts_at", DEFAULT_DAY_STARTS_AT)
    new_dsa = (new.get("settings") or {}).get("day_starts_at", DEFAULT_DAY_STARTS_AT)
    if old_dsa != new_dsa:
        reasons.append(f"day_starts_at changed from {old_dsa} to {new_dsa}")
    old_rules = old.get("rules") or {}
    new_rules = new.get("rules") or {}
    for name, o in old_rules.items():
        n = new_rules.get(name)
        if n is None:
            reasons.append(f"rule {name!r} removed")
            continue
        for d in o.get("domains", []):
            if d not in n.get("domains", []):
                reasons.append(f"{name}: domain {d} removed")
        for p in o.get("processes", []):
            if p not in n.get("processes", []):
                reasons.append(f"{name}: process {p} removed")
        if o.get("preset") and o.get("preset") != n.get("preset"):
            reasons.append(f"{name}: preset {o['preset']} removed")
        for field in ("sessions_per_day", "session_minutes", "daily_minutes"):
            ov, nv = o.get(field, 0), n.get(field, 0)
            if nv > ov:
                reasons.append(f"{name}: {field} raised from {ov} to {nv}")
        ov, nv = o.get("cooldown_minutes", 0), n.get("cooldown_minutes", 0)
        if nv < ov:
            reasons.append(f"{name}: cooldown_minutes lowered from {ov} to {nv}")
    return reasons


# Expansion ---------------------------------------------------------------


def expand(name: str, rule: dict) -> dict:
    """A rule with its preset merged in: label, glyph, domains, hosts, allow, block, home."""
    p = presets.get(rule.get("preset")) or {}
    extras = [d for d in rule.get("domains", []) if d not in p.get("domains", [])]
    domains = list(p.get("domains", [])) + extras
    hosts = list(p.get("hosts", []))
    for d in extras:
        for h in (d, f"www.{d}"):
            if h not in hosts:
                hosts.append(h)
    return {
        "name": name,
        "label": rule.get("label") or p.get("label") or name,
        "glyph": p.get("glyph", ""),
        "preset": rule.get("preset"),
        "domains": domains,
        "extra_domains": extras,
        "hosts": hosts,
        "allow": list(p.get("allow", [])),
        "block": list(p.get("block", [])),
        "home": p.get("home") or (f"https://{extras[0]}/" if extras else None),
        "processes": list(rule.get("processes", [])),
        "sessions_per_day": rule["sessions_per_day"],
        "session_minutes": rule["session_minutes"],
        "daily_minutes": rule["daily_minutes"],
        "cooldown_minutes": rule["cooldown_minutes"],
    }


# Writing -----------------------------------------------------------------


def write_rules(new: dict, password: str | None = None) -> list[str]:
    """Write the rules file following the contract. Returns the loosening reasons (may be empty).

    Raises PasswordRequired when loosening and no password was given, NoPassword when no key
    exists, WrongPassword when the password does not decrypt the key.
    """
    new = validate(new)
    old = load()
    reasons = is_loosening(old, new)
    data = dumps(new)
    if not reasons:
        paths.atomic_write(paths.rules_file(), data, 0o644)
        try:
            paths.rules_sig().unlink()
        except FileNotFoundError:
            pass
        return reasons
    if not paths.key_pem().exists():
        raise NoPassword()
    if password is None:
        raise PasswordRequired(reasons)
    if not crypto.check_password(password):
        raise WrongPassword()
    sig = crypto.sign(data, password)
    paths.atomic_write(paths.rules_sig(), base64.b64encode(sig) + b"\n", 0o644)
    paths.atomic_write(paths.rules_file(), data, 0o644)
    return reasons
