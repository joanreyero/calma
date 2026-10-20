"""cli: table rendering, rule editing commands with --password-file, status/open/close against a live daemon."""

import json
import threading

import pytest

from calma import cli, crypto, paths, rules
from calma import daemon as daemon_mod


def run(*argv):
    return cli.main(list(argv))


def test_table_alignment_and_ansi(monkeypatch):
    monkeypatch.setattr(cli, "C", cli.Palette(True))
    out = cli.table(["Rule", "Sessions"], [["x", cli.C.green("1/2")], ["youtube", "10/10"]], right={1})
    lines = out.splitlines()
    assert cli._width(lines[0]) == cli._width(lines[1]) == cli._width(lines[2])
    assert lines[1].endswith("1/2\033[0m") or lines[1].endswith("1/2\033[0m".rstrip())
    assert "\033[32m" in lines[1]


def test_mmss_and_clock():
    assert cli.mmss(272) == "4:32" and cli.mmss(0) == "0:00"
    import datetime as dt

    now = dt.datetime(2026, 10, 20, 12, 0).timestamp()
    assert cli.clock(dt.datetime(2026, 10, 20, 15, 32).timestamp(), now) == "15:32"
    assert cli.clock(dt.datetime(2026, 10, 21, 4, 0).timestamp(), now) == "tomorrow 04:00"
    assert cli.clock(None) == "—"


def test_status_when_daemon_down(capsys):
    assert run("status") == 2
    assert "Calma isn't running" in capsys.readouterr().out
    assert run() == 2
    assert run("open", "x") == 2
    assert run("close") == 2


def test_add_set_remove_with_password_file(tmp_path, capsys):
    assert run("add", "x", "--preset", "x", "--sessions", "2", "--minutes", "5") == 0
    assert "Added X: 2 × 5 min a day" in capsys.readouterr().out
    r = rules.load()["rules"]["x"]
    assert r["daily_minutes"] == 10 and r["preset"] == "x"
    assert run("add", "x", "--preset", "x", "--sessions", "2", "--minutes", "5") == 1
    assert run("add", "steam", "--domain", "Store.SteamPowered.com", "--process", "steam",
               "--sessions", "1", "--minutes", "60", "--cooldown", "30", "--label", "Steam") == 0
    assert rules.load()["rules"]["steam"]["domains"] == ["store.steampowered.com"]
    # tightening needs no password
    assert run("set", "x", "--sessions", "1") == 0
    assert rules.load()["rules"]["x"]["sessions_per_day"] == 1
    assert rules.load()["rules"]["x"]["daily_minutes"] == 5  # re-derived
    # loosening without a key is refused
    assert run("set", "x", "--sessions", "3") == 1
    assert "no password" in capsys.readouterr().err
    assert rules.load()["rules"]["x"]["sessions_per_day"] == 1
    # set a password, loosen with a password file
    newpw = tmp_path / "new.txt"
    newpw.write_text("correct horse\n")
    assert run("password", "--new-password-file", str(newpw)) == 0
    assert crypto.check_password("correct horse")
    wrong = tmp_path / "wrong.txt"
    wrong.write_text("nope\n")
    assert run("set", "x", "--sessions", "3", "--password-file", str(wrong)) == 1
    assert rules.load()["rules"]["x"]["sessions_per_day"] == 1
    assert run("set", "x", "--sessions", "3", "--password-file", str(newpw)) == 0
    assert rules.load()["rules"]["x"]["sessions_per_day"] == 3
    assert paths.rules_sig().exists()
    # members
    assert run("domain", "add", "x", "threads.net") == 0
    assert rules.load()["rules"]["x"]["domains"] == ["threads.net"]
    assert run("domain", "remove", "x", "threads.net") == 1  # no password given, stdin not a tty
    assert run("domain", "remove", "x", "threads.net", "--password-file", str(newpw)) == 0
    assert rules.load()["rules"]["x"]["domains"] == []
    assert run("process", "add", "steam", "steamwebhelper") == 0
    assert run("process", "remove", "steam", "steamwebhelper", "--password-file", str(newpw)) == 0
    # remove a rule
    assert run("remove", "steam") == 1
    assert run("remove", "steam", "--password-file", str(newpw)) == 0
    assert "steam" not in rules.load()["rules"]
    assert run("remove", "steam") == 1
    # change the password (needs the old one)
    other = tmp_path / "other.txt"
    other.write_text("second one")
    assert run("password", "--password-file", str(wrong), "--new-password-file", str(other)) == 1
    assert run("password", "--password-file", str(newpw), "--new-password-file", str(other)) == 0
    assert crypto.check_password("second one") and paths.key_pub_sig().exists()


