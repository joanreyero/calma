"""rules: validation, normalisation, loosening in both directions for every field, expansion, writing."""

import copy
import json

import pytest

from calma import crypto, paths, rules


def test_validate_example(example_rules):
    v = rules.validate(example_rules)
    assert set(v["rules"]) == {"x", "youtube", "steam"}
    yt = v["rules"]["youtube"]
    assert yt["domains"] == [] and yt["processes"] == [] and yt["cooldown_minutes"] == 0


def test_daily_minutes_derived():
    v = rules.validate({"rules": {"a": {"domains": ["a.com"], "sessions_per_day": 3, "session_minutes": 4}}})
    assert v["rules"]["a"]["daily_minutes"] == 12
    assert v["settings"]["day_starts_at"] == 4


def test_domain_normalisation():
    v = rules.validate({"rules": {"a": {"domains": ["https://WWW.Example.com/path", "example.com"],
                                        "sessions_per_day": 1, "session_minutes": 1}}})
    assert v["rules"]["a"]["domains"] == ["example.com"]


@pytest.mark.parametrize(
    "bad",
    [
        {"rules": {"Bad": {"domains": ["a.com"], "sessions_per_day": 1, "session_minutes": 1}}},
        {"rules": {"a": {"sessions_per_day": 1, "session_minutes": 1}}},  # nothing to block
        {"rules": {"a": {"domains": ["not a domain"], "sessions_per_day": 1, "session_minutes": 1}}},
        {"rules": {"a": {"domains": ["a.com"], "sessions_per_day": -1, "session_minutes": 1}}},
        {"rules": {"a": {"domains": ["a.com"], "sessions_per_day": 1, "session_minutes": 0}}},
        {"rules": {"a": {"domains": ["a.com"], "sessions_per_day": 1, "session_minutes": 1, "daily_minutes": 0}}},
        {"rules": {"a": {"domains": ["a.com"], "sessions_per_day": 1, "session_minutes": 1, "cooldown_minutes": -1}}},
        {"rules": {"a": {"domains": ["a.com"], "sessions_per_day": "1", "session_minutes": 1}}},
        {"rules": {"a": {"domains": ["a.com"], "sessions_per_day": True, "session_minutes": 1}}},
        {"rules": {"a": {"preset": "tiktok", "sessions_per_day": 1, "session_minutes": 1}}},
        {"rules": {"a": {"domains": ["a.com"], "sessions_per_day": 1, "session_minutes": 1, "bogus": 1}}},
        {"version": 2, "rules": {}},
        {"settings": {"day_starts_at": 24}, "rules": {}},
        {"rules": []},
        [],
    ],
)
def test_validate_rejects(bad):
    with pytest.raises(rules.RulesError):
        rules.validate(bad)


def test_loads_rejects_bad_json():
    with pytest.raises(rules.RulesError):
        rules.loads(b"{nope")


def test_load_missing_is_empty():
    assert rules.load() == rules.empty()


# Loosening -----------------------------------------------------------------


def _ex(example_rules):
    return rules.validate(example_rules)


def test_not_loosening_identical(example_rules):
    v = _ex(example_rules)
    assert rules.is_loosening(v, copy.deepcopy(v)) == []


def test_loosening_rule_removed(example_rules):
    old = _ex(example_rules)
    new = copy.deepcopy(old)
    del new["rules"]["steam"]
    assert any("steam" in r and "removed" in r for r in rules.is_loosening(old, new))
    assert rules.is_loosening(new, old) == []  # adding a rule is tightening


def test_loosening_domain_and_process(example_rules):
    old = _ex(example_rules)
    new = copy.deepcopy(old)
    new["rules"]["steam"]["domains"] = []
    new["rules"]["steam"]["processes"] = []
    reasons = rules.is_loosening(old, new)
    assert any("domain store.steampowered.com removed" in r for r in reasons)
    assert any("process steam removed" in r for r in reasons)
    added = copy.deepcopy(old)
    added["rules"]["steam"]["domains"].append("steamcommunity.com")
    added["rules"]["steam"]["processes"].append("steamwebhelper")
    assert rules.is_loosening(old, added) == []


def test_loosening_preset(example_rules):
    old = _ex(example_rules)
    new = copy.deepcopy(old)
    del new["rules"]["x"]["preset"]
    new["rules"]["x"]["domains"] = ["x.com"]
    assert any("preset x removed" in r for r in rules.is_loosening(old, new))
    # adding a preset is tightening
    with_preset = copy.deepcopy(new)
    with_preset["rules"]["x"]["preset"] = "x"
    assert rules.is_loosening(new, with_preset) == []


