"""daemon: acceptance with the trust store, the loop, the HTTP API over a real socket."""

import base64
import copy
import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from calma import crypto, paths, rules
from calma import daemon as daemon_mod
from calma import state as state_mod


def _write(doc):
    paths.rules_file().write_bytes(rules.dumps(rules.validate(doc)))


def _accepted():
    return rules.loads(paths.accepted_rules().read_bytes())


# Acceptance ------------------------------------------------------------------


def test_first_run_accepts_and_copies(write_example):
    d = daemon_mod.Daemon()
    r = d.load_rules()
    assert set(r["rules"]) == {"x", "youtube", "steam"}
    assert _accepted() == r
    assert not paths.accepted_key_pub().exists()


def test_first_run_without_file_creates_empty():
    d = daemon_mod.Daemon()
    assert d.load_rules() == rules.empty()
    assert paths.rules_file().exists() and _accepted() == rules.empty()


def test_tightening_accepted_without_signature(write_example, example_rules):
    d = daemon_mod.Daemon()
    d.load_rules()
    tighter = copy.deepcopy(example_rules)
    tighter["rules"]["x"]["sessions_per_day"] = 1
    _write(tighter)
    r = d.load_rules()
    assert r["rules"]["x"]["sessions_per_day"] == 1 and _accepted() == r
    assert d.state.tamper_events == []


def test_loosening_without_signature_rejected_and_restored(write_example, example_rules):
    d = daemon_mod.Daemon()
    d.load_rules()
    looser = copy.deepcopy(example_rules)
    looser["rules"]["x"]["sessions_per_day"] = 9
    _write(looser)
    r = d.load_rules()
    assert r["rules"]["x"]["sessions_per_day"] == 2
    assert rules.load()["rules"]["x"]["sessions_per_day"] == 2  # file overwritten with accepted copy
    assert len(d.state.tamper_events) == 1 and "loosened" in d.state.tamper_events[0]["what"]
    # deleting the file is also restored
    paths.rules_file().unlink()
    d.load_rules()
    assert rules.load()["rules"]["x"]["sessions_per_day"] == 2
    assert len(d.state.tamper_events) == 2


def test_loosening_with_valid_signature_accepted(write_example, example_rules):
    crypto.set_password("pw")
    d = daemon_mod.Daemon()
    d.load_rules()
    assert paths.accepted_key_pub().read_bytes() == paths.key_pub().read_bytes()
    looser = copy.deepcopy(example_rules)
    looser["rules"]["x"]["sessions_per_day"] = 9
    rules.write_rules(looser, "pw")
    r = d.load_rules()
    assert r["rules"]["x"]["sessions_per_day"] == 9 and _accepted() == r
    assert d.state.tamper_events == []


def test_loosening_signed_by_foreign_key_rejected(write_example, example_rules):
    crypto.set_password("pw")
    d = daemon_mod.Daemon()
    d.load_rules()
    # attacker generates their own key and signs with it
    pem, pub = crypto.generate_key("evil")
    looser = copy.deepcopy(example_rules)
    del looser["rules"]["steam"]
    data = rules.dumps(rules.validate(looser))
    evil_pem = paths.etc_dir() / "evil.pem"
    evil_pem.write_bytes(pem)
    sig = crypto.sign(data, "evil", evil_pem)
    paths.rules_sig().write_bytes(base64.b64encode(sig))
    paths.rules_file().write_bytes(data)
    r = d.load_rules()
    assert "steam" in r["rules"]
    assert len(d.state.tamper_events) == 1
    assert not paths.rules_sig().exists()


