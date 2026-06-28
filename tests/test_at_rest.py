# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the at-rest encryption envelope

from __future__ import annotations

import os
from pathlib import Path

import pytest
from cryptography.exceptions import InvalidTag

from synapse_channel.core.at_rest import (
    ENVELOPE_MAGIC,
    KEY_BYTES,
    AtRestCipher,
    check_key_file,
    decrypt_file,
    derive_key,
    encrypt_file,
    generate_key_file,
    is_envelope,
)


def _cipher() -> AtRestCipher:
    return AtRestCipher(b"k" * KEY_BYTES)


def test_round_trip_recovers_plaintext() -> None:
    cipher = _cipher()
    blob = cipher.encrypt(b"event-log line")
    assert is_envelope(blob)
    assert blob.startswith(ENVELOPE_MAGIC)
    assert cipher.decrypt(blob) == b"event-log line"


def test_each_encryption_uses_a_fresh_nonce() -> None:
    cipher = _cipher()
    assert cipher.encrypt(b"same") != cipher.encrypt(b"same")


def test_wrong_key_fails_authentication() -> None:
    blob = _cipher().encrypt(b"secret")
    other = AtRestCipher(b"x" * KEY_BYTES)
    with pytest.raises(InvalidTag):
        other.decrypt(blob)


def test_tampered_ciphertext_fails_authentication() -> None:
    cipher = _cipher()
    blob = bytearray(cipher.encrypt(b"secret"))
    blob[-1] ^= 0x01
    with pytest.raises(InvalidTag):
        cipher.decrypt(bytes(blob))


def test_non_envelope_and_truncated_blobs_are_rejected() -> None:
    cipher = _cipher()
    with pytest.raises(ValueError, match="not a Synapse at-rest envelope"):
        cipher.decrypt(b"plain text not an envelope at all")
    with pytest.raises(ValueError, match="not a Synapse at-rest envelope"):
        cipher.decrypt(ENVELOPE_MAGIC + b"short")


def test_key_must_be_full_length() -> None:
    with pytest.raises(ValueError, match="must be 32 bytes"):
        AtRestCipher(b"too short")


def test_passphrase_derivation_is_deterministic_and_salt_sensitive() -> None:
    salt = b"s" * 16
    key_a = derive_key("hunter2", salt, n=2**10, r=8, p=1)
    key_b = derive_key("hunter2", salt, n=2**10, r=8, p=1)
    key_c = derive_key("hunter2", b"t" * 16, n=2**10, r=8, p=1)
    assert key_a == key_b
    assert key_a != key_c
    assert len(key_a) == KEY_BYTES


def test_passphrase_cipher_round_trips() -> None:
    salt = b"s" * 16
    cipher = AtRestCipher.from_passphrase("pw", salt, n=2**10)
    twin = AtRestCipher.from_passphrase("pw", salt, n=2**10)
    assert twin.decrypt(cipher.encrypt(b"hi")) == b"hi"


def test_generate_key_file_writes_owner_only_and_refuses_overwrite(tmp_path: Path) -> None:
    key_path = tmp_path / "store.key"
    generate_key_file(key_path)
    assert key_path.stat().st_size == KEY_BYTES
    assert key_path.stat().st_mode & 0o077 == 0
    with pytest.raises(FileExistsError):
        generate_key_file(key_path)


def test_check_key_file_accepts_a_good_key_and_rejects_problems(tmp_path: Path) -> None:
    good = tmp_path / "ok.key"
    generate_key_file(good)
    assert check_key_file(good) == (True, "ok")

    assert check_key_file(tmp_path / "missing.key")[0] is False

    directory = tmp_path / "dir.key"
    directory.mkdir()
    assert "not a regular file" in check_key_file(directory)[1]

    loose = tmp_path / "loose.key"
    loose.write_bytes(b"k" * KEY_BYTES)
    loose.chmod(0o644)
    assert "owner-only" in check_key_file(loose)[1]

    short = tmp_path / "short.key"
    short.write_bytes(b"k" * 8)
    short.chmod(0o600)
    assert "exactly 32 bytes" in check_key_file(short)[1]


def test_from_key_file_round_trips_and_rejects_bad_permissions(tmp_path: Path) -> None:
    key_path = tmp_path / "store.key"
    generate_key_file(key_path)
    cipher = AtRestCipher.from_key_file(key_path)
    assert cipher.decrypt(cipher.encrypt(b"x")) == b"x"

    loose = tmp_path / "loose.key"
    loose.write_bytes(b"k" * KEY_BYTES)
    loose.chmod(0o666)
    with pytest.raises(ValueError, match="owner-only"):
        AtRestCipher.from_key_file(loose)


def test_encrypt_file_is_atomic_and_round_trips(tmp_path: Path) -> None:
    cipher = _cipher()
    target = tmp_path / "nested" / "relay.enc"
    encrypt_file(target, b"durable", cipher)
    assert target.exists()
    assert not (tmp_path / "nested" / "relay.enc.tmp").exists()
    assert is_envelope(target.read_bytes())
    assert decrypt_file(target, cipher) == b"durable"


def test_check_key_file_rejects_a_foreign_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key_path = tmp_path / "store.key"
    generate_key_file(key_path)
    real_euid = os.geteuid()
    monkeypatch.setattr(os, "geteuid", lambda: real_euid + 1)
    ok, reason = check_key_file(key_path)
    assert ok is False
    assert "owned by the current user" in reason