def test_list_and_presets(capsys):
    assert run("list") == 0
    assert "No rules" in capsys.readouterr().out
    run("add", "yt", "--preset", "youtube", "--sessions", "1", "--minutes", "20")
    assert run("list") == 0
    out = capsys.readouterr().out
    assert "yt" in out and "YouTube" in out and "preset youtube" in out
    assert run("presets") == 0
    out = capsys.readouterr().out
    assert "x.com, twitter.com" in out and "feed/subscriptions" in out


def test_doctor_without_daemon(capsys):
    assert run("doctor") == 1
    out = capsys.readouterr().out
    assert "daemon not running" in out and "problem" in out and "impulse" in out


@pytest.fixture
def live(write_example):
    d = daemon_mod.Daemon()
    d.load_rules()
    d.tick()
    srv = daemon_mod.serve(d)
    stop = threading.Event()

    def loop():
        while not stop.is_set():
            d.tick()
            stop.wait(0.2)

    threading.Thread(target=loop, daemon=True).start()
    yield d
    stop.set()
    srv.shutdown()
    srv.server_close()


def test_status_open_close_live(live, capsys):
    assert run("status") == 0
    out = capsys.readouterr().out
    assert "X" in out and "YouTube" in out and "blocked" in out and "0/2" in out and "now" in out
    assert run("status", "--json") == 0
    st = json.loads(capsys.readouterr().out)
    assert st["rules"]["x"]["state"] == "blocked"
    assert run("open", "x", "3") == 0
    out = capsys.readouterr().out
    assert "X is open for 3 minutes" in out
    assert run("open", "x") == 1
    assert "already open" in capsys.readouterr().out
    assert run("open", "nope") == 1
    assert run("status") == 0
    out = capsys.readouterr().out
    assert "open · " in out and "left" in out and "1/2" in out
    assert run("close", "x") == 0
    assert "blocked again" in capsys.readouterr().out
    assert run("close") == 0
    assert "Nothing was open" in capsys.readouterr().out
    assert run("open", "youtube") == 0
    capsys.readouterr()
    assert run("close") == 0
    assert "Closed youtube" in capsys.readouterr().out
    assert run("doctor") == 0
    out = capsys.readouterr().out
    assert "daemon running" in out and "Every layer" in out


def test_uninstall_dev_mode(live, tmp_path, capsys):
    pw = tmp_path / "pw"
    pw.write_text("abcd")
    assert run("uninstall", "--yes") == 1  # rules exist, no password: can't
    assert run("password", "--new-password-file", str(pw)) == 0
    with pytest.raises(SystemExit):
        run("uninstall", "--yes", "--password-file", str(tmp_path / "missing"))
    assert run("uninstall", "--yes", "--password-file", str(pw)) == 0
    assert not paths.etc_dir().exists() and not paths.policy_file().exists()
    assert "# calma" not in paths.hosts_file().read_text()
    assert "Calma is gone" in capsys.readouterr().out


def test_version_and_help(capsys):
    with pytest.raises(SystemExit) as ei:
        run("--version")
    assert ei.value.code == 0
    assert capsys.readouterr().out.strip() == f"calma {cli.__version__}"
    parser = cli.build_parser()
    commands = set(parser._subparsers._group_actions[0].choices)
    assert commands == {"status", "open", "close", "add", "set", "domain", "process", "remove", "password",
                        "list", "presets", "doctor", "log", "uninstall"}


def test_status_shows_problems_and_password_line(live, capsys, tmp_path):
    assert run("status") == 0
    assert "no password: nothing can ever be loosened" in capsys.readouterr().out
    pw = tmp_path / "pw"
    pw.write_text("abcd")
    assert run("password", "--new-password-file", str(pw)) == 0
    live.tick()
    assert run("status") == 0
    assert "password set" in capsys.readouterr().out
    live._problems = ["Chromium policy file is missing"]
    assert run("status") == 0
    assert "problem: Chromium policy file is missing" in capsys.readouterr().out


