"""The managed block in /etc/hosts between `# calma:begin` and `# calma:end`."""

from __future__ import annotations

from calma import paths
from calma import rules as rules_mod

BEGIN = "# calma:begin"
END = "# calma:end"


def blocked_hosts(rules: dict, open_rules: set[str] | frozenset[str]) -> list[str]:
    out: list[str] = []
    for name, rule in (rules.get("rules") or {}).items():
        if name in open_rules:
            continue
        for h in rules_mod.expand(name, rule)["hosts"]:
            if h not in out:
                out.append(h)
    return out


def block_text(hosts: list[str]) -> str:
    if not hosts:
        return ""
    lines = [BEGIN, "# managed by calma; edits here are overwritten every second"]
    for h in hosts:
        lines.append(f"0.0.0.0 {h}")
        lines.append(f":: {h}")
    lines.append(END)
    return "\n".join(lines) + "\n"


def strip_block(text: str) -> str:
    """The hosts file without our block (and without the blank line that preceded it)."""
    lines = text.split("\n")
    out: list[str] = []
    inside = False
    for line in lines:
        if line.strip() == BEGIN:
            inside = True
            continue
        if line.strip() == END:
            inside = False
            continue
        if not inside:
            out.append(line)
    text = "\n".join(out)
    if not text.strip():
        return ""
    while text.endswith("\n\n"):
        text = text[:-1]
    if text and not text.endswith("\n"):
        text += "\n"
    return text


def render(current: str, hosts: list[str]) -> str:
    """The hosts file with our block appended after a blank line. A fixed point: render(render(x)) == render(x)."""
    base = strip_block(current)
    block = block_text(hosts)
    if not block:
        return base
    if not base:
        return block
    if not base.endswith("\n"):
        base += "\n"
    return base + "\n" + block


def write_hosts(rules: dict, open_rules: set[str] | frozenset[str]) -> bool:
    """Rewrite the managed block if needed. Returns True when the file was written."""
    path = paths.hosts_file()
    try:
        current = path.read_text()
    except FileNotFoundError:
        current = ""
    new = render(current, blocked_hosts(rules, open_rules))
    if new == current:
        return False
    paths.atomic_write(path, new, 0o644)
    return True


def remove_block() -> bool:
    path = paths.hosts_file()
    try:
        current = path.read_text()
    except FileNotFoundError:
        return False
    new = strip_block(current)
    if new == current:
        return False
    paths.atomic_write(path, new, 0o644)
    return True


def hosts_ok(rules: dict, open_rules: set[str] | frozenset[str]) -> bool:
    try:
        current = paths.hosts_file().read_text()
    except OSError:
        return False
    return render(current, blocked_hosts(rules, open_rules)) == current
