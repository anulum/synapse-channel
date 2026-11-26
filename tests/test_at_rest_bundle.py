# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the sealed at-rest backup bundle and its confined restore

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from synapse_channel import cli
from synapse_channel.core.at_rest import (
    AtRestCipher,
    AtRestSurface,
    backup_profile,
    decrypt_file,
    restore_profile_backup,
)
from synapse_channel.core.at_rest_bundle import bundle_entry, write_bundle_manifest
from synapse_channel.core.at_rest_format import KEY_BYTES


def _cipher(fill: bytes = b"k") -> AtRestCipher:
    return AtRestCipher(fill * KEY_BYTES)


def _profile(tmp_path: Path, cipher: AtRestCipher) -> tuple[AtRestSurface, AtRestSurface]:
    """Write two encrypted profile files and return their surfaces."""
    relay = tmp_path / "feed.ndjson"
    cursor = tmp_path / "relay.cursor"
    relay.write_bytes(cipher.encrypt(b"relay-now"))
    cursor.write_bytes(cipher.encrypt(b"cursor-now"))
    return AtRestSurface("relay-log", relay), AtRestSurface("cursor-file", cursor)


def _manifest_data(manifest: Path) -> dict[str, object]:
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _write_manifest_data(manifest: Path, data: dict[str, object]) -> None:
    manifest.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _files(data: dict[str, object]) -> list[dict[str, str]]:
    files = data["files"]
    assert isinstance(files, list)
    return files


def test_backup_manifest_binds_each_copy_by_sha256_and_a_seal(tmp_path: Path) -> None:
    cipher = _cipher()
    surfaces = _profile(tmp_path, cipher)
    manifest = backup_profile(surfaces, tmp_path / "bundle", cipher)

    data = _manifest_data(manifest)
    assert data["schema_version"] == "synapse-at-rest-backup.v2"
    assert isinstance(data["seal"], str)
    for entry, surface in zip(_files(data), surfaces, strict=True):
        copy = manifest.parent / entry["backup_file"]
        assert entry == {
            "backup_file": copy.name,
            "role": surface.role,
            "sha256": hashlib.sha256(copy.read_bytes()).hexdigest(),
            "source_path": str(surface.path),
        }
        assert copy.read_bytes() == surface.path.read_bytes()


def test_restore_refuses_a_copy_replaced_by_another_valid_envelope(tmp_path: Path) -> None:
    cipher = _cipher()
    relay, cursor = _profile(tmp_path, cipher)
    manifest = backup_profile((relay, cursor), tmp_path / "bundle", cipher)
    copy = manifest.parent / _files(_manifest_data(manifest))[0]["backup_file"]
    # An older backup of the same file under the same key decrypts, so only the digest can tell.
    copy.write_bytes(cipher.encrypt(b"relay-older"))
    relay.path.write_bytes(cipher.encrypt(b"relay-current"))

    with pytest.raises(ValueError, match="does not match its recorded SHA-256"):
        restore_profile_backup(manifest, cipher, surfaces=(relay, cursor))
    assert decrypt_file(relay.path, cipher) == b"relay-current"


def test_restore_refuses_another_surfaces_copy_put_in_place(tmp_path: Path) -> None:
    cipher = _cipher()
    relay, cursor = _profile(tmp_path, cipher)
    manifest = backup_profile((relay, cursor), tmp_path / "bundle", cipher)
    relay_copy, cursor_copy = (
        manifest.parent / entry["backup_file"] for entry in _files(_manifest_data(manifest))
    )
    relay_copy.write_bytes(cursor_copy.read_bytes())
    relay.path.unlink()

    with pytest.raises(ValueError, match="does not match its recorded SHA-256"):
        restore_profile_backup(manifest, cipher, surfaces=(relay, cursor))
    assert not relay.path.exists()


def test_restore_refuses_a_manifest_edited_to_match_a_substituted_copy(tmp_path: Path) -> None:
    cipher = _cipher()
    relay, cursor = _profile(tmp_path, cipher)
    manifest = backup_profile((relay, cursor), tmp_path / "bundle", cipher)
    data = _manifest_data(manifest)
    entry = _files(data)[0]
    copy = manifest.parent / entry["backup_file"]
    copy.write_bytes(cipher.encrypt(b"relay-older"))
    entry["sha256"] = hashlib.sha256(copy.read_bytes()).hexdigest()
    _write_manifest_data(manifest, data)
    relay.path.unlink()

    with pytest.raises(ValueError, match="changed after it was sealed"):
        restore_profile_backup(manifest, cipher, surfaces=(relay, cursor))
    assert not relay.path.exists()


