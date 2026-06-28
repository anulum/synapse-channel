# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the at-rest key-file management CLI

from __future__ import annotations

from pathlib import Path

import pytest

from synapse_channel import cli, cli_encrypt_key
from synapse_channel.core.at_rest import KEY_BYTES


def test_parser_registers_encrypt_key_subcommands() -> None:
    parser = cli.build_parser()
    generate = parser.parse_args(["encrypt-key", "generate", "/tmp/store.key"])
    assert generate.func is cli_encrypt_key._cmd_generate
    assert generate.encrypt_key_command == "generate"
    check = parser.parse_args(["encrypt-key", "check", "/tmp/store.key"])
    assert check.func is cli_encrypt_key._cmd_check
    profile = parser.parse_args(["encrypt-key", "profile", "--key", "/tmp/store.key"])
    assert profile.func is cli_encrypt_key._cmd_profile
    migrate = parser.parse_args(["encrypt-key", "migrate", "--key", "/tmp/store.key"])
    assert migrate.func is cli_encrypt_key._cmd_migrate
    rekey = parser.parse_args(
        ["encrypt-key", "rekey", "--old-key", "/tmp/old.key", "--new-key", "/tmp/new.key"]
    )
    assert rekey.func is cli_encrypt_key._cmd_rekey
    backup = parser.parse_args(
        ["encrypt-key", "backup", "--key", "/tmp/store.key", "--backup-dir", "/tmp/backup"]
    )
    assert backup.func is cli_encrypt_key._cmd_backup
    restore = parser.parse_args(
        ["encrypt-key", "restore", "--key", "/tmp/store.key", "--manifest", "/tmp/manifest.json"]
    )
    assert restore.func is cli_encrypt_key._cmd_restore


