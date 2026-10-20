"""Policy generation, hosts block, flags repair and process scanning."""

import json
from pathlib import Path

from calma import paths, rules
from calma.enforce import chromium, hosts, procs


def _v(example_rules):
    return rules.validate(example_rules)


def test_policy_all_blocked(example_rules):
    pol = chromium.policy(_v(example_rules), frozenset())
    assert pol["URLBlocklist"] == [
        "x.com", "twitter.com", "youtube.com", "youtu.be", "youtube-nocookie.com", "store.steampowered.com",
    ]
    assert pol["URLAllowlist"] == []


def test_policy_x_open_youtube_blocked(example_rules):
    pol = chromium.policy(_v(example_rules), frozenset({"x"}))
    assert pol["URLBlocklist"][:3] == ["youtube.com", "youtu.be", "youtube-nocookie.com"]
    assert "x.com/explore" in pol["URLBlocklist"] and "x.com/i/grok" in pol["URLBlocklist"]
    assert "x.com" not in pol["URLBlocklist"]
    assert pol["URLAllowlist"] == ["x.com", "twitter.com"]


def test_policy_preset_open_with_extra_domains(example_rules):
    v = _v(example_rules)
    v["rules"]["youtube"]["domains"] = ["invidious.io"]
    pol = chromium.policy(v, frozenset({"youtube"}))
    assert "youtube.com/watch" in pol["URLAllowlist"]
    assert "invidious.io" in pol["URLAllowlist"]
    assert "youtube.com" not in pol["URLBlocklist"]
    closed = chromium.policy(v, frozenset())
    assert "invidious.io" in closed["URLBlocklist"]


def test_policy_non_preset_open(example_rules):
    pol = chromium.policy(_v(example_rules), frozenset({"steam"}))
    assert pol["URLAllowlist"] == ["store.steampowered.com"]
    assert "store.steampowered.com" not in pol["URLBlocklist"]


def test_policy_empty_rules_writes_both_keys():
    assert chromium.policy(rules.empty(), frozenset()) == {"URLBlocklist": [], "URLAllowlist": []}


def test_write_policy_only_on_change(example_rules):
    v = _v(example_rules)
    assert chromium.write_policy(v, frozenset())
    mtime = paths.policy_file().stat().st_mtime_ns
    assert not chromium.write_policy(v, frozenset())
    assert paths.policy_file().stat().st_mtime_ns == mtime
    assert chromium.write_policy(v, frozenset({"x"}))
    assert json.loads(paths.policy_file().read_text())["URLAllowlist"] == ["x.com", "twitter.com"]
    assert chromium.policy_ok(v, frozenset({"x"}))


# hosts -----------------------------------------------------------------------


def test_hosts_block_idempotent(example_rules):
    v = _v(example_rules)
    paths.hosts_file().write_text("127.0.0.1 localhost\n::1 localhost\n")
    assert hosts.write_hosts(v, frozenset())
    text = paths.hosts_file().read_text()
    assert text.startswith("127.0.0.1 localhost\n::1 localhost\n\n# calma:begin\n")
    assert text.count("# calma:begin") == 1 and text.endswith("# calma:end\n")
    assert "0.0.0.0 www.x.com\n" in text and ":: music.youtube.com\n" in text
    assert "0.0.0.0 store.steampowered.com\n" in text
    assert not hosts.write_hosts(v, frozenset())
    assert paths.hosts_file().read_text() == text
    # open x → x hosts leave, block still single
    assert hosts.write_hosts(v, frozenset({"x"}))
    t2 = paths.hosts_file().read_text()
    assert "x.com" not in t2 and t2.count("# calma:begin") == 1
    assert "0.0.0.0 www.youtube.com\n" in t2
    assert hosts.hosts_ok(v, frozenset({"x"}))
    # remove everything restores the original file
    assert hosts.write_hosts(rules.empty(), frozenset())
    assert paths.hosts_file().read_text() == "127.0.0.1 localhost\n::1 localhost\n"
    assert hosts.remove_block() is False