@pytest.mark.parametrize("field", ["sessions_per_day", "session_minutes", "daily_minutes"])
def test_loosening_budget_fields(example_rules, field):
    old = _ex(example_rules)
    up = copy.deepcopy(old)
    up["rules"]["x"][field] += 1
    assert any(field in r and "raised" in r for r in rules.is_loosening(old, up))
    down = copy.deepcopy(old)
    down["rules"]["x"][field] -= 1
    assert rules.is_loosening(old, down) == []


def test_loosening_cooldown(example_rules):
    old = _ex(example_rules)
    old["rules"]["x"]["cooldown_minutes"] = 10
    down = copy.deepcopy(old)
    down["rules"]["x"]["cooldown_minutes"] = 5
    assert any("cooldown_minutes lowered" in r for r in rules.is_loosening(old, down))
    up = copy.deepcopy(old)
    up["rules"]["x"]["cooldown_minutes"] = 20
    assert rules.is_loosening(old, up) == []


def test_loosening_day_starts_at_and_version(example_rules):
    old = _ex(example_rules)
    new = copy.deepcopy(old)
    new["settings"]["day_starts_at"] = 5
    assert rules.is_loosening(old, new) == ["day_starts_at changed from 4 to 5"]
    new["settings"]["day_starts_at"] = 3
    assert len(rules.is_loosening(old, new)) == 1
    new = copy.deepcopy(old)
    new["version"] = 2
    assert rules.is_loosening(old, new) == ["rules version changed"]


def test_label_change_is_neutral(example_rules):
    old = _ex(example_rules)
    new = copy.deepcopy(old)
    new["rules"]["x"]["label"] = "Twitter"
    assert rules.is_loosening(old, new) == []


# Expansion -------------------------------------------------------------------


def test_expand_preset_plus_extras(example_rules):
    v = _ex(example_rules)
    v["rules"]["x"]["domains"] = ["threads.net"]
    ex = rules.expand("x", v["rules"]["x"])
    assert ex["domains"] == ["x.com", "twitter.com", "threads.net"]
    assert ex["extra_domains"] == ["threads.net"]
    assert "www.threads.net" in ex["hosts"] and "mobile.x.com" in ex["hosts"]
    assert ex["glyph"] == "𝕏" and ex["home"] == "https://x.com/home"
    st = rules.expand("steam", v["rules"]["steam"])
    assert st["label"] == "Steam" and st["allow"] == [] and st["home"] == "https://store.steampowered.com/"
    assert st["hosts"] == ["store.steampowered.com", "www.store.steampowered.com"]


# Writing -------------------------------------------------------------------


def test_write_rules_tightening_no_password(example_rules):
    rules.write_rules(example_rules)
    assert paths.rules_file().exists()
    assert oct(paths.rules_file().stat().st_mode & 0o777) == "0o644"
    tighter = copy.deepcopy(example_rules)
    tighter["rules"]["x"]["sessions_per_day"] = 1
    assert rules.write_rules(tighter) == []
    assert rules.load()["rules"]["x"]["sessions_per_day"] == 1
    assert not paths.rules_sig().exists()


def test_write_rules_loosening_without_key(example_rules):
    rules.write_rules(example_rules)
    looser = copy.deepcopy(example_rules)
    looser["rules"]["x"]["sessions_per_day"] = 3
    with pytest.raises(rules.NoPassword):
        rules.write_rules(looser)
    with pytest.raises(rules.NoPassword):
        rules.write_rules(looser, "whatever")
    assert rules.load()["rules"]["x"]["sessions_per_day"] == 2


def test_write_rules_loosening_with_key(example_rules):
    rules.write_rules(example_rules)
    crypto.set_password("hunter22")
    looser = copy.deepcopy(example_rules)
    looser["rules"]["x"]["sessions_per_day"] = 3
    with pytest.raises(rules.PasswordRequired) as ei:
        rules.write_rules(looser)
    assert ei.value.reasons
    with pytest.raises(rules.WrongPassword):
        rules.write_rules(looser, "nope")
    assert rules.load()["rules"]["x"]["sessions_per_day"] == 2
    reasons = rules.write_rules(looser, "hunter22")
    assert reasons
    data = paths.rules_file().read_bytes()
    import base64

    sig = base64.b64decode(paths.rules_sig().read_bytes())
    assert crypto.verify(data, sig, paths.key_pub())
    assert json.loads(data)["rules"]["x"]["sessions_per_day"] == 3
    # a following tightening removes the signature
    rules.write_rules(example_rules)
    assert not paths.rules_sig().exists()
