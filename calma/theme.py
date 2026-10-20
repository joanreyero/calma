"""Reads the Omarchy theme colours (and font) of the primary user, with a Tokyo Night fallback."""

from __future__ import annotations

import pwd
import re
import subprocess
import tomllib
from pathlib import Path

from calma import paths

FALLBACK = {
    "mode": "dark",
    "name": "tokyo-night",
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
        "bright_red": "#ff7a93",
        "bright_yellow": "#ff9e64",
        "bright_green": "#b9f27c",
        "bright_cyan": "#0db9d7",
        "bright_blue": "#7da6ff",
        "bright_magenta": "#bb9af7",
    },
    "font": "JetBrainsMono Nerd Font",
}


def primary_home(settings: dict | None = None) -> tuple[str | None, Path | None]:
    """(user, home) of the primary user: settings.user, else the first /home/* with an omarchy state."""
    user = (settings or {}).get("user")
    if user:
        try:
            return user, Path(pwd.getpwnam(user).pw_dir)
        except KeyError:
            pass
        home = paths.homes_dir() / user
        if home.is_dir():
            return user, home
    homes = paths.homes_dir()
    if homes.is_dir():
        for home in sorted(homes.iterdir()):
            if (home / ".local" / "state" / "omarchy").is_dir():
                return home.name, home
    return None, None


def _font_for(home: Path) -> str | None:
    for candidate in (home / ".config" / "foot" / "foot.ini", home / ".config" / "alacritty" / "alacritty.toml"):
        try:
            text = candidate.read_text()
        except OSError:
            continue
        m = re.search(r"^\s*font\s*=\s*\"?([^:\n\"]+)", text, re.M)
        if m:
            return m.group(1).strip()
        m = re.search(r"family\s*=\s*\"([^\"]+)\"", text)
        if m:
            return m.group(1).strip()
    try:
        r = subprocess.run(["fc-match", "monospace", "-f", "%{family}"], capture_output=True, text=True, timeout=2)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.split(",")[0].strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def theme(settings: dict | None = None) -> dict:
    """The theme document served by GET /theme."""
    _user, home = primary_home(settings)
    out = {"mode": FALLBACK["mode"], "name": FALLBACK["name"], "colors": dict(FALLBACK["colors"]), "font": None}
    if home is None:
        out["font"] = FALLBACK["font"]
        return out
    current = home / ".local" / "state" / "omarchy" / "current"
    try:
        data = tomllib.loads((current / "theme" / "colors.toml").read_text())
    except (OSError, tomllib.TOMLDecodeError):
        data = {}
    colors = {k: v for k, v in data.items() if isinstance(v, str) and v.startswith("#")}
    if colors:
        out["colors"] = colors
        out["mode"] = data.get("mode", "dark") if isinstance(data.get("mode"), str) else "dark"
    try:
        name = (current / "theme.name").read_text().strip()
        if name:
            out["name"] = name
    except OSError:
        pass
    out["font"] = _font_for(home) or FALLBACK["font"]
    return out