def test_restore_refuses_a_manifest_path_outside_the_given_profile_before_writing(
    tmp_path: Path,
) -> None:
    cipher = _cipher()
    relay, cursor = _profile(tmp_path, cipher)
    manifest = backup_profile((relay, cursor), tmp_path / "bundle", cipher)
    relay.path.unlink()
    cursor.path.unlink()
    other = AtRestSurface("relay-log", tmp_path / "other-hub" / "feed.ndjson")

    with pytest.raises(ValueError, match="not in the profile given to restore"):
        restore_profile_backup(manifest, cipher, surfaces=(other, cursor))
    assert not relay.path.exists()
    assert not cursor.path.exists()
    assert not other.path.exists()


def test_restore_refuses_a_manifest_naming_another_path_even_unsealed(tmp_path: Path) -> None:
    cipher = _cipher()
    relay, cursor = _profile(tmp_path, cipher)
    manifest = backup_profile((relay, cursor), tmp_path / "bundle", cipher)
    victim = tmp_path / "victim.txt"
    victim.write_bytes(b"operator data")
    data = _manifest_data(manifest)
    _files(data)[0]["source_path"] = str(victim)
    _write_manifest_data(manifest, data)

    with pytest.raises(ValueError, match="changed after it was sealed"):
        restore_profile_backup(
            manifest, cipher, surfaces=(AtRestSurface("relay-log", victim), cursor)
        )
    assert victim.read_bytes() == b"operator data"


@pytest.mark.parametrize(
    "backup_file",
    ["../0001-feed.ndjson.encrypted", "/tmp/0001-feed.ndjson.encrypted", "feed.ndjson", ""],
)
def test_restore_reads_only_named_copies_inside_the_bundle(
    tmp_path: Path, backup_file: str
) -> None:
    cipher = _cipher()
    relay, cursor = _profile(tmp_path, cipher)
    manifest = backup_profile((relay, cursor), tmp_path / "bundle", cipher)
    data = _manifest_data(manifest)
    _files(data)[0]["backup_file"] = backup_file
    _write_manifest_data(manifest, data)

    with pytest.raises(ValueError, match="malformed file entry"):
        restore_profile_backup(manifest, cipher, surfaces=(relay, cursor))


def test_restore_refuses_a_copy_that_is_a_link_out_of_the_bundle(tmp_path: Path) -> None:
    cipher = _cipher()
    relay, cursor = _profile(tmp_path, cipher)
    manifest = backup_profile((relay, cursor), tmp_path / "bundle", cipher)
    copy = manifest.parent / _files(_manifest_data(manifest))[0]["backup_file"]
    outside = tmp_path / "outside.encrypted"
    outside.write_bytes(copy.read_bytes())
    copy.unlink()
    copy.symlink_to(outside)

    with pytest.raises(ValueError, match="regular file inside the bundle"):
        restore_profile_backup(manifest, cipher, surfaces=(relay, cursor))


def test_restore_refuses_a_copy_that_is_not_a_regular_file(tmp_path: Path) -> None:
    cipher = _cipher()
    relay, cursor = _profile(tmp_path, cipher)
    manifest = backup_profile((relay, cursor), tmp_path / "bundle", cipher)
    copy = manifest.parent / _files(_manifest_data(manifest))[0]["backup_file"]
    copy.unlink()
    copy.mkdir()

    with pytest.raises(ValueError, match="regular file inside the bundle"):
        restore_profile_backup(manifest, cipher, surfaces=(relay, cursor))


def test_restore_refuses_an_entry_with_a_value_that_is_not_text(tmp_path: Path) -> None:
    cipher = _cipher()
    relay, cursor = _profile(tmp_path, cipher)
    manifest = backup_profile((relay, cursor), tmp_path / "bundle", cipher)
    data = _manifest_data(manifest)
    first, second = _files(data)
    edited: dict[str, object] = {**first, "role": 1}
    data["files"] = [edited, second]
    _write_manifest_data(manifest, data)

    with pytest.raises(ValueError, match="malformed file entry"):
        restore_profile_backup(manifest, cipher, surfaces=(relay, cursor))


def test_restore_refuses_a_missing_copy(tmp_path: Path) -> None:
    cipher = _cipher()
    relay, cursor = _profile(tmp_path, cipher)
    manifest = backup_profile((relay, cursor), tmp_path / "bundle", cipher)
    (manifest.parent / _files(_manifest_data(manifest))[1]["backup_file"]).unlink()

    with pytest.raises(ValueError, match="missing from the bundle"):
        restore_profile_backup(manifest, cipher, surfaces=(relay, cursor))