@pytest.fixture
def served(write_example):
    """A daemon answering HTTP but without the loop thread, so files stay as the test leaves them."""
    d = daemon_mod.Daemon()
    d.load_rules()
    d.tick()
    srv = daemon_mod.serve(d)
    yield d
    srv.shutdown()
    srv.server_close()


def test_doctor_dedupes_daemon_problems(served, capsys):
    # a problem both sides see (flags) is listed once; one only the daemon sees is prefixed
    paths.chromium_flags_file().write_text("")
    served._check_now(served.status()["now"])
    served._problems.append("extension not seen for 3 min while Chromium is running")
    assert run("doctor") == 1
    out = capsys.readouterr().out
    assert out.count("does not load the extension") == 1
    assert "daemon reports: extension not seen for 3 min" in out
    assert "2 problems" in out


def test_doctor_shows_trust_store_mismatch(served, capsys):
    import copy

    looser = copy.deepcopy(rules.load())
    del looser["rules"]["steam"]
    paths.rules_file().write_bytes(rules.dumps(looser))
    # before the daemon's next tick the file differs from the trust store
    assert run("doctor") == 1
    assert "differs from the trust store" in capsys.readouterr().out
    served.tick()
    assert run("doctor") == 1
    out = capsys.readouterr().out
    assert "1 tamper event" in out and "matches the daemon's trust store" in out


def test_password_refused_when_trust_store_has_key_but_pem_missing(tmp_path, capsys):
    pw = tmp_path / "pw"
    pw.write_text("abcd")
    assert run("password", "--new-password-file", str(pw)) == 0
    paths.accepted_dir().mkdir(parents=True, exist_ok=True)
    paths.accepted_key_pub().write_bytes(paths.key_pub().read_bytes())
    paths.key_pem().unlink()
    assert run("password", "--new-password-file", str(pw)) == 1
    assert "trust store already holds a password key" in capsys.readouterr().err
    assert not paths.key_pem().exists()
    assert run("doctor") == 1
    assert "key.pem is missing" in capsys.readouterr().out


def test_password_too_short_and_cli_set_cooldown(tmp_path, capsys):
    short = tmp_path / "s"
    short.write_text("abc")
    assert run("password", "--new-password-file", str(short)) == 1
    assert "at least 4" in capsys.readouterr().err
    run("add", "x", "--preset", "x", "--sessions", "2", "--minutes", "5")
    assert run("set", "x") == 1  # nothing to change
    assert run("set", "x", "--cooldown", "30") == 0
    assert rules.load()["rules"]["x"]["cooldown_minutes"] == 30
    assert run("set", "x", "--cooldown", "10") == 1  # loosening without a key
    assert run("set", "x", "--label", "Twitter") == 0
    assert rules.load()["rules"]["x"]["label"] == "Twitter"
    assert run("set", "nope", "--cooldown", "1") == 1
    assert run("domain", "add", "nope", "a.com") == 1
    assert run("add", "bad name", "--domain", "a.com", "--sessions", "1", "--minutes", "1") == 1
    assert run("add", "empty", "--sessions", "1", "--minutes", "1") == 1  # nothing to block


def test_open_messages_for_refusals(live, capsys):
    run("add", "always", "--domain", "a.com", "--sessions", "0", "--minutes", "1")
    live.tick()
    assert run("open", "always") == 1
    assert "always blocked" in capsys.readouterr().out
    assert run("open", "youtube") == 0
    capsys.readouterr()
    assert run("close", "youtube") == 0
    capsys.readouterr()
    assert run("open", "youtube") == 1
    out = capsys.readouterr().out
    assert "no sessions left today" in out and "Back at" in out
    assert run("status") == 0
    out = capsys.readouterr().out
    assert "never" in out and "1/1" in out


def test_notify_dev_mode_prints_to_stderr(capsys):
    from calma import notify

    notify.send("X · 1 minute left", "body")
    err = capsys.readouterr().err
    assert "notify: X · 1 minute left — body" in err
