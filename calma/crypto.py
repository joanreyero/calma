"""Password-protected Ed25519 key via the `openssl` CLI: generate, sign, verify, check."""

from __future__ import annotations

import base64
import os
import subprocess
import tempfile
from pathlib import Path

from calma import paths


class CryptoError(Exception):
    """openssl failed in a way that is not just a wrong password."""


def _run(args: list[str], password: str | None = None, cwd: str | None = None) -> subprocess.CompletedProcess:
    """Run openssl; the password travels over a pipe (`fd:N`), never on argv."""
    pass_fds: tuple[int, ...] = ()
    read_fd = None
    if password is not None:
        read_fd, write_fd = os.pipe()
        os.write(write_fd, password.encode() + b"\n")
        os.close(write_fd)
        pass_fds = (read_fd,)
        args = [a.replace("{FD}", str(read_fd)) for a in args]
    try:
        return subprocess.run(
            ["openssl", *args],
            capture_output=True,
            pass_fds=pass_fds,
            cwd=cwd,
            check=False,
        )
    finally:
        if read_fd is not None:
            os.close(read_fd)


def generate_key(password: str) -> tuple[bytes, bytes]:
    """Return (encrypted private key PEM, public key PEM) for a fresh Ed25519 key."""
    if not password:
        raise CryptoError("empty password")
    with tempfile.TemporaryDirectory(prefix="calma-key-") as tmp:
        raw = Path(tmp) / "raw.pem"
        enc = Path(tmp) / "key.pem"
        pub = Path(tmp) / "key.pub"
        r = _run(["genpkey", "-algorithm", "ed25519", "-out", str(raw)])
        if r.returncode != 0:
            raise CryptoError(r.stderr.decode(errors="replace").strip())
        r = _run(
            ["pkey", "-in", str(raw), "-aes-256-cbc", "-passout", "fd:{FD}", "-out", str(enc)],
            password,
        )
        if r.returncode != 0:
            raise CryptoError(r.stderr.decode(errors="replace").strip())
        r = _run(["pkey", "-in", str(raw), "-pubout", "-out", str(pub)])
        if r.returncode != 0:
            raise CryptoError(r.stderr.decode(errors="replace").strip())
        pem_bytes, pub_bytes = enc.read_bytes(), pub.read_bytes()
        raw.write_bytes(b"\0" * len(raw.read_bytes()))
    return pem_bytes, pub_bytes


def sign(data: bytes, password: str, key_pem: Path | None = None) -> bytes:
    """Raw Ed25519 signature of `data` by the (encrypted) private key."""
    key_pem = key_pem or paths.key_pem()
    with tempfile.TemporaryDirectory(prefix="calma-sign-") as tmp:
        din = Path(tmp) / "data"
        sig = Path(tmp) / "sig"
        din.write_bytes(data)
        r = _run(
            [
                "pkeyutl", "-sign", "-inkey", str(key_pem), "-passin", "fd:{FD}",
                "-rawin", "-in", str(din), "-out", str(sig),
            ],
            password,
        )
        if r.returncode != 0:
            raise CryptoError(r.stderr.decode(errors="replace").strip() or "signing failed")
        return sig.read_bytes()


def verify(data: bytes, sig: bytes, pub: bytes | Path | None = None) -> bool:
    """True when `sig` is a valid signature of `data` by the public key (bytes or path)."""
    with tempfile.TemporaryDirectory(prefix="calma-verify-") as tmp:
        din = Path(tmp) / "data"
        sfile = Path(tmp) / "sig"
        din.write_bytes(data)
        sfile.write_bytes(sig)
        if pub is None:
            pub = paths.key_pub()
        if isinstance(pub, Path):
            pub_path = pub
        else:
            pub_path = Path(tmp) / "key.pub"
            pub_path.write_bytes(pub)
        if not pub_path.exists() or not sig:
            return False
        r = _run(
            [
                "pkeyutl", "-verify", "-pubin", "-inkey", str(pub_path),
                "-rawin", "-in", str(din), "-sigfile", str(sfile),
            ]
        )
        return r.returncode == 0


def check_password(password: str, key_pem: Path | None = None) -> bool:
    """Can the private key be decrypted with this password?"""
    key_pem = key_pem or paths.key_pem()
    if not key_pem.exists() or not password:
        return False
    r = _run(["pkey", "-in", str(key_pem), "-passin", "fd:{FD}", "-noout"], password)
    return r.returncode == 0


def password_set() -> bool:
    return paths.key_pem().exists()


def key_problems() -> list[str]:
    """Inconsistencies between the key files and the daemon's trust store (for doctor and /status)."""
    out: list[str] = []
    acc = paths.accepted_key_pub()
    if not acc.exists():
        return out
    try:
        accepted = acc.read_bytes()
        current = paths.key_pub().read_bytes() if paths.key_pub().exists() else None
    except OSError:
        return out
    if current != accepted:
        out.append("key.pub differs from the trust store; the daemon restores the accepted key")
    if not paths.key_pem().exists():
        out.append("key.pem is missing while the trust store holds a key: nothing can be loosened until it is restored")
    return out


def set_password(new_password: str, old_password: str | None = None) -> None:
    """Create the key (first time) or rotate it, leaving `key.pub.sig` signed by the old key."""
    if not new_password:
        raise CryptoError("empty password")
    old_exists = paths.key_pem().exists()
    if old_exists:
        if old_password is None or not check_password(old_password):
            raise CryptoError("wrong password")
    pem, pub = generate_key(new_password)
    if old_exists:
        assert old_password is not None
        sig = sign(pub, old_password)
        paths.atomic_write(paths.key_pub_sig(), base64.b64encode(sig) + b"\n", 0o644)
    paths.atomic_write(paths.key_pem(), pem, 0o600)
    paths.atomic_write(paths.key_pub(), pub, 0o644)