def test_restore_writes_nothing_when_a_later_entry_fails(tmp_path: Path) -> None:
    cipher = _cipher()
    relay, cursor = _profile(tmp_path, cipher)
    manifest = backup_profile((relay, cursor), tmp_path / "bundle", cipher)
    cursor_copy = manifest.parent / _files(_manifest_data(manifest))[1]["backup_file"]
    cursor_copy.write_bytes(cipher.encrypt(b"cursor-older"))
    relay.path.unlink()

    with pytest.raises(ValueError, match="does not match its recorded SHA-256"):
        restore_profile_backup(manifest, cipher, surfaces=(relay, cursor))
    assert not relay.path.exists()


def test_restore_writes_nothing_when_a_later_copy_is_not_an_envelope(tmp_path: Path) -> None:
    cipher = _cipher()
    relay, cursor = _profile(tmp_path, cipher)
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    relay_copy = bundle / "0001-feed.ndjson.encrypted"
    cursor_copy = bundle / "0002-relay.cursor.encrypted"
    relay_copy.write_bytes(relay.path.read_bytes())
    cursor_copy.write_bytes(b"plaintext cursor")
    # Both copies are bound and sealed as backup would, so only the envelope check can refuse.
    manifest = write_bundle_manifest(
        bundle,
        (
            bundle_entry(relay.role, relay.path, relay_copy),
            bundle_entry(cursor.role, cursor.path, cursor_copy),
        ),
        cipher,
    )
    relay.path.unlink()

    with pytest.raises(ValueError, match="plaintext or corrupt"):
        restore_profile_backup(manifest, cipher, surfaces=(relay, cursor))
    assert not relay.path.exists()


def test_restore_refuses_a_v1_manifest_that_binds_no_digest(tmp_path: Path) -> None:
    cipher = _cipher()
    relay, cursor = _profile(tmp_path, cipher)
    copy = tmp_path / "0001-feed.ndjson.encrypted"
    copy.write_bytes(relay.path.read_bytes())
    manifest = tmp_path / "manifest.json"
    _write_manifest_data(
        manifest,
        {
            "schema_version": "synapse-at-rest-backup.v1",
            "files": [
                {"role": "relay-log", "source_path": str(relay.path), "backup_path": str(copy)}
            ],
        },
    )
    relay.path.unlink()

    with pytest.raises(ValueError, match="binds no file digest"):
        restore_profile_backup(manifest, cipher, surfaces=(relay, cursor))
    assert not relay.path.exists()


def test_restore_with_another_key_is_a_clean_refusal(tmp_path: Path) -> None:
    cipher = _cipher()
    relay, cursor = _profile(tmp_path, cipher)
    manifest = backup_profile((relay, cursor), tmp_path / "bundle", cipher)

    with pytest.raises(ValueError, match="seal does not verify with this key"):
        restore_profile_backup(manifest, _cipher(b"x"), surfaces=(relay, cursor))


def test_restore_round_trip_restores_each_surface_of_the_profile(tmp_path: Path) -> None:
    cipher = _cipher()
    relay, cursor = _profile(tmp_path, cipher)
    manifest = backup_profile((relay, cursor), tmp_path / "bundle", cipher)
    relay.path.unlink()
    cursor.path.unlink()

    result = restore_profile_backup(manifest, cipher, surfaces=(relay, cursor))

    assert result.changed == 2
    assert result.manifest == manifest
    assert decrypt_file(relay.path, cipher) == b"relay-now"
    assert decrypt_file(cursor.path, cipher) == b"cursor-now"


def test_cli_restore_confines_writes_to_the_profile_flags(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    parser = cli.build_parser()
    key = tmp_path / "store.key"
    generate = parser.parse_args(["encrypt-key", "generate", str(key)])
    assert generate.func(generate) == 0
    cipher = AtRestCipher.from_key_file(key)
    relay = tmp_path / "feed.ndjson"
    relay.write_bytes(cipher.encrypt(b"relay-now"))
    backup = parser.parse_args(
        ["encrypt-key", "backup", "--key", str(key), "--relay-log", str(relay)]
        + ["--backup-dir", str(tmp_path / "bundle")]
    )
    assert backup.func(backup) == 0
    manifest = capsys.readouterr().out.strip().split()[-1]
    relay.unlink()

    without_profile = parser.parse_args(
        ["encrypt-key", "restore", "--key", str(key), "--manifest", manifest]
    )
    assert without_profile.func(without_profile) == 1
    assert "not in the profile given to restore" in capsys.readouterr().out
    assert not relay.exists()

    restore = parser.parse_args(
        ["encrypt-key", "restore", "--key", str(key), "--manifest", manifest]
        + ["--relay-log", str(relay)]
    )
    assert restore.func(restore) == 0
    assert "restored 1 file(s)" in capsys.readouterr().out
    assert decrypt_file(relay, cipher) == b"relay-now"
