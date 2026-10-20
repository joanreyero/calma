"""state: day reset (incl. clock backwards), session lifecycle, cooldown, budgets, reboot, persistence."""

import datetime as dt
import json

from calma import paths, rules
from calma import state as state_mod

DAY = 4


def _rules(example_rules):
    return rules.validate(example_rules)


def _ts(y, m, d, h, mi=0):
    return dt.datetime(y, m, d, h, mi).timestamp()


def test_day_for_and_next_reset():
    assert state_mod.day_for(_ts(2026, 10, 20, 3, 59), DAY) == "2026-10-19"
    assert state_mod.day_for(_ts(2026, 10, 20, 4, 0), DAY) == "2026-10-20"
    assert state_mod.next_reset(_ts(2026, 10, 20, 3, 0), DAY) == _ts(2026, 10, 20, 4)
    assert state_mod.next_reset(_ts(2026, 10, 20, 12, 0), DAY) == _ts(2026, 10, 21, 4)


def test_day_reset_forward_only(example_rules):
    st = state_mod.State()
    now = _ts(2026, 10, 20, 12)
    assert st.check_day(now, DAY) is False
    assert st.day == "2026-10-20"
    st.usage("x")["sessions_used"] = 2
    st.usage("x")["seconds_used"] = 600
    # same day: nothing
    assert st.check_day(_ts(2026, 10, 21, 3, 59), DAY) is False
    assert st.usage("x")["sessions_used"] == 2
    # clock moved backwards: nothing reset, nothing granted
    assert st.check_day(_ts(2026, 10, 18, 12), DAY) is False
    assert st.day == "2026-10-20" and st.usage("x")["sessions_used"] == 2
    # next Calma day: reset
    assert st.check_day(_ts(2026, 10, 21, 4, 0), DAY) is True
    assert st.day == "2026-10-21"
    assert st.usage("x") == {"sessions_used": 0, "seconds_used": 0, "last_session_end": None}


def test_session_lifecycle(example_rules):
    r = _rules(example_rules)
    st = state_mod.State()
    now, mono = 1_000_000.0, 500.0
    assert st.cannot_open_reason("x", r["rules"]["x"], now) is None
    err, sess = st.open_session("x", r["rules"]["x"], now, mono)
    assert err is None and sess["ends_mono"] == mono + 300 and sess["minutes"] == 5
    assert st.usage("x")["sessions_used"] == 1
    assert st.cannot_open_reason("x", r["rules"]["x"], now) == "already open"
    assert st.open_session("x", r["rules"]["x"], now, mono) == ("already open", None)
    # ticks charge the real elapsed time
    assert st.tick(r, now + 10, mono + 10) == []
    assert st.usage("x")["seconds_used"] == 10
    st.tick(r, now + 12.5, mono + 12.5)
    st.tick(r, now + 15, mono + 15)
    assert st.usage("x")["seconds_used"] == 15
    view = st.session_view("x", now + 15, mono + 15)
    assert view == {"ends_at": now + 300, "seconds_left": 285, "minutes": 5}
    # one minute warning once
    evs = st.tick(r, now + 241, mono + 241)
    assert [e.kind for e in evs] == ["one_minute"] and "1 minute left" in evs[0].message
    assert st.tick(r, now + 242, mono + 242) == []
    # expiry: charged exactly 300 s even when the tick comes late
    evs = st.tick(r, now + 330, mono + 330)
    assert [e.kind for e in evs] == ["ended"]
    assert evs[0].message == "X · session over · 1 session left today"
    assert "x" not in st.sessions
    u = st.usage("x")
    assert u["seconds_used"] == 300 and u["last_session_end"] == now + 330
    # second session is shorter: only 5 min of the 10 left anyway; close early at 60 s
    err, sess = st.open_session("x", r["rules"]["x"], now + 400, mono + 400)
    assert err is None
    st.tick(r, now + 430, mono + 430)
    ev = st.close_session("x", r, now + 460, mono + 460)
    assert ev.kind == "ended" and "0 sessions left today" in ev.message
    assert st.usage("x")["seconds_used"] == 360
    assert st.close_session("x", r, now + 461, mono + 461) is None  # idempotent
    assert st.cannot_open_reason("x", r["rules"]["x"], now + 500) == "no sessions left today"
    assert st.next_opportunity("x", r["rules"]["x"], now + 500, DAY) == state_mod.next_reset(now + 500, DAY)


