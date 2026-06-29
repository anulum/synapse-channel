# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the at-rest encryption envelope

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from cryptography.exceptions import InvalidTag

from synapse_channel.core import at_rest
from synapse_channel.core.at_rest import (
    ENVELOPE_MAGIC,
    KEY_BYTES,
    AtRestCipher,
    AtRestSurface,
    backup_profile,
    check_key_file,
    decrypt_file,
    derive_key,
    encrypt_file,
    full_profile_surfaces,
    generate_key_file,
    inspect_profile,
    is_envelope,
    migrate_profile,
    rekey_profile,
    require_encrypted_profile,
    restore_profile_backup,
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


def test_passphrase_derivation_works_with_secure_default_parameters() -> None:
    # The default scrypt profile (n=2**15) must not blow OpenSSL's maxmem; this
    # exercises the default path that the n=2**10 overrides above skip.
    salt = b"s" * 16
    cipher = AtRestCipher.from_passphrase("pw", salt)
    assert cipher.decrypt(cipher.encrypt(b"default-profile")) == b"default-profile"


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


def test_check_key_file_rejects_a_symlink(tmp_path: Path) -> None:
    real = tmp_path / "real.key"
    generate_key_file(real)
    link = tmp_path / "link.key"
    link.symlink_to(real)
    ok, reason = check_key_file(link)
    assert ok is False
    assert "not a regular file" in reason


def test_from_key_file_reports_a_missing_key(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="does not exist"):
        AtRestCipher.from_key_file(tmp_path / "absent.key")


def test_from_key_file_refuses_a_symlink(tmp_path: Path) -> None:
    real = tmp_path / "real.key"
    generate_key_file(real)
    link = tmp_path / "link.key"
    link.symlink_to(real)
    with pytest.raises(ValueError, match="must not be a symlink"):
        AtRestCipher.from_key_file(link)


def test_encrypt_file_leaves_no_temp_when_encryption_fails(tmp_path: Path) -> None:
    class _Boom:
        def encrypt(self, plaintext: bytes) -> bytes:
            raise RuntimeError("no cryptography")

    target = tmp_path / "out.enc"
    with pytest.raises(RuntimeError):
        at_rest.encrypt_file(target, b"x", _Boom())  # type: ignore[arg-type]
    assert not target.exists()
    assert list(tmp_path.glob("*.tmp")) == []


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


def test_full_profile_surfaces_include_sqlite_sidecars_and_file_roles(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    relay = tmp_path / "feed.ndjson"
    a2a = tmp_path / "a2a-state.json"
    cursor = tmp_path / "feed.cursor"
    archive = tmp_path / "archive.html"

    surfaces = full_profile_surfaces(
        sqlite_event_stores=[db],
        relay_logs=[relay],
        a2a_state_files=[a2a],
        cursor_files=[cursor],
        archive_outputs=[archive],
    )

    assert [(surface.role, surface.path) for surface in surfaces] == [
        ("sqlite-event-store", db),
        ("sqlite-wal", Path(f"{db}-wal")),
        ("sqlite-shm", Path(f"{db}-shm")),
        ("relay-log", relay),
        ("a2a-state-file", a2a),
        ("cursor-file", cursor),
        ("archive-output", archive),
    ]


def test_migrate_profile_encrypts_all_existing_surfaces_and_backs_up_plaintext(
    tmp_path: Path,
) -> None:
    cipher = _cipher()
    db = tmp_path / "hub.db"
    wal = Path(f"{db}-wal")
    shm = Path(f"{db}-shm")
    relay = tmp_path / "feed.ndjson"
    a2a = tmp_path / "a2a-state.json"
    cursor = tmp_path / "feed.cursor"
    archive = tmp_path / "archive.html"
    for path, payload in {
        db: b"sqlite-main",
        wal: b"wal-sidecar",
        shm: b"shm-sidecar",
        relay: b'{"ty":"chat"}\n',
        a2a: b'{"tasks":{},"pushConfigs":{}}',
        cursor: b"42",
        archive: b"<!doctype html><html></html>",
    }.items():
        path.write_bytes(payload)
        path.chmod(0o600)

    surfaces = full_profile_surfaces(
        sqlite_event_stores=[db],
        relay_logs=[relay],
        a2a_state_files=[a2a],
        cursor_files=[cursor],
        archive_outputs=[archive],
    )
    with pytest.raises(ValueError, match="plaintext"):
        require_encrypted_profile(surfaces, cipher)

    backup_dir = tmp_path / "backup"
    result = migrate_profile(surfaces, cipher, backup_dir=backup_dir)

    assert result.changed == 7
    assert result.skipped == 0
    encrypted_paths = [db, wal, shm, relay, a2a, cursor, archive]
    assert all(is_envelope(path.read_bytes()) for path in encrypted_paths)
    assert decrypt_file(relay, cipher) == b'{"ty":"chat"}\n'
    assert (backup_dir / "0001-hub.db.plain").read_bytes() == b"sqlite-main"
    assert (backup_dir / "0002-hub.db-wal.plain").read_bytes() == b"wal-sidecar"
    assert require_encrypted_profile(surfaces, cipher).encrypted == 7


def test_rekey_profile_moves_encrypted_surfaces_to_a_new_key(tmp_path: Path) -> None:
    old = AtRestCipher(b"o" * KEY_BYTES)
    new = AtRestCipher(b"n" * KEY_BYTES)
    db = tmp_path / "hub.db"
    relay = tmp_path / "feed.ndjson"
    db.write_bytes(old.encrypt(b"main"))
    relay.write_bytes(old.encrypt(b"relay"))
    surfaces = full_profile_surfaces(sqlite_event_stores=[db], relay_logs=[relay])

    result = rekey_profile(surfaces, old, new, backup_dir=tmp_path / "rekey-backup")

    assert result.changed == 2
    assert decrypt_file(db, new) == b"main"
    assert decrypt_file(relay, new) == b"relay"
    with pytest.raises(InvalidTag):
        decrypt_file(db, old)
    assert (tmp_path / "rekey-backup" / "0001-hub.db.encrypted").exists()


def test_backup_and_restore_profile_preserve_encrypted_surfaces(tmp_path: Path) -> None:
    cipher = _cipher()
    db = tmp_path / "hub.db"
    relay = tmp_path / "feed.ndjson"
    db.write_bytes(cipher.encrypt(b"main"))
    relay.write_bytes(cipher.encrypt(b"relay"))
    surfaces = full_profile_surfaces(sqlite_event_stores=[db], relay_logs=[relay])

    manifest = backup_profile(surfaces, tmp_path / "bundle", cipher)
    db.unlink()
    relay.unlink()

    restored = restore_profile_backup(manifest, cipher)

    assert restored.changed == 2
    assert decrypt_file(db, cipher) == b"main"
    assert decrypt_file(relay, cipher) == b"relay"


def test_require_encrypted_profile_fails_closed_for_wrong_key_or_plain_sidecar(
    tmp_path: Path,
) -> None:
    cipher = _cipher()
    wrong = AtRestCipher(b"w" * KEY_BYTES)
    db = tmp_path / "hub.db"
    Path(f"{db}-wal").write_bytes(b"plain-wal")
    db.write_bytes(cipher.encrypt(b"main"))
    surfaces = full_profile_surfaces(sqlite_event_stores=[db])

    with pytest.raises(ValueError, match="sqlite-wal"):
        require_encrypted_profile(surfaces, cipher)
    Path(f"{db}-wal").write_bytes(cipher.encrypt(b"wal"))
    with pytest.raises(ValueError, match="cannot decrypt"):
        require_encrypted_profile(surfaces, wrong)


def test_inspect_profile_reports_missing_plain_encrypted_and_read_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cipher = _cipher()
    encrypted = tmp_path / "encrypted.bin"
    plain = tmp_path / "plain.bin"
    unreadable = tmp_path / "unreadable.bin"
    encrypted.write_bytes(cipher.encrypt(b"secret"))
    plain.write_bytes(b"plain")
    unreadable.write_bytes(b"x")
    real_read_bytes = Path.read_bytes

    def guarded_read_bytes(path: Path) -> bytes:
        if path == unreadable:
            raise OSError("permission denied")
        return real_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", guarded_read_bytes)
    surfaces = (
        AtRestSurface("encrypted", encrypted),
        AtRestSurface("plain", plain),
        AtRestSurface("missing", tmp_path / "missing.bin"),
        AtRestSurface("unreadable", unreadable),
    )

    report = inspect_profile(surfaces)

    assert report.existing == 3
    assert report.missing == 1
    assert report.encrypted == 1
    assert report.plaintext == 2
    assert [status.reason for status in report.statuses] == [
        "encrypted",
        "plaintext",
        "missing",
        "permission denied",
    ]


def test_migrate_profile_skips_missing_and_existing_envelopes(tmp_path: Path) -> None:
    cipher = _cipher()
    encrypted = tmp_path / "already.enc"
    encrypted.write_bytes(cipher.encrypt(b"sealed"))
    surfaces = (
        AtRestSurface("missing", tmp_path / "missing.bin"),
        AtRestSurface("encrypted", encrypted),
    )

    result = migrate_profile(surfaces, cipher)

    assert result.changed == 0
    assert result.skipped == 2


def test_rekey_profile_skips_missing_and_refuses_plaintext(tmp_path: Path) -> None:
    old = AtRestCipher(b"o" * KEY_BYTES)
    new = AtRestCipher(b"n" * KEY_BYTES)
    missing = AtRestSurface("missing", tmp_path / "missing.bin")
    plain_path = tmp_path / "plain.txt"
    plain_path.write_text("plain", encoding="utf-8")

    assert rekey_profile((missing,), old, new).skipped == 1
    with pytest.raises(ValueError, match="run migrate before rekey"):
        rekey_profile((AtRestSurface("plain", plain_path),), old, new)


def test_rekey_profile_rotates_existing_envelope_without_backup(tmp_path: Path) -> None:
    old = AtRestCipher(b"o" * KEY_BYTES)
    new = AtRestCipher(b"n" * KEY_BYTES)
    relay = tmp_path / "feed.ndjson"
    relay.write_bytes(old.encrypt(b"relay"))

    result = rekey_profile((AtRestSurface("relay-log", relay),), old, new)

    assert result.changed == 1
    assert result.skipped == 0
    assert decrypt_file(relay, new) == b"relay"


def test_backup_profile_skips_missing_surfaces(tmp_path: Path) -> None:
    cipher = _cipher()
    relay = tmp_path / "feed.ndjson"
    relay.write_bytes(cipher.encrypt(b"relay"))
    manifest = backup_profile(
        (
            AtRestSurface("relay-log", relay),
            AtRestSurface("cursor-file", tmp_path / "missing.cursor"),
        ),
        tmp_path / "bundle",
        cipher,
    )

    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert [entry["role"] for entry in data["files"]] == ["relay-log"]


def test_backup_profile_cleans_manifest_temp_when_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cipher = _cipher()
    relay = tmp_path / "feed.ndjson"
    relay.write_bytes(cipher.encrypt(b"relay"))
    real_replace = os.replace

    def fail_manifest_replace(src: str | Path, dst: str | Path) -> None:
        if Path(dst).name == "manifest.json":
            raise RuntimeError("replace failed")
        real_replace(src, dst)

    monkeypatch.setattr("synapse_channel.core.at_rest.os.replace", fail_manifest_replace)

    with pytest.raises(RuntimeError, match="replace failed"):
        backup_profile((AtRestSurface("relay-log", relay),), tmp_path / "bundle", cipher)
    assert not list((tmp_path / "bundle").glob(".manifest.json.*.tmp"))


def test_restore_profile_backup_rejects_bad_manifests_and_plaintext_backups(
    tmp_path: Path,
) -> None:
    cipher = _cipher()
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"schema_version":"wrong","files":[]}', encoding="utf-8")
    with pytest.raises(ValueError, match="not a Synapse at-rest backup manifest"):
        restore_profile_backup(manifest, cipher)

    manifest.write_text(
        json.dumps({"schema_version": "synapse-at-rest-backup.v1"}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="no file list"):
        restore_profile_backup(manifest, cipher)

    manifest.write_text(
        json.dumps({"schema_version": "synapse-at-rest-backup.v1", "files": ["bad"]}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="malformed file entry"):
        restore_profile_backup(manifest, cipher)

    manifest.write_text(
        json.dumps({"schema_version": "synapse-at-rest-backup.v1", "files": [{"role": 1}]}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="malformed file entry"):
        restore_profile_backup(manifest, cipher)

    backup = tmp_path / "plain.backup"
    backup.write_bytes(b"plain")
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "synapse-at-rest-backup.v1",
                "files": [
                    {
                        "role": "relay-log",
                        "source_path": str(tmp_path / "feed.ndjson"),
                        "backup_path": str(backup),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="plaintext or corrupt"):
        restore_profile_backup(manifest, cipher)
