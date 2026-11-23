# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — byte-identical format and key-lifecycle characterisation tests

from __future__ import annotations

import base64
import json
import os
import re
import secrets
import stat
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from cryptography.exceptions import InvalidTag

from synapse_channel.core import at_rest
from synapse_channel.core.at_rest import (
    AtRestCipher,
    AtRestKeyExhausted,
    AtRestSurface,
    backup_profile,
    check_key_file,
    decrypt_file,
    derive_key,
    generate_key_file,
    generate_key_file_from_passphrase,
    generate_wrapped_key_file,
    load_key_file,
    restore_profile_backup,
    rewrap_wrapped_key_file,
    unwrap_data_key,
    wrap_data_key,
)
from synapse_channel.core.at_rest_format import (
    BACKUP_MANIFEST_SCHEMA,
    DEFAULT_SCRYPT_N,
    DEFAULT_SCRYPT_P,
    DEFAULT_SCRYPT_R,
    ENVELOPE_MAGIC,
    GCM_MESSAGE_LIMIT,
    GCM_REKEY_WARNING_THRESHOLD,
    KEY_BYTES,
    NONCE_BYTES,
    PASSPHRASE_SCRYPT_BACKEND,
    SCRYPT_SALT_BYTES,
    WRAPPED_KEY_SCHEMA,
)
from synapse_channel.core.secure_path import assert_owner_only_file_path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Pinned deterministic vectors for the v1 at-rest envelope and wrapped-key formats.
FIXED_RAW_KEY = bytes(range(0x10, 0x30))
FIXED_SALT_1 = bytes(range(0xA0, 0xB0))
FIXED_SALT_2 = bytes(range(0xB0, 0xC0))
FIXED_DATA_KEY = bytes(range(0x30, 0x50))
FIXED_NONCE = bytes(range(0x01, 0x0D))
FIXED_PLAINTEXT = b"synapse at-rest byte-identical vector\n"

PINNED_DEFAULT_DERIVED_KEY = bytes.fromhex(
    "5fc6803c06904c497837cca9ed73c2b8aefdd08900fb2f767073080e71fa830c"
)
PINNED_FAST_DERIVED_KEY = bytes.fromhex(
    "4c97bb69b3aa343d0f8b615ad1713e6b8f8e3f57e8cccd9346659238e21cd086"
)
PINNED_WRAPPED_BYTES_INITIAL = bytes.fromhex(
    "dfdbe9906bb85ee487423ad6e6264f8c040ff7bbae3f2ba934a7e81120b75021f337548414c4e8c2"
)
PINNED_WRAPPED_BYTES_REWRAPPED = bytes.fromhex(
    "55a86551c686fdf79c6c9c87dd5824d19ecdf752828557d74d1f5dfe6e7cfaebdf0d288da59eb56a"
)


def _pinned_wrapped_doc(salt: bytes, wrapped_bytes: bytes) -> bytes:
    payload = {
        "backend": "passphrase-scrypt",
        "params": {
            "n": 1024,
            "p": 1,
            "r": 8,
            "salt": base64.b64encode(salt).decode("ascii"),
        },
        "schema": "synapse-at-rest-wrapped-key.v1",
        "wrapped_key": base64.b64encode(wrapped_bytes).decode("ascii"),
    }
    return json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True).encode("utf-8") + b"\n"


PINNED_WRAPPED_DOC_INITIAL = _pinned_wrapped_doc(FIXED_SALT_1, PINNED_WRAPPED_BYTES_INITIAL)
PINNED_WRAPPED_DOC_REWRAPPED = _pinned_wrapped_doc(FIXED_SALT_2, PINNED_WRAPPED_BYTES_REWRAPPED)
PINNED_ENVELOPE = bytes.fromhex(
    "53594e41520100000102030405060708090a0b0c"
    "e3e946b841607601756f2670b8b2a39d77352f90f83fe7e56ebce0d8dc7239cb"
    "cc3cb3c696c65af478915f4185d8034360af0bcfa118"
)