def test_requested_minutes_and_remaining_budget(example_rules):
    r = _rules(example_rules)
    r["rules"]["x"]["sessions_per_day"] = 5
    st = state_mod.State()
    now, mono = 1_000_000.0, 100.0
    err, sess = st.open_session("x", r["rules"]["x"], now, mono, minutes=2)
    assert err is None and sess["ends_mono"] == mono + 120
    st.close_session("x", r, now + 120, mono + 120)
    # asking for more than session_minutes is capped at session_minutes
    err, sess = st.open_session("x", r["rules"]["x"], now + 200, mono + 200, minutes=30)
    assert sess["ends_mono"] == mono + 500
    st.close_session("x", r, now + 500, mono + 500)
    assert st.usage("x")["seconds_used"] == 420
    # only 180 s of the 600 s daily budget left: the session is clipped
    err, sess = st.open_session("x", r["rules"]["x"], now + 600, mono + 600)
    assert err is None and sess["ends_mono"] == mono + 780 and sess["minutes"] == 3
    st.tick(r, now + 800, mono + 800)
    assert st.usage("x")["seconds_used"] == 600
    assert st.cannot_open_reason("x", r["rules"]["x"], now + 900) == "no time left today"


def test_cooldown(example_rules):
    r = _rules(example_rules)
    r["rules"]["x"]["cooldown_minutes"] = 10
    r["rules"]["x"]["sessions_per_day"] = 5
    st = state_mod.State()
    now, mono = 1_000_000.0, 100.0
    st.open_session("x", r["rules"]["x"], now, mono)
    st.close_session("x", r, now + 30, mono + 30)
    assert st.cannot_open_reason("x", r["rules"]["x"], now + 60) == "cooling down"
    assert st.next_opportunity("x", r["rules"]["x"], now + 60, DAY) == now + 30 + 600
    assert st.cannot_open_reason("x", r["rules"]["x"], now + 630) is None


def test_always_blocked(example_rules):
    r = _rules(example_rules)
    r["rules"]["x"]["sessions_per_day"] = 0
    st = state_mod.State()
    assert st.cannot_open_reason("x", r["rules"]["x"], 1.0) == "always blocked"
    assert st.next_opportunity("x", r["rules"]["x"], 1.0, DAY) is None
    assert st.open_session("x", r["rules"]["x"], 1.0, 1.0) == ("always blocked", None)


def test_reboot_ends_sessions_and_charges_fully(example_rules):
    r = _rules(example_rules)
    st = state_mod.State()
    now, mono = 1_000_000.0, 100.0
    st.check_boot(r, now, "boot-a")
    st.open_session("x", r["rules"]["x"], now, mono)
    st.tick(r, now + 40, mono + 40)
    assert st.usage("x")["seconds_used"] == 40
    st.save(force=True)
    # the machine reboots: boottime restarts, boot id changes
    st2 = state_mod.State.load()
    assert "x" in st2.sessions
    evs = st2.check_boot(r, now + 1000, "boot-b")
    assert [e.kind for e in evs] == ["ended"] and "reboot" in evs[0].message
    assert st2.sessions == {}
    u = st2.usage("x")
    assert u["seconds_used"] == 300 and u["last_session_end"] == now + 1000 and u["sessions_used"] == 1
    assert st2.boot_id == "boot-b"
    # same boot id: nothing happens
    assert st2.check_boot(r, now + 1001, "boot-b") == []


def test_persistence_roundtrip(example_rules):
    r = _rules(example_rules)
    st = state_mod.State()
    now, mono = 1_000_000.0, 100.0
    st.check_day(now, DAY)
    st.check_boot(r, now, "boot-a")
    st.open_session("youtube", r["rules"]["youtube"], now, mono)
    st.record_tamper("test", now)
    st.extension_seen = now
    assert st.save() is True
    assert st.save() is False  # not dirty
    raw = json.loads(paths.state_file().read_text())
    assert raw["day"] == st.day and raw["boot_id"] == "boot-a"
    assert raw["sessions"]["youtube"]["minutes"] == 20
    assert raw["tamper_events"] == [{"at": now, "what": "test"}]
    assert oct(paths.state_file().stat().st_mode & 0o777) == "0o644"
    st2 = state_mod.State.load()
    assert st2.day == st.day and st2.sessions["youtube"]["ends_mono"] == mono + 1200
    assert st2.usage("youtube")["sessions_used"] == 1
    assert st2.tamper_events_today(now, DAY) == [{"at": now, "what": "test"}]
    assert st2.extension_seen == now


def test_load_corrupt_state_is_empty():
    paths.state_file().write_text("{not json")
    st = state_mod.State.load()
    assert st.day == "" and st.rules == {} and st.sessions == {}


def test_prune_removed_rules(example_rules):
    r = _rules(example_rules)
    st = state_mod.State()
    st.usage("gone")
    st.sessions["gone"] = {"started_mono": 0, "ends_mono": 1, "started_wall": 0, "minutes": 1}
    st.prune(r)
    assert "gone" not in st.rules and "gone" not in st.sessions
