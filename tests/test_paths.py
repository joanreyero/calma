"""paths: dev mode prefixing and atomic writes."""

from calma import paths


def test_dev_root_prefixes_everything(tmp_path):
    assert paths.rules_file() == tmp_path / "etc/calma/rules.json"
    assert paths.hosts_file() == tmp_path / "etc/hosts"
    assert paths.policy_file() == tmp_path / "etc/chromium/policies/managed/calma.json"
    assert paths.state_file() == tmp_path / "var/lib/calma/state.json"
    assert paths.dev_mode()
    assert not paths.kill_enabled()


def test_port(monkeypatch):
    monkeypatch.setenv("CALMA_PORT", "7399")
    assert paths.port() == 7399
    monkeypatch.setenv("CALMA_PORT", "nope")
    assert paths.port() == 7390


def test_write_if_changed(tmp_path):
    p = tmp_path / "f"
    assert paths.write_if_changed(p, "a")
    assert not paths.write_if_changed(p, "a")
    assert paths.write_if_changed(p, "b")
    assert p.read_text() == "b"
    assert oct(p.stat().st_mode & 0o777) == "0o644"
    assert not list(tmp_path.glob(".f.tmp*"))


def test_extension_dir_in_dev_mode_is_the_checkout(tmp_path, monkeypatch):
    # a source checkout: the extension Chromium can actually load
    ext = paths.extension_dir()
    assert (ext / "manifest.json").is_file() and ext.name == "extension"
    assert paths.extension_flag() == f"--load-extension={ext}"
    assert paths.share_dir() == tmp_path / "usr/share/calma"
    # without a checkout the prefixed install path is used
    monkeypatch.setattr(paths, "source_extension_dir", lambda: None)
    assert paths.extension_dir() == tmp_path / "usr/share/calma/extension"


def test_atomic_write_mode_and_parent(tmp_path):
    p = tmp_path / "deep" / "er" / "f"
    paths.atomic_write(p, b"x", 0o600)
    assert p.read_bytes() == b"x" and oct(p.stat().st_mode & 0o777) == "0o600"