def _install_deterministic_randomness(monkeypatch: pytest.MonkeyPatch, chunks: list[bytes]) -> None:
    stream: Iterator[bytes] = iter(chunks)

    def _next_bytes(length: int) -> bytes:
        chunk = next(stream)
        assert len(chunk) == length
        return chunk

    monkeypatch.setattr(secrets, "token_bytes", _next_bytes)
    monkeypatch.setattr(os, "urandom", _next_bytes)


def test_format_constants_are_byte_identical() -> None:
    assert ENVELOPE_MAGIC == b"SYNAR\x01\x00\x00"
    assert KEY_BYTES == 32
    assert NONCE_BYTES == 12
    assert DEFAULT_SCRYPT_N == 2**15
    assert DEFAULT_SCRYPT_R == 8
    assert DEFAULT_SCRYPT_P == 1
    assert SCRYPT_SALT_BYTES == 16
    assert BACKUP_MANIFEST_SCHEMA == "synapse-at-rest-backup.v1"
    assert WRAPPED_KEY_SCHEMA == "synapse-at-rest-wrapped-key.v1"
    assert PASSPHRASE_SCRYPT_BACKEND == "passphrase-scrypt"
    assert GCM_MESSAGE_LIMIT == 2**32
    assert GCM_REKEY_WARNING_THRESHOLD == (2**32) - ((2**32) // 16)


def test_raw_and_passphrase_key_files_are_byte_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert (
        derive_key(
            "correct-horse-battery",
            FIXED_SALT_1,
            n=DEFAULT_SCRYPT_N,
            r=DEFAULT_SCRYPT_R,
            p=DEFAULT_SCRYPT_P,
        )
        == PINNED_DEFAULT_DERIVED_KEY
    )

    _install_deterministic_randomness(monkeypatch, [FIXED_RAW_KEY, FIXED_SALT_1])
    raw_path = tmp_path / "raw.key"
    generate_key_file(raw_path)
    assert raw_path.read_bytes() == FIXED_RAW_KEY
    assert_owner_only_file_path(raw_path, purpose="at-rest key")
    if os.name == "posix":
        assert stat.S_IMODE(raw_path.stat().st_mode) == 0o600

    derived_path = tmp_path / "derived.key"
    generate_key_file_from_passphrase(derived_path, "correct-horse-battery", n=2**10, r=8, p=1)
    assert derived_path.read_bytes() == PINNED_FAST_DERIVED_KEY
    assert_owner_only_file_path(derived_path, purpose="at-rest key")
    if os.name == "posix":
        assert stat.S_IMODE(derived_path.stat().st_mode) == 0o600


def test_wrapped_key_document_and_rewrap_are_byte_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_deterministic_randomness(
        monkeypatch, [FIXED_DATA_KEY, FIXED_SALT_1, FIXED_NONCE, FIXED_SALT_2]
    )
    wrapped_path = tmp_path / "wrapped.key"
    generate_wrapped_key_file(wrapped_path, "initial-pass", n=2**10, r=8, p=1)
    assert wrapped_path.read_bytes() == PINNED_WRAPPED_DOC_INITIAL
    assert_owner_only_file_path(wrapped_path, purpose="wrapped at-rest key")

    cipher_before = AtRestCipher.from_wrapped_key_file(wrapped_path, "initial-pass")
    sealed = cipher_before.encrypt(FIXED_PLAINTEXT)
    assert sealed == PINNED_ENVELOPE

    rewrap_wrapped_key_file(wrapped_path, "initial-pass", "rotated-pass", n=2**10, r=8, p=1)
    assert wrapped_path.read_bytes() == PINNED_WRAPPED_DOC_REWRAPPED
    assert_owner_only_file_path(wrapped_path, purpose="wrapped at-rest key")
    if os.name == "posix":
        assert stat.S_IMODE(wrapped_path.stat().st_mode) == 0o600

    cipher_after = AtRestCipher.from_wrapped_key_file(wrapped_path, "rotated-pass")
    assert cipher_after.decrypt(sealed) == FIXED_PLAINTEXT


def test_envelope_bytes_and_decrypt_are_byte_identical(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_deterministic_randomness(monkeypatch, [FIXED_NONCE])
    cipher = AtRestCipher(FIXED_DATA_KEY)
    blob = cipher.encrypt(FIXED_PLAINTEXT)
    assert blob == PINNED_ENVELOPE
    assert blob[: len(ENVELOPE_MAGIC)] == ENVELOPE_MAGIC
    assert blob[len(ENVELOPE_MAGIC) : len(ENVELOPE_MAGIC) + NONCE_BYTES] == FIXED_NONCE
    assert cipher.decrypt(PINNED_ENVELOPE) == FIXED_PLAINTEXT


def test_profile_backup_manifest_and_restore_are_byte_identical(tmp_path: Path) -> None:
    cipher = AtRestCipher(FIXED_DATA_KEY)
    relay = tmp_path / "feed.ndjson"
    relay.write_bytes(PINNED_ENVELOPE)
    surfaces = (AtRestSurface("relay-log", relay),)

    bundle_dir = tmp_path / "bundle"
    manifest = backup_profile(surfaces, bundle_dir, cipher)
    expected_backup_path = bundle_dir / "0001-feed.ndjson.encrypted"
    expected_manifest = (
        json.dumps(
            {
                "files": [
                    {
                        "backup_path": str(expected_backup_path),
                        "role": "relay-log",
                        "source_path": str(relay),
                    }
                ],
                "schema_version": BACKUP_MANIFEST_SCHEMA,
            },
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )
    assert manifest.read_bytes() == expected_manifest
    assert expected_backup_path.read_bytes() == PINNED_ENVELOPE
    assert_owner_only_file_path(manifest, purpose="at-rest backup manifest")
    assert_owner_only_file_path(expected_backup_path, purpose="at-rest backup copy")
    if os.name == "posix":
        assert stat.S_IMODE(manifest.stat().st_mode) == 0o600
        assert stat.S_IMODE(expected_backup_path.stat().st_mode) == 0o600

    relay.unlink()
    restored = restore_profile_backup(manifest, cipher)
    assert restored.changed == 1
    assert restored.skipped == 0
    assert restored.manifest == manifest
    assert relay.read_bytes() == PINNED_ENVELOPE
    assert decrypt_file(relay, cipher) == FIXED_PLAINTEXT
    if os.name == "posix":
        assert stat.S_IMODE(relay.stat().st_mode) == 0o600


def test_refusals_preserve_exact_exception_types_and_messages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Wrong-mode key file (check_key_file)
    if os.name == "posix":
        loose = tmp_path / "loose.key"
        loose.write_bytes(FIXED_RAW_KEY)
        loose.chmod(0o644)
        assert check_key_file(loose) == (
            False,
            f"key file must be owner-only (chmod 600): {loose}",
        )

    # Short key (cipher, check_key_file, load_key_file)
    with pytest.raises(ValueError, match=r"^at-rest key must be 32 bytes, got 5$"):
        AtRestCipher(b"short")
    short = tmp_path / "short.key"
    short.write_bytes(b"short")
    if os.name == "posix":
        short.chmod(0o600)
    assert check_key_file(short) == (
        False,
        f"key file must hold exactly 32 bytes: {short}",
    )
    with pytest.raises(
        ValueError,
        match=rf"^key file must hold exactly 32 bytes: {re.escape(str(short))}$",
    ):
        load_key_file(short)

    # Corrupt envelope (non-envelope, truncated, tampered tag)
    cipher = AtRestCipher(FIXED_DATA_KEY)
    with pytest.raises(ValueError, match=r"^not a Synapse at-rest envelope$"):
        cipher.decrypt(b"plaintext-not-envelope")
    with pytest.raises(ValueError, match=r"^not a Synapse at-rest envelope$"):
        cipher.decrypt(ENVELOPE_MAGIC + b"short")
    tampered = bytearray(PINNED_ENVELOPE)
    tampered[-1] ^= 0xFF
    with pytest.raises(InvalidTag):
        cipher.decrypt(bytes(tampered))

    # Exhausted cipher at lowered GCM_MESSAGE_LIMIT
    monkeypatch.setattr(at_rest, "GCM_MESSAGE_LIMIT", 2)
    limited = AtRestCipher(FIXED_DATA_KEY)
    limited.encrypt(b"one")
    limited.encrypt(b"two")
    with pytest.raises(
        AtRestKeyExhausted,
        match=(
            r"^at-rest key has reached its AES-GCM message limit "
            r"\(2 messages\); rotate the key before encrypting more$"
        ),
    ):
        limited.encrypt(b"three")

    # Wrong passphrase on wrapped key
    wrapped_path = tmp_path / "wrapped.key"
    wrapped_path.write_bytes(PINNED_WRAPPED_DOC_INITIAL)
    with pytest.raises(
        ValueError,
        match=r"^cannot unwrap data key: wrong key-encryption key or corrupt wrapped key$",
    ):
        AtRestCipher.from_wrapped_key_file(wrapped_path, "wrong-pass")

    # Unknown wrapped-key schema
    unknown_schema = tmp_path / "unknown_schema.key"
    unknown_schema.write_text(
        json.dumps(
            {
                "schema": "synapse-at-rest-wrapped-key.v999",
                "backend": PASSPHRASE_SCRYPT_BACKEND,
                "params": {},
                "wrapped_key": "AAAA",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(
        ValueError,
        match=rf"^not a Synapse wrapped at-rest key file: {re.escape(str(unknown_schema))}$",
    ):
        AtRestCipher.from_wrapped_key_file(unknown_schema, "initial-pass")

    # Tampered wrapped key rejected by unwrap_data_key
    wrapped_bytes = wrap_data_key(FIXED_DATA_KEY, FIXED_RAW_KEY)
    bad_wrap = bytearray(wrapped_bytes)
    bad_wrap[0] ^= 0x01
    with pytest.raises(
        ValueError,
        match=r"^cannot unwrap data key: wrong key-encryption key or corrupt wrapped key$",
    ):
        unwrap_data_key(bytes(bad_wrap), FIXED_RAW_KEY)


def test_importing_at_rest_does_not_eagerly_import_cryptography() -> None:
    code = (
        "import builtins, sys\n"
        "real_import = builtins.__import__\n"
        "def blocked(name, *args, **kwargs):\n"
        "    if name == 'cryptography' or name.startswith('cryptography.'):\n"
        "        raise ImportError('cryptography blocked')\n"
        "    return real_import(name, *args, **kwargs)\n"
        "builtins.__import__ = blocked\n"
        "import synapse_channel.core.at_rest_format\n"
        "import synapse_channel.core.at_rest\n"
        "assert 'cryptography' not in sys.modules\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")},
    )
    assert result.returncode == 0, result.stderr


def test_at_rest_format_module_ownership_and_import_order() -> None:
    from synapse_channel.core import at_rest_format

    assert at_rest_format.require_aes_gcm.__module__ == "synapse_channel.core.at_rest_format"
    assert "_AESGCMFactory" not in vars(at_rest)
    assert "require_aes_gcm" in vars(at_rest_format)
    for order in (
        "import synapse_channel.core.at_rest_format, synapse_channel.core.at_rest",
        "import synapse_channel.core.at_rest, synapse_channel.core.at_rest_format",
    ):
        result = subprocess.run(
            [sys.executable, "-c", order],
            check=False,
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")},
        )
        assert result.returncode == 0, f"{order}: {result.stderr}"
