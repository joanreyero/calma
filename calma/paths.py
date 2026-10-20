"""Every filesystem path Calma uses. CALMA_ROOT (dev mode) prefixes all of them."""

from __future__ import annotations

import os
from pathlib import Path


def root() -> Path | None:
    """The dev-mode root, or None when running for real."""
    value = os.environ.get("CALMA_ROOT")
    return Path(value) if value else None


def dev_mode() -> bool:
    return root() is not None


def _p(absolute: str) -> Path:
    r = root()
    if r is None:
        return Path(absolute)
    return r / absolute.lstrip("/")


def port() -> int:
    try:
        return int(os.environ.get("CALMA_PORT", "7390"))
    except ValueError:
        return 7390


def kill_enabled() -> bool:
    """In dev mode processes are only reported unless CALMA_KILL=1."""
    return not dev_mode() or os.environ.get("CALMA_KILL") == "1"


# Configuration
def etc_dir() -> Path:
    return _p("/etc/calma")


def rules_file() -> Path:
    return etc_dir() / "rules.json"


def rules_sig() -> Path:
    return etc_dir() / "rules.sig"


def key_pem() -> Path:
    return etc_dir() / "key.pem"


def key_pub() -> Path:
    return etc_dir() / "key.pub"


def key_pub_sig() -> Path:
    return etc_dir() / "key.pub.sig"


# State
def state_dir() -> Path:
    return _p("/var/lib/calma")


def state_file() -> Path:
    return state_dir() / "state.json"


def accepted_dir() -> Path:
    return state_dir() / "accepted"


def accepted_rules() -> Path:
    return accepted_dir() / "rules.json"


def accepted_key_pub() -> Path:
    return accepted_dir() / "key.pub"


def run_dir() -> Path:
    return _p("/run/calma")


def pid_file() -> Path:
    return run_dir() / "calmad.pid"


# Enforcement
def policy_dir() -> Path:
    return _p("/etc/chromium/policies/managed")


def policy_file() -> Path:
    return policy_dir() / "calma.json"


def hosts_file() -> Path:
    return _p("/etc/hosts")


def chromium_flags_file() -> Path:
    return _p("/etc/chromium-flags.conf")


def share_dir() -> Path:
    """/usr/share/calma: what install.sh creates and uninstall removes (never the source checkout)."""
    return _p("/usr/share/calma")


def source_extension_dir() -> Path | None:
    """The `extension/` directory of a source checkout (sibling of the package), when running from one."""
    candidate = Path(__file__).resolve().parent.parent / "extension"
    return candidate if (candidate / "manifest.json").is_file() else None


def extension_dir() -> Path:
    """Where the unpacked extension lives.

    Installed: /usr/share/calma/extension. Dev mode: the checkout's own `extension/` directory when there
    is one (the only thing a Chromium started by hand could load), else the prefixed path.
    """
    if dev_mode():
        src = source_extension_dir()
        if src is not None:
            return src
    return _p("/usr/share/calma/extension")


def extension_flag() -> str:
    return f"--load-extension={extension_dir()}"


def homes_dir() -> Path:
    return _p("/home")


def user_flags_files() -> list[Path]:
    """Every `/home/*/.config/chromium-flags.conf` that exists."""
    homes = homes_dir()
    if not homes.is_dir():
        return []
    out = []
    for home in sorted(homes.iterdir()):
        f = home / ".config" / "chromium-flags.conf"
        if f.exists():
            out.append(f)
    return out


def systemd_dir() -> Path:
    return _p("/etc/systemd/system")


def lib_dir() -> Path:
    return _p("/usr/lib/calma")


def cli_bin() -> Path:
    return _p("/usr/bin/calma")


def ensure_dirs() -> None:
    for d in (etc_dir(), state_dir(), accepted_dir(), policy_dir()):
        d.mkdir(parents=True, exist_ok=True)
    try:
        run_dir().mkdir(parents=True, exist_ok=True)
    except OSError:
        pass


def atomic_write(path: Path, data: bytes | str, mode: int = 0o644) -> None:
    """Write via temp file + rename so readers never see a partial file."""
    if isinstance(data, str):
        data = data.encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp{os.getpid()}")
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def write_if_changed(path: Path, data: bytes | str, mode: int = 0o644) -> bool:
    """Atomic write only when content differs. Returns True when written."""
    if isinstance(data, str):
        data = data.encode()
    try:
        if path.read_bytes() == data:
            return False
    except OSError:
        pass
    atomic_write(path, data, mode)
    return True
