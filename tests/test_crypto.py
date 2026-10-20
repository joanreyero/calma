"""crypto: real openssl keys; sign, verify, password checks, rotation."""

import base64

import pytest

from calma import crypto, paths


def test_generate_sign_verify():
    pem, pub = crypto.generate_key("pw")
    assert b"ENCRYPTED PRIVATE KEY" in pem and b"PUBLIC KEY" in pub
    paths.key_pem().write_bytes(pem)
    paths.key_pub().write_bytes(pub)
    sig = crypto.sign(b"hello", "pw")
    assert len(sig) == 64
    assert crypto.verify(b"hello", sig, pub)
    assert crypto.verify(b"hello", sig, paths.key_pub())
    assert not crypto.verify(b"hellp", sig, pub)
    assert not crypto.verify(b"hello", sig[:-1] + bytes([sig[-1] ^ 1]), pub)
    assert not crypto.verify(b"hello", b"", pub)
    other_pem, other_pub = crypto.generate_key("x")
    assert not crypto.verify(b"hello", sig, other_pub)


def test_check_password_and_wrong_password():
    assert not crypto.password_set()
    assert not crypto.check_password("pw")  # no key yet
    crypto.set_password("pw")
    assert crypto.password_set()
    assert crypto.check_password("pw")
    assert not crypto.check_password("PW")
    assert not crypto.check_password("")
    with pytest.raises(crypto.CryptoError):
        crypto.sign(b"d", "wrong")
    assert oct(paths.key_pem().stat().st_mode & 0o777) == "0o600"


def test_rotate_password_signs_new_pub():
    crypto.set_password("one")
    old_pub = paths.key_pub().read_bytes()
    with pytest.raises(crypto.CryptoError):
        crypto.set_password("two", "bad")
    with pytest.raises(crypto.CryptoError):
        crypto.set_password("two")
    crypto.set_password("two", "one")
    new_pub = paths.key_pub().read_bytes()
    assert new_pub != old_pub
    sig = base64.b64decode(paths.key_pub_sig().read_bytes())
    assert crypto.verify(new_pub, sig, old_pub)
    assert crypto.check_password("two") and not crypto.check_password("one")


def test_password_never_on_argv(monkeypatch):
    seen = []
    real = crypto.subprocess.run

    def spy(args, **kw):
        seen.append(list(args))
        return real(args, **kw)

    monkeypatch.setattr(crypto.subprocess, "run", spy)
    crypto.set_password("s3cret-value")
    assert all("s3cret-value" not in a for args in seen for a in args)
