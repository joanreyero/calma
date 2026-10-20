"""theme: reads the primary user's Omarchy theme, falls back to Tokyo Night."""

from calma import theme


def test_fallback_without_homes():
    t = theme.theme({})
    assert t["name"] == "tokyo-night" and t["colors"]["accent"] == "#7aa2f7" and t["mode"] == "dark"
    assert t["font"] == "JetBrainsMono Nerd Font"


def test_reads_user_theme(tmp_path):
    home = tmp_path / "home" / "ana"
    cur = home / ".local" / "state" / "omarchy" / "current"
    (cur / "theme").mkdir(parents=True)
    (cur / "theme" / "colors.toml").write_text('mode = "light"\naccent = "#123456"\nbackground = "#ffffff"\n')
    (cur / "theme.name").write_text("rose-pine\n")
    (home / ".config" / "foot").mkdir(parents=True)
    (home / ".config" / "foot" / "foot.ini").write_text("[main]\nfont=CaskaydiaMono Nerd Font:size=9\n")
    t = theme.theme({})
    assert t == {
        "mode": "light",
        "name": "rose-pine",
        "colors": {"accent": "#123456", "background": "#ffffff"},
        "font": "CaskaydiaMono Nerd Font",
    }
    assert theme.primary_home({})[0] == "ana"
    # settings.user wins when it exists under /home
    (tmp_path / "home" / "bob").mkdir()
    assert theme.primary_home({"user": "bob"})[0] == "bob"