def test_generate_then_check_round_trip(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    key_path = tmp_path / "store.key"
    parser = cli.build_parser()

    generate_args = parser.parse_args(["encrypt-key", "generate", str(key_path)])
    assert generate_args.func(generate_args) == 0
    assert key_path.stat().st_size == KEY_BYTES
    assert "owner-only" in capsys.readouterr().out

    check_args = parser.parse_args(["encrypt-key", "check", str(key_path)])
    assert check_args.func(check_args) == 0
    assert "key file ok" in capsys.readouterr().out


def test_generate_refuses_to_overwrite(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    key_path = tmp_path / "store.key"
    key_path.write_bytes(b"existing")
    args = cli.build_parser().parse_args(["encrypt-key", "generate", str(key_path)])
    assert args.func(args) == 1
    assert "refusing to overwrite" in capsys.readouterr().out


def test_check_reports_a_bad_key(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    loose = tmp_path / "loose.key"
    loose.write_bytes(b"k" * KEY_BYTES)
    loose.chmod(0o644)
    args = cli.build_parser().parse_args(["encrypt-key", "check", str(loose)])
    assert args.func(args) == 1
    assert "owner-only" in capsys.readouterr().out


def test_migrate_and_profile_cover_all_runtime_surface_flags(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    key_path = tmp_path / "store.key"
    generate = cli.build_parser().parse_args(["encrypt-key", "generate", str(key_path)])
    cli_encrypt_key._cmd_generate(generate)
    capsys.readouterr()
    db = tmp_path / "hub.db"
    relay = tmp_path / "feed.ndjson"
    a2a = tmp_path / "a2a-state.json"
    cursor = tmp_path / "feed.cursor"
    archive = tmp_path / "archive.html"
    db.write_bytes(b"sqlite")
    Path(f"{db}-wal").write_bytes(b"wal")
    Path(f"{db}-shm").write_bytes(b"shm")
    relay.write_text('{"ty":"chat"}\n', encoding="utf-8")
    a2a.write_text('{"tasks":{},"pushConfigs":{}}', encoding="utf-8")
    cursor.write_text("5", encoding="utf-8")
    archive.write_text("<!doctype html><html></html>", encoding="utf-8")
    parser = cli.build_parser()

    migrate = parser.parse_args(
        [
            "encrypt-key",
            "migrate",
            "--key",
            str(key_path),
            "--sqlite-db",
            str(db),
            "--relay-log",
            str(relay),
            "--a2a-state-file",
            str(a2a),
            "--cursor",
            str(cursor),
            "--archive-report",
            str(archive),
            "--backup-dir",
            str(tmp_path / "migration-backup"),
        ]
    )
    assert migrate.func(migrate) == 0
    assert "encrypted 7 file(s)" in capsys.readouterr().out

    profile = parser.parse_args(
        [
            "encrypt-key",
            "profile",
            "--key",
            str(key_path),
            "--sqlite-db",
            str(db),
            "--relay-log",
            str(relay),
            "--a2a-state-file",
            str(a2a),
            "--cursor",
            str(cursor),
            "--archive-report",
            str(archive),
            "--require-encrypted",
        ]
    )
    assert profile.func(profile) == 0
    output = capsys.readouterr().out
    assert "encrypted: 7" in output
    assert "plaintext: 0" in output


def test_rekey_backup_and_restore_round_trip_from_cli(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    old_key = tmp_path / "old.key"
    new_key = tmp_path / "new.key"
    parser = cli.build_parser()
    for key in (old_key, new_key):
        assert parser.parse_args(["encrypt-key", "generate", str(key)]).func(
            parser.parse_args(["encrypt-key", "generate", str(key)])
        ) == 0
    capsys.readouterr()
    relay = tmp_path / "feed.ndjson"
    relay.write_bytes(b'{"ty":"chat"}\n')
    migrate = parser.parse_args(
        ["encrypt-key", "migrate", "--key", str(old_key), "--relay-log", str(relay)]
    )
    assert migrate.func(migrate) == 0
    capsys.readouterr()

    rekey = parser.parse_args(
        [
            "encrypt-key",
            "rekey",
            "--old-key",
            str(old_key),
            "--new-key",
            str(new_key),
            "--relay-log",
            str(relay),
            "--backup-dir",
            str(tmp_path / "rekey-backup"),
        ]
    )
    assert rekey.func(rekey) == 0
    assert "re-encrypted 1 file(s)" in capsys.readouterr().out

    backup = parser.parse_args(
        [
            "encrypt-key",
            "backup",
            "--key",
            str(new_key),
            "--relay-log",
            str(relay),
            "--backup-dir",
            str(tmp_path / "bundle"),
        ]
    )
    assert backup.func(backup) == 0
    backup_output = capsys.readouterr().out
    manifest = backup_output.strip().split()[-1]
    relay.unlink()

    restore = parser.parse_args(
        ["encrypt-key", "restore", "--key", str(new_key), "--manifest", manifest]
    )
    assert restore.func(restore) == 0
    assert relay.exists()
    assert "restored 1 file(s)" in capsys.readouterr().out


def test_profile_reports_missing_and_plaintext_surfaces(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    key_path = tmp_path / "store.key"
    parser = cli.build_parser()
    assert parser.parse_args(["encrypt-key", "generate", str(key_path)]).func(
        parser.parse_args(["encrypt-key", "generate", str(key_path)])
    ) == 0
    capsys.readouterr()
    relay = tmp_path / "feed.ndjson"
    relay.write_text('{"ty":"chat"}\n', encoding="utf-8")

    profile = parser.parse_args(
        [
            "encrypt-key",
            "profile",
            "--key",
            str(key_path),
            "--relay-log",
            str(relay),
            "--cursor",
            str(tmp_path / "missing.cursor"),
        ]
    )

    assert profile.func(profile) == 0
    output = capsys.readouterr().out
    assert f"problem relay-log: {relay} (plaintext)" in output
    assert "missing cursor-file:" in output


def test_runtime_commands_report_key_or_manifest_failures(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    parser = cli.build_parser()
    missing_key = tmp_path / "missing.key"
    relay = tmp_path / "feed.ndjson"
    relay.write_text("plain", encoding="utf-8")

    profile = parser.parse_args(
        ["encrypt-key", "profile", "--key", str(missing_key), "--relay-log", str(relay)]
    )
    assert profile.func(profile) == 1
    assert "at-rest profile problem" in capsys.readouterr().out

    migrate = parser.parse_args(
        ["encrypt-key", "migrate", "--key", str(missing_key), "--relay-log", str(relay)]
    )
    assert migrate.func(migrate) == 1
    assert "at-rest migration problem" in capsys.readouterr().out

    rekey = parser.parse_args(
        [
            "encrypt-key",
            "rekey",
            "--old-key",
            str(missing_key),
            "--new-key",
            str(missing_key),
            "--relay-log",
            str(relay),
        ]
    )
    assert rekey.func(rekey) == 1
    assert "at-rest rekey problem" in capsys.readouterr().out

    backup = parser.parse_args(
        [
            "encrypt-key",
            "backup",
            "--key",
            str(missing_key),
            "--backup-dir",
            str(tmp_path / "bundle"),
            "--relay-log",
            str(relay),
        ]
    )
    assert backup.func(backup) == 1
    assert "at-rest backup problem" in capsys.readouterr().out

    key_path = tmp_path / "store.key"
    assert parser.parse_args(["encrypt-key", "generate", str(key_path)]).func(
        parser.parse_args(["encrypt-key", "generate", str(key_path)])
    ) == 0
    bad_manifest = tmp_path / "bad-manifest.json"
    bad_manifest.write_text("{}", encoding="utf-8")
    capsys.readouterr()
    restore = parser.parse_args(
        ["encrypt-key", "restore", "--key", str(key_path), "--manifest", str(bad_manifest)]
    )
    assert restore.func(restore) == 1
    assert "at-rest restore problem" in capsys.readouterr().out