def test_key_replacement_needs_old_key(write_example):
    crypto.set_password("one")
    d = daemon_mod.Daemon()
    d.load_rules()
    accepted_pub = paths.accepted_key_pub().read_bytes()
    # legit rotation through crypto.set_password leaves key.pub.sig → accepted
    crypto.set_password("two", "one")
    d.load_rules()
    assert paths.accepted_key_pub().read_bytes() == paths.key_pub().read_bytes() != accepted_pub
    legit_pub = paths.key_pub().read_bytes()
    # attacker drops in a new key pair without the old password
    pem, pub = crypto.generate_key("evil")
    paths.key_pem().write_bytes(pem)
    paths.key_pub().write_bytes(pub)
    paths.key_pub_sig().unlink()
    d.load_rules()
    assert paths.key_pub().read_bytes() == legit_pub
    assert paths.accepted_key_pub().read_bytes() == legit_pub
    assert any("key.pub" in e["what"] for e in d.state.tamper_events)


def test_password_set_after_first_run_is_accepted(write_example):
    d = daemon_mod.Daemon()
    d.load_rules()
    assert not paths.accepted_key_pub().exists()
    crypto.set_password("later")
    d.load_rules()
    assert paths.accepted_key_pub().read_bytes() == paths.key_pub().read_bytes()


# Loop ------------------------------------------------------------------------


def test_tick_renders_and_reloads(write_example, example_rules, caplog):
    d = daemon_mod.Daemon()
    d.load_rules()
    d.tick()
    pol = json.loads(paths.policy_file().read_text())
    assert "x.com" in pol["URLBlocklist"] and pol["URLAllowlist"] == []
    assert "# calma:begin" in paths.hosts_file().read_text()
    assert paths.state_file().exists()
    code, out = d.open("x")
    assert code == 200 and out["rule"]["state"] == "open"
    pol = json.loads(paths.policy_file().read_text())
    assert pol["URLAllowlist"] == ["x.com", "twitter.com"] and "x.com/explore" in pol["URLBlocklist"]
    assert "x.com" not in paths.hosts_file().read_text()
    # rules file changes are picked up on the next tick (mtime/inode)
    tighter = copy.deepcopy(example_rules)
    tighter["rules"]["x"]["session_minutes"] = 1
    time.sleep(0.01)
    _write(tighter)
    d.tick()
    assert d.rules["rules"]["x"]["session_minutes"] == 1
    # a tightened session length applies to the running session
    assert d.state.sessions["x"]["ends_mono"] <= d.state.sessions["x"]["started_mono"] + 60
    code, out = d.close("x")
    assert code == 200 and out["closed"] == ["x"] and out["rule"]["state"] == "blocked"
    assert json.loads(paths.policy_file().read_text())["URLAllowlist"] == []


def test_open_errors(write_example, example_rules):
    d = daemon_mod.Daemon()
    d.load_rules()
    assert d.open("nope")[0] == 404
    assert d.open("x")[0] == 200
    code, out = d.open("x")
    assert code == 409 and out["error"] == "already open" and out["rule"]["state"] == "open"
    d.close("x")
    d.open("x")
    d.close("x")
    code, out = d.open("x")
    assert code == 409 and out["error"] == "no sessions left today"
    assert out["rule"]["next_opportunity"] == state_mod.next_reset(time.time(), 4)
    assert d.close("nope")[0] == 404
    assert d.close(None) == (200, {"ok": True, "closed": []})


def test_status_shape(write_example):
    d = daemon_mod.Daemon()
    d.load_rules()
    d.tick()
    st = d.status()
    for key in ("version", "now", "day", "day_starts_at", "next_reset", "password_set", "healthy", "problems", "rules"):
        assert key in st
    x = st["rules"]["x"]
    assert x["domains"] == ["x.com", "twitter.com"] and x["glyph"] == "𝕏" and x["home"] == "https://x.com/home"
    assert x["state"] == "blocked" and x["session"] is None and x["can_open"] is True
    assert x["cannot_open_reason"] is None and x["next_opportunity"] is None
    assert x["sessions_left"] == 2 and x["seconds_left"] == 600
    assert st["password_set"] is False
    assert st["day"] == state_mod.day_for(time.time(), 4)


def test_problems_reported(write_example):
    d = daemon_mod.Daemon()
    d.load_rules()
    d.tick()
    paths.policy_file().unlink()
    d._last_check = 0
    d._problems = d._self_check(time.time())
    assert "Chromium policy file is missing" in d._problems
    d.state.record_tamper("x")
    d._problems = d._self_check(time.time())
    assert any("tamper" in p for p in d._problems)


