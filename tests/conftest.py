"""Shared fixtures: every test runs in dev mode with CALMA_ROOT pointing at a temp dir."""

from __future__ import annotations

import json
import socket

import pytest

from calma import paths

EXAMPLE_RULES = {
    "version": 1,
    "settings": {"day_starts_at": 4},
    "rules": {
        "x": {
            "preset": "x",
            "label": "X",
            "domains": [],
            "processes": [],
            "sessions_per_day": 2,
            "session_minutes": 5,
            "daily_minutes": 10,
            "cooldown_minutes": 0,
        },
        "youtube": {"preset": "youtube", "sessions_per_day": 1, "session_minutes": 20, "daily_minutes": 20,
                    "cooldown_minutes": 0},
        "steam": {
            "label": "Steam",
            "domains": ["store.steampowered.com"],
            "processes": ["steam"],
            "sessions_per_day": 1,
            "session_minutes": 60,
            "daily_minutes": 60,
            "cooldown_minutes": 0,
        },
    },
}


@pytest.fixture(autouse=True)
def calma_root(tmp_path, monkeypatch):
    monkeypatch.setenv("CALMA_ROOT", str(tmp_path))
    monkeypatch.delenv("CALMA_KILL", raising=False)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        monkeypatch.setenv("CALMA_PORT", str(sock.getsockname()[1]))
    paths.ensure_dirs()
    return tmp_path


@pytest.fixture
def example_rules():
    return json.loads(json.dumps(EXAMPLE_RULES))


@pytest.fixture
def write_example(example_rules):
    paths.rules_file().write_text(json.dumps(example_rules))
    return example_rules
