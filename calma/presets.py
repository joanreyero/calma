"""Built-in presets: the single source of truth for the minimal versions on the policy side."""

from __future__ import annotations

PRESETS: dict[str, dict] = {
    "x": {
        "label": "X",
        "glyph": "𝕏",
        "domains": ["x.com", "twitter.com"],
        # exact host names for /etc/hosts
        "hosts": [
            "x.com",
            "www.x.com",
            "mobile.x.com",
            "twitter.com",
            "www.twitter.com",
            "mobile.twitter.com",
        ],
        # when a session is open: allow everything on these patterns…
        "allow": ["x.com", "twitter.com"],
        # …except these (more specific, so they win)
        "block": [
            "x.com/explore",
            "x.com/i/grok",
            "x.com/i/trending",
            "x.com/i/communities",
            "x.com/i/premium_sign_up",
            "x.com/i/verified-choose",
            "x.com/jobs",
            "twitter.com/explore",
            "twitter.com/i/grok",
            "twitter.com/i/trending",
        ],
        "home": "https://x.com/home",
    },
    "youtube": {
        "label": "YouTube",
        "glyph": "",
        "domains": ["youtube.com", "youtu.be", "youtube-nocookie.com"],
        "hosts": [
            "youtube.com",
            "www.youtube.com",
            "m.youtube.com",
            "music.youtube.com",
            "youtu.be",
            "www.youtube-nocookie.com",
        ],
        "allow": [
            "youtube.com/watch",
            "youtube.com/results",
            "youtube.com/feed/subscriptions",
            "youtube.com/feed/library",
            "youtube.com/feed/history",
            "youtube.com/feed/playlists",
            "youtube.com/feed/you",
            "youtube.com/playlist",
            "youtube.com/@",
            "youtube.com/channel/",
            "youtube.com/c/",
            "youtube.com/user/",
            "youtube.com/embed/",
            "youtube-nocookie.com/embed/",
            "youtu.be",
            "youtube.com/account",
            "youtube.com/paid_memberships",
            "youtube.com/premium",
        ],
        # home, /shorts, /feed/trending, /gaming stay blocked: only the allow list opens paths
        "block": [],
        "home": "https://www.youtube.com/feed/subscriptions",
    },
}


def names() -> list[str]:
    return list(PRESETS)


def get(name: str | None) -> dict | None:
    if not name:
        return None
    return PRESETS.get(name)
