"""Desktop notifications to the logged-in user, sent from root via notify-send; stderr in dev mode."""

from __future__ import annotations

import os
import pwd
import subprocess
import sys
from pathlib import Path

from calma import paths


def _sessions() -> list[tuple[str, int, str]]:
    """(user, uid, runtime_dir) for every user with a runtime dir and a Wayland or D-Bus socket."""
    out = []
    run_user = Path("/run/user")
    if not run_user.is_dir():
        return out
    for d in sorted(run_user.iterdir()):
        if not d.name.isdigit():
            continue
        uid = int(d.name)
        if uid < 1000:
            continue
        if not (d / "bus").exists():
            continue
        try:
            user = pwd.getpwuid(uid).pw_name
        except KeyError:
            continue
        out.append((user, uid, str(d)))
    return out


def send(title: str, body: str = "", urgency: str = "normal", timeout_ms: int = 8000) -> None:
    """Best-effort desktop notification. Never raises."""
    if paths.dev_mode():
        print(f"notify: {title}" + (f" — {body}" if body else ""), file=sys.stderr, flush=True)
        return
    args = ["notify-send", "-a", "Calma", "-u", urgency, "-t", str(timeout_ms), title]
    if body:
        args.append(body)
    try:
        if os.geteuid() != 0:
            subprocess.run(args, capture_output=True, timeout=5, check=False)
            return
        for user, _uid, runtime in _sessions():
            env = {
                "XDG_RUNTIME_DIR": runtime,
                "DBUS_SESSION_BUS_ADDRESS": f"unix:path={runtime}/bus",
                "PATH": "/usr/local/bin:/usr/bin:/bin",
                "HOME": pwd.getpwnam(user).pw_dir,
            }
            subprocess.run(
                ["runuser", "-u", user, "--", *args],
                env=env,
                capture_output=True,
                timeout=5,
                check=False,
            )
    except (OSError, subprocess.SubprocessError):
        pass