# HTTP --------------------------------------------------------------------------


@pytest.fixture
def server(write_example):
    d = daemon_mod.Daemon()
    d.load_rules()
    d.tick()
    srv = daemon_mod.serve(d)
    stop = threading.Event()

    def loop():
        while not stop.is_set():
            d.tick()
            stop.wait(0.2)

    t = threading.Thread(target=loop, daemon=True)
    t.start()
    yield d
    stop.set()
    t.join(2)
    srv.shutdown()
    srv.server_close()


def _req(method, path, body=None, headers=None):
    req = urllib.request.Request(f"http://127.0.0.1:{paths.port()}{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read()), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read()), dict(e.headers)


def test_http_endpoints(server):
    code, st, hdr = _req("GET", "/status")
    assert code == 200 and hdr["Content-Type"] == "application/json" and hdr["Access-Control-Allow-Origin"] == "*"
    assert set(st["rules"]) == {"x", "youtube", "steam"}
    # POST without the header is refused
    code, out, _ = _req("POST", "/open", {"rule": "x"})
    assert code == 403 and out["ok"] is False
    code, out, _ = _req("POST", "/open", {"rule": "x", "minutes": 2}, {"X-Calma": "1"})
    assert code == 200 and out["ok"] and out["rule"]["session"]["minutes"] == 2
    code, out, _ = _req("POST", "/open", {"rule": "x"}, {"X-Calma": "1"})
    assert code == 409 and out["error"] == "already open"
    code, out, _ = _req("POST", "/open", {"rule": "zzz"}, {"X-Calma": "1"})
    assert code == 404 and out["error"] == "unknown rule"
    code, out, _ = _req("POST", "/open", {"rule": "x", "minutes": "a"}, {"X-Calma": "1"})
    assert code == 400
    code, out, _ = _req("POST", "/ping", {"extension": "0.1.0"}, {"X-Calma": "1"})
    assert out == {"ok": True} and server.state.extension_version == "0.1.0"
    code, th, _ = _req("GET", "/theme")
    assert code == 200 and th["colors"]["accent"].startswith("#") and "font" in th and th["mode"] in ("dark", "light")
    code, out, _ = _req("POST", "/close", {"rule": "x"}, {"X-Calma": "1"})
    assert code == 200 and out["ok"] and out["rule"]["state"] == "blocked"
    code, out, _ = _req("POST", "/close", {"rule": "x"}, {"X-Calma": "1"})
    assert code == 200 and out["closed"] == []
    code, out, _ = _req("GET", "/nope")
    assert code == 404


def test_events_long_poll(server):
    _, st, _ = _req("GET", "/status")
    since = st["last_change"]
    results = {}

    def poll():
        results["r"] = _req("GET", f"/events?since={since}")

    t = threading.Thread(target=poll)
    t.start()
    time.sleep(0.3)
    assert "r" not in results  # still waiting
    _req("POST", "/open", {"rule": "youtube"}, {"X-Calma": "1"})
    t.join(5)
    code, out, _ = results["r"]
    assert code == 200 and out["changed"] is True and out["status"]["rules"]["youtube"]["state"] == "open"
    # an old `since` returns immediately
    code, out, _ = _req("GET", "/events?since=0")
    assert out["changed"] is True
    # a fresh `since` with no change times out as not changed
    daemon_mod.LONG_POLL_SECONDS = 0.5
    try:
        code, out, _ = _req("GET", f"/events?since={out['status']['last_change']}")
        assert out == {"changed": False}
    finally:
        daemon_mod.LONG_POLL_SECONDS = 25


# Watchdog ----------------------------------------------------------------------


def test_watchdog_repairs_policy(write_example, capsys):
    d = daemon_mod.Daemon()
    d.load_rules()
    d.tick()
    paths.policy_file().unlink()
    paths.chromium_flags_file().write_text("")
    assert daemon_mod.watchdog() == 0
    out = capsys.readouterr().out
    assert "wrote" in out and "chromium-flags.conf" in out
    assert paths.policy_file().exists()
    assert daemon_mod.watchdog() == 0
    assert "nothing to repair" in capsys.readouterr().out


# Self-check and problems ----------------------------------------------------------


def test_self_check_clean_right_after_first_tick(write_example):
    """Regression: the first render created /etc/hosts and the cached check must not call it stale."""
    d = daemon_mod.Daemon()
    d.load_rules()
    d.tick()
    assert d.status()["problems"] == [] and d.status()["healthy"] is True
    # a session changes the rendered files: the cached check is refreshed at once, not 10 s later
    d.open("x")
    assert d.status()["problems"] == []
    d.close("x")
    assert d.status()["problems"] == []


def test_self_check_refreshes_after_repair(write_example):
    d = daemon_mod.Daemon()
    d.load_rules()
    d.tick(now_wall=1_000_000.0)
    paths.policy_file().unlink()
    paths.hosts_file().write_text("127.0.0.1 localhost\n")
    d._problems = d._self_check(1_000_001.0)
    assert "Chromium policy file is missing" in d._problems
    assert "hosts file is missing the Calma block" in d._problems
    # the next tick repairs both and re-runs the check even though 10 s have not passed
    d.tick(now_wall=1_000_002.0)
    assert d.status()["problems"] == []
    assert paths.hosts_file().read_text().startswith("127.0.0.1 localhost\n\n# calma:begin\n")


def test_self_check_extension_and_key_problems(write_example, monkeypatch):
    d = daemon_mod.Daemon()
    d.load_rules()
    d.tick()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(paths, "source_extension_dir", lambda: None)
        probs = d._self_check(time.time())
    assert any(p.startswith("extension files missing") for p in probs)
    assert not any(p.startswith("extension files") for p in d._self_check(time.time()))
    crypto.set_password("pw")
    d.load_rules()
    paths.key_pem().unlink()
    probs = d._self_check(time.time())
    assert any("key.pem is missing" in p for p in probs)
    assert not any("key.pub differs" in p for p in probs)
    # the daemon restores a replaced key.pub on reload; between tamper and reload the check reports it
    paths.key_pub().write_bytes(crypto.generate_key("evil")[1])
    assert any("key.pub differs" in p for p in crypto.key_problems())
    d.load_rules()
    assert crypto.key_problems() == [
        "key.pem is missing while the trust store holds a key: nothing can be loosened until it is restored"
    ]


def test_extension_stale_problem(write_example, monkeypatch):
    d = daemon_mod.Daemon()
    d.load_rules()
    monkeypatch.setattr(daemon_mod.procs, "chromium_running", lambda: True)
    now = time.time()
    assert "extension not seen while Chromium is running" in d._self_check(now)
    d.ping("0.1.0")
    assert not any("extension not seen" in p for p in d._self_check(now))
    assert "extension not seen for 5 min while Chromium is running" in d._self_check(now + 330)
    monkeypatch.setattr(daemon_mod.procs, "chromium_running", lambda: False)
    assert not any("extension not seen" in p for p in d._self_check(now + 330))


# Time -------------------------------------------------------------------------------


def _ts(y, m, d, h, mi=0):
    import datetime as dt

    return dt.datetime(y, m, d, h, mi).timestamp()


def test_tick_day_reset_forward_only(write_example):
    d = daemon_mod.Daemon()
    d.load_rules()
    t0 = _ts(2026, 10, 20, 12)
    d.tick(now_wall=t0, now_mono=100.0)
    assert d.state.day == "2026-10-20"
    d.open("x")
    d.close("x")
    assert d.state.usage("x")["sessions_used"] == 1
    # clock moved back two days: nothing is reset or granted
    d.tick(now_wall=_ts(2026, 10, 18, 12), now_mono=101.0)
    assert d.state.day == "2026-10-20" and d.state.usage("x")["sessions_used"] == 1
    assert d.status()["rules"]["x"]["sessions_used"] == 1
    # 03:59 next morning is still the same Calma day; 04:00 resets and bumps /events
    d.tick(now_wall=_ts(2026, 10, 21, 3, 59), now_mono=102.0)
    assert d.state.usage("x")["sessions_used"] == 1
    before = d.last_change
    time.sleep(0.01)
    d.tick(now_wall=_ts(2026, 10, 21, 4, 0), now_mono=103.0)
    assert d.state.day == "2026-10-21" and d.state.usage("x")["sessions_used"] == 0
    assert d.last_change > before


def test_session_expiry_in_loop_charges_boottime(write_example):
    d = daemon_mod.Daemon()
    d.load_rules()
    d.tick(now_wall=1_000_000.0, now_mono=100.0)
    code, out = d.open("x", minutes=2)
    assert code == 200
    sess = d.state.sessions["x"]
    start = sess["started_mono"]
    # a suspend: boottime jumps 500 s although only a few ticks happened
    d.tick(now_wall=1_000_500.0, now_mono=start + 500)
    assert "x" not in d.state.sessions
    assert d.state.usage("x")["seconds_used"] == 120  # charged exactly the session, not the gap
    assert json.loads(paths.policy_file().read_text())["URLAllowlist"] == []
    assert "0.0.0.0 x.com" in paths.hosts_file().read_text()


def test_startup_after_reboot_ends_sessions(write_example, monkeypatch):
    d = daemon_mod.Daemon()
    d.load_rules()
    d.state.check_boot(d.rules, time.time())  # as run() does at startup
    d.tick()
    d.open("youtube")
    d.state.save(force=True)
    raw = json.loads(paths.state_file().read_text())
    assert "youtube" in raw["sessions"]
    monkeypatch.setattr(state_mod, "boot_id", lambda: "another-boot")
    d2 = daemon_mod.Daemon()
    d2.load_rules()
    evs = d2.state.check_boot(d2.rules, time.time())
    assert [e.kind for e in evs] == ["ended"]
    d2.tick()
    st = d2.status()["rules"]["youtube"]
    assert st["state"] == "blocked" and st["seconds_used"] == 1200 and st["cannot_open_reason"]
    assert "0.0.0.0 www.youtube.com" in paths.hosts_file().read_text()


def test_watchdog_fails_closed_after_reboot(write_example, capsys, monkeypatch):
    d = daemon_mod.Daemon()
    d.load_rules()
    d.state.check_boot(d.rules, time.time())  # as run() does at startup
    d.tick()
    d.open("x")
    d.state.save(force=True)
    pol = json.loads(paths.policy_file().read_text())
    assert pol["URLAllowlist"] == ["x.com", "twitter.com"]
    # same boot: the watchdog respects the open session
    assert daemon_mod.watchdog() == 0
    assert "nothing to repair" in capsys.readouterr().out
    # another boot: the session is void, the watchdog renders everything blocked and repairs hosts
    monkeypatch.setattr(state_mod, "boot_id", lambda: "another-boot")
    paths.hosts_file().write_text("127.0.0.1 localhost\n")
    assert daemon_mod.watchdog() == 0
    out = capsys.readouterr().out
    assert "refreshed" in out and "hosts" in out
    pol = json.loads(paths.policy_file().read_text())
    assert pol["URLAllowlist"] == [] and "x.com" in pol["URLBlocklist"]
    assert "0.0.0.0 x.com" in paths.hosts_file().read_text()


def test_watchdog_uses_trust_store_not_loosened_file(write_example, example_rules, capsys):
    d = daemon_mod.Daemon()
    d.load_rules()
    d.tick()
    looser = copy.deepcopy(example_rules)
    del looser["rules"]["steam"]
    paths.rules_file().write_bytes(rules.dumps(rules.validate(looser)))
    paths.policy_file().unlink()
    assert daemon_mod.watchdog() == 0
    assert "store.steampowered.com" in json.loads(paths.policy_file().read_text())["URLBlocklist"]


# HTTP edge cases ------------------------------------------------------------------


def test_http_edge_cases(server):
    code, out, hdr = _req("OPTIONS", "/open")
    assert code == 200 and "X-Calma" in hdr["Access-Control-Allow-Headers"]
    req = urllib.request.Request(f"http://127.0.0.1:{paths.port()}/open", method="POST", data=b"{nope")
    req.add_header("X-Calma", "1")
    with pytest.raises(urllib.error.HTTPError) as ei:
        urllib.request.urlopen(req, timeout=5)
    assert ei.value.code == 400
    code, out, _ = _req("POST", "/open", {}, {"X-Calma": "1"})
    assert code == 400 and "rule" in out["error"]
    code, out, _ = _req("POST", "/open", {"rule": "x", "minutes": 0}, {"X-Calma": "1"})
    assert code == 400
    code, out, _ = _req("POST", "/nope", {}, {"X-Calma": "1"})
    assert code == 404
    code, out, _ = _req("GET", "/events?since=abc")
    assert out["changed"] is True  # unparsable since counts as 0
    code, out, _ = _req("GET", "/")
    assert out["ok"] is True and out["version"] == daemon_mod.__version__
    # close without a rule closes every session
    _req("POST", "/open", {"rule": "x"}, {"X-Calma": "1"})
    _req("POST", "/open", {"rule": "youtube"}, {"X-Calma": "1"})
    code, out, _ = _req("POST", "/close", {}, {"X-Calma": "1"})
    assert code == 200 and sorted(out["closed"]) == ["x", "youtube"] and "rule" not in out
    _, st, _ = _req("GET", "/status")
    assert all(r["state"] == "blocked" for r in st["rules"].values())
    assert st["rules"]["x"]["cannot_open_reason"] is None  # one of two sessions used
    assert st["rules"]["youtube"]["cannot_open_reason"] == "no sessions left today"
    assert st["rules"]["youtube"]["next_opportunity"] == st["next_reset"]


def test_theme_endpoint_uses_settings_user(server, tmp_path):
    home = tmp_path / "home" / "ana"
    cur = home / ".local" / "state" / "omarchy" / "current"
    (cur / "theme").mkdir(parents=True)
    (cur / "theme" / "colors.toml").write_text('accent = "#abcdef"\n')
    (cur / "theme.name").write_text("catppuccin\n")
    code, th, _ = _req("GET", "/theme")
    assert th["name"] == "catppuccin" and th["colors"]["accent"] == "#abcdef"


# run(): the real startup path -------------------------------------------------------


def test_run_startup_and_sigterm(write_example, monkeypatch):
    """run() renders before the HTTP server exists and must not trip over its own lock; SIGTERM stops it."""
    import os

    stop = threading.Event()
    result = {}

    def target():
        result["rc"] = daemon_mod.run(stop)

    t = threading.Thread(target=target, daemon=True)
    t.start()
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            code, st, _ = _req("GET", "/status")
            break
        except (urllib.error.URLError, ConnectionError, OSError):
            time.sleep(0.05)
    else:
        raise AssertionError("daemon did not come up")
    assert set(st["rules"]) == {"x", "youtube", "steam"} and st["problems"] == []
    assert paths.pid_file().read_text().strip() == str(os.getpid())
    assert "0.0.0.0 x.com" in paths.hosts_file().read_text()
    code, out, _ = _req("POST", "/open", {"rule": "x"}, {"X-Calma": "1"})
    assert code == 200
    # the loop picks up a tightened rules file written from outside
    tighter = copy.deepcopy(write_example)
    tighter["rules"]["steam"]["sessions_per_day"] = 0
    _write(tighter)
    deadline = time.time() + 3
    while time.time() < deadline:
        _, st, _ = _req("GET", "/status")
        if st["rules"]["steam"]["cannot_open_reason"] == "always blocked":
            break
        time.sleep(0.1)
    assert st["rules"]["steam"]["cannot_open_reason"] == "always blocked"
    stop.set()  # what the SIGTERM handler does
    t.join(5)
    assert result["rc"] == 0 and not paths.pid_file().exists()
    raw = json.loads(paths.state_file().read_text())
    assert "x" in raw["sessions"] and raw["boot_id"] == state_mod.boot_id()