def test_hosts_missing_file(example_rules):
    v = _v(example_rules)
    assert hosts.write_hosts(v, frozenset())
    text = paths.hosts_file().read_text()
    assert text.startswith("# calma:begin\n")
    # the rendered file is a fixed point: nothing to do on the next tick, and the self-check agrees
    assert not hosts.write_hosts(v, frozenset())
    assert hosts.hosts_ok(v, frozenset())
    assert hosts.render(text, hosts.blocked_hosts(v, frozenset())) == text


def test_hosts_render_fixed_point_from_blank_file(example_rules):
    v = _v(example_rules)
    paths.hosts_file().write_text("\n")
    assert hosts.write_hosts(v, frozenset())
    assert hosts.hosts_ok(v, frozenset())
    assert not hosts.write_hosts(v, frozenset())
    assert hosts.remove_block()
    assert paths.hosts_file().read_text() == ""


def test_hosts_strip_block_without_trailing_newline():
    paths.hosts_file().write_text("127.0.0.1 localhost")
    hosts.write_hosts(_v({"rules": {"a": {"domains": ["a.com"], "sessions_per_day": 1, "session_minutes": 1}}}),
                      frozenset())
    assert paths.hosts_file().read_text() == (
        "127.0.0.1 localhost\n\n# calma:begin\n# managed by calma; edits here are overwritten every second\n"
        "0.0.0.0 a.com\n:: a.com\n0.0.0.0 www.a.com\n:: www.a.com\n# calma:end\n"
    )


# flags -----------------------------------------------------------------------


def test_repair_flags_system_and_user(tmp_path):
    ext = str(paths.extension_dir())
    sysf = paths.chromium_flags_file()
    assert chromium.repair_flags() == [sysf]
    assert sysf.read_text() == f"--load-extension={ext}\n"
    assert chromium.repair_flags() == []
    sysf.write_text("--ozone-platform=wayland\n--load-extension=/opt/other\n")
    chromium.repair_flags()
    assert sysf.read_text() == f"--ozone-platform=wayland\n--load-extension=/opt/other,{ext}\n"
    # user file without a load-extension line is left alone
    user = tmp_path / "home" / "joan" / ".config" / "chromium-flags.conf"
    user.parent.mkdir(parents=True)
    user.write_text("--enable-features=Foo\n")
    assert chromium.repair_flags() == []
    assert user.read_text() == "--enable-features=Foo\n"
    # user file with a load-extension line gets ours appended
    user.write_text("--load-extension=/home/joan/ext\n")
    assert chromium.flags_problems() == [f"{user} has a --load-extension line without the Calma extension"]
    assert chromium.repair_flags() == [user]
    assert user.read_text() == f"--load-extension=/home/joan/ext,{ext}\n"
    assert chromium.flags_problems() == []


def test_repair_flags_follows_symlink(tmp_path):
    ext = str(paths.extension_dir())
    target = tmp_path / "dotfiles-flags.conf"
    target.write_text("--load-extension=/x\n")
    user = tmp_path / "home" / "joan" / ".config" / "chromium-flags.conf"
    user.parent.mkdir(parents=True)
    user.symlink_to(target)
    chromium.repair_flags()
    assert user.is_symlink()
    assert target.read_text() == f"--load-extension=/x,{ext}\n"


def test_flags_problems_missing_file():
    assert chromium.flags_problems() == [f"{paths.chromium_flags_file()} is missing"]


# procs -----------------------------------------------------------------------


def test_procs_find_self_excluded_and_dev_mode_no_kill(example_rules):
    import os

    comm = Path(f"/proc/{os.getpid()}/comm").read_text().strip()
    assert all(pid != os.getpid() for pid, _ in procs.find({comm}))
    v = _v({"rules": {"py": {"processes": [comm], "sessions_per_day": 1, "session_minutes": 1}}})
    # in dev mode without CALMA_KILL nothing dies; the parent pytest process (if any) may be listed
    victims = procs.kill_blocked(v, frozenset())
    assert all(isinstance(p, int) for p, _ in victims)
    assert procs.kill_blocked(v, frozenset({"py"})) == []
