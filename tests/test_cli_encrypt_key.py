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

from synapse_channel import (
    cli,
    cli_encrypt_key,
    cli_encrypt_key_attest,
    cli_encrypt_key_escrow,
    cli_encrypt_key_hardware,
    cli_encrypt_key_profile,
)
from synapse_channel.core.at_rest import KEY_BYTES


def test_parser_registers_encrypt_key_subcommands() -> None:
    parser = cli.build_parser()
    generate = parser.parse_args(["encrypt-key", "generate", "/tmp/store.key"])
    assert generate.func is cli_encrypt_key._cmd_generate
    assert generate.encrypt_key_command == "generate"
    check = parser.parse_args(["encrypt-key", "check", "/tmp/store.key"])
    assert check.func is cli_encrypt_key._cmd_check
    profile = parser.parse_args(["encrypt-key", "profile", "--key", "/tmp/store.key"])
    assert profile.func is cli_encrypt_key_profile._cmd_profile
    migrate = parser.parse_args(["encrypt-key", "migrate", "--key", "/tmp/store.key"])
    assert migrate.func is cli_encrypt_key_profile._cmd_migrate
    rekey = parser.parse_args(
        ["encrypt-key", "rekey", "--old-key", "/tmp/old.key", "--new-key", "/tmp/new.key"]
    )
    assert rekey.func is cli_encrypt_key_profile._cmd_rekey
    backup = parser.parse_args(
        ["encrypt-key", "backup", "--key", "/tmp/store.key", "--backup-dir", "/tmp/backup"]
    )
    assert backup.func is cli_encrypt_key_profile._cmd_backup
    restore = parser.parse_args(
        ["encrypt-key", "restore", "--key", "/tmp/store.key", "--manifest", "/tmp/manifest.json"]
    )
    assert restore.func is cli_encrypt_key_profile._cmd_restore
    generate_wrapped = parser.parse_args(["encrypt-key", "generate-wrapped", "/tmp/w.key"])
    assert generate_wrapped.func is cli_encrypt_key._cmd_generate_wrapped
    rewrap = parser.parse_args(["encrypt-key", "rewrap", "/tmp/w.key"])
    assert rewrap.func is cli_encrypt_key._cmd_rewrap
    pkcs11 = parser.parse_args(
        ["encrypt-key", "generate-wrapped-pkcs11", "--token-label", "t", "/tmp/w.key"]
    )
    assert pkcs11.func is cli_encrypt_key_hardware._cmd_generate_wrapped_pkcs11
    assert pkcs11.token_label == "t"
    assert pkcs11.create_kek is True
    cloud = parser.parse_args(
        [
            "encrypt-key",
            "generate-wrapped-cloud-hsm",
            "--provider",
            "local-aes-kw",
            "--master-key-file",
            "/tmp/m.key",
            "/tmp/w.key",
        ]
    )
    assert cloud.func is cli_encrypt_key_hardware._cmd_generate_wrapped_cloud_hsm
    assert cloud.provider == "local-aes-kw"
    escrow_split = parser.parse_args(
        [
            "encrypt-key",
            "escrow-split",
            "--key",
            "/tmp/k.key",
            "--threshold",
            "2",
            "--shares",
            "3",
            "--out-dir",
            "/tmp/shares",
        ]
    )
    assert escrow_split.func is cli_encrypt_key_escrow._cmd_escrow_split
    assert escrow_split.threshold == 2
    escrow_recover = parser.parse_args(
        [
            "encrypt-key",
            "escrow-recover",
            "--share",
            "/tmp/s1.json",
            "--share",
            "/tmp/s2.json",
            "--out",
            "/tmp/out.key",
        ]
    )
    assert escrow_recover.func is cli_encrypt_key_escrow._cmd_escrow_recover
    attest_policy = parser.parse_args(
        ["encrypt-key", "attest-policy-create", "--policy-id", "seat", "/tmp/p.json"]
    )
    assert attest_policy.func is cli_encrypt_key_attest._cmd_attest_policy_create
    attest_create = parser.parse_args(
        ["encrypt-key", "attest-create", "--policy", "/tmp/p.json", "/tmp/e.json"]
    )
    assert attest_create.func is cli_encrypt_key_attest._cmd_attest_create
    attest_verify = parser.parse_args(
        [
            "encrypt-key",
            "attest-verify",
            "--policy",
            "/tmp/p.json",
            "--evidence",
            "/tmp/e.json",
        ]
    )
    assert attest_verify.func is cli_encrypt_key_attest._cmd_attest_verify


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


def test_generate_from_passphrase_writes_a_key_check_passes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    key_path = tmp_path / "pp.key"
    args = cli.build_parser().parse_args(
        ["encrypt-key", "generate", "--from-passphrase", str(key_path)]
    )
    rc = cli_encrypt_key._cmd_generate(args, passphrase_reader=lambda _prompt: "correct horse")
    assert rc == 0
    assert key_path.stat().st_size == KEY_BYTES
    assert oct(key_path.stat().st_mode & 0o777) == "0o600"
    assert "owner-only" in capsys.readouterr().out

    check_args = cli.build_parser().parse_args(["encrypt-key", "check", str(key_path)])
    assert check_args.func(check_args) == 0


def test_generate_from_passphrase_rejects_mismatched_confirmation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    key_path = tmp_path / "pp.key"
    args = cli.build_parser().parse_args(
        ["encrypt-key", "generate", "--from-passphrase", str(key_path)]
    )
    prompts = iter(["first", "second"])
    rc = cli_encrypt_key._cmd_generate(args, passphrase_reader=lambda _prompt: next(prompts))
    assert rc == 2
    assert "do not match" in capsys.readouterr().out
    assert not key_path.exists()  # nothing written on a mismatch


def test_generate_from_passphrase_threads_scrypt_params_and_rejects_bad_n(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    good = cli.build_parser().parse_args(
        ["encrypt-key", "generate", "--from-passphrase", "--scrypt-n", "16384", str(tmp_path / "a")]
    )
    assert good.scrypt_n == 16384
    assert cli_encrypt_key._cmd_generate(good, passphrase_reader=lambda _p: "pw") == 0
    assert (tmp_path / "a").stat().st_size == KEY_BYTES

    # scrypt n must be a power of two — a bad value is a clean 2, not a crash.
    bad = cli.build_parser().parse_args(
        ["encrypt-key", "generate", "--from-passphrase", "--scrypt-n", "1000", str(tmp_path / "b")]
    )
    assert cli_encrypt_key._cmd_generate(bad, passphrase_reader=lambda _p: "pw") == 2
    assert not (tmp_path / "b").exists()


def test_generate_wrapped_round_trips_and_rewrap_rotates_passphrase(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from synapse_channel.core.at_rest import AtRestCipher

    key_path = tmp_path / "wrapped.key"
    gen = cli.build_parser().parse_args(
        ["encrypt-key", "generate-wrapped", "--scrypt-n", "1024", str(key_path)]
    )
    assert cli_encrypt_key._cmd_generate_wrapped(gen, passphrase_reader=lambda _p: "old-pass") == 0
    assert oct(key_path.stat().st_mode & 0o777) == "0o600"
    assert "wrapped at-rest key" in capsys.readouterr().out

    # The wrapped key opens the cipher and seals data.
    blob = AtRestCipher.from_wrapped_key_file(key_path, "old-pass").encrypt(b"secret")

    # Rotate the passphrase: current, then the new passphrase twice.
    prompts = iter(["old-pass", "new-pass", "new-pass"])
    rw = cli.build_parser().parse_args(
        ["encrypt-key", "rewrap", "--scrypt-n", "1024", str(key_path)]
    )
    assert cli_encrypt_key._cmd_rewrap(rw, passphrase_reader=lambda _p: next(prompts)) == 0
    assert "rewrapped at-rest key" in capsys.readouterr().out
    # Same data key underneath ⇒ ciphertext sealed before the rotation still decrypts.
    assert AtRestCipher.from_wrapped_key_file(key_path, "new-pass").decrypt(blob) == b"secret"


def test_generate_wrapped_rejects_bad_scrypt_n(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # scrypt n must be a power of two — a bad value is a clean exit 2, not a crash.
    bad = cli.build_parser().parse_args(
        ["encrypt-key", "generate-wrapped", "--scrypt-n", "1000", str(tmp_path / "w.key")]
    )
    assert cli_encrypt_key._cmd_generate_wrapped(bad, passphrase_reader=lambda _p: "pw") == 2
    assert "generate-wrapped" in capsys.readouterr().out
    assert not (tmp_path / "w.key").exists()


def test_generate_wrapped_rejects_mismatched_confirmation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    key_path = tmp_path / "wrapped.key"
    args = cli.build_parser().parse_args(["encrypt-key", "generate-wrapped", str(key_path)])
    prompts = iter(["first", "second"])
    rc = cli_encrypt_key._cmd_generate_wrapped(args, passphrase_reader=lambda _p: next(prompts))
    assert rc == 2
    assert "do not match" in capsys.readouterr().out
    assert not key_path.exists()


def test_generate_wrapped_refuses_overwrite(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    key_path = tmp_path / "wrapped.key"
    first = cli.build_parser().parse_args(
        ["encrypt-key", "generate-wrapped", "--scrypt-n", "1024", str(key_path)]
    )
    assert cli_encrypt_key._cmd_generate_wrapped(first, passphrase_reader=lambda _p: "pw") == 0
    capsys.readouterr()
    again = cli.build_parser().parse_args(
        ["encrypt-key", "generate-wrapped", "--scrypt-n", "1024", str(key_path)]
    )
    assert cli_encrypt_key._cmd_generate_wrapped(again, passphrase_reader=lambda _p: "pw") == 1
    assert "refusing to overwrite" in capsys.readouterr().out


def test_rewrap_reports_wrong_current_passphrase(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    key_path = tmp_path / "wrapped.key"
    gen = cli.build_parser().parse_args(
        ["encrypt-key", "generate-wrapped", "--scrypt-n", "1024", str(key_path)]
    )
    assert cli_encrypt_key._cmd_generate_wrapped(gen, passphrase_reader=lambda _p: "right") == 0
    capsys.readouterr()
    prompts = iter(["wrong", "new", "new"])  # wrong current, matching new pair
    rw = cli.build_parser().parse_args(
        ["encrypt-key", "rewrap", "--scrypt-n", "1024", str(key_path)]
    )
    assert cli_encrypt_key._cmd_rewrap(rw, passphrase_reader=lambda _p: next(prompts)) == 2
    assert "rewrap" in capsys.readouterr().out


def test_rewrap_rejects_mismatched_new_passphrase(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    key_path = tmp_path / "wrapped.key"
    gen = cli.build_parser().parse_args(
        ["encrypt-key", "generate-wrapped", "--scrypt-n", "1024", str(key_path)]
    )
    assert cli_encrypt_key._cmd_generate_wrapped(gen, passphrase_reader=lambda _p: "old") == 0
    capsys.readouterr()
    prompts = iter(["old", "newA", "newB"])  # correct current, mismatched new pair
    rw = cli.build_parser().parse_args(["encrypt-key", "rewrap", str(key_path)])
    assert cli_encrypt_key._cmd_rewrap(rw, passphrase_reader=lambda _p: next(prompts)) == 2
    assert "do not match" in capsys.readouterr().out


def test_generate_parser_defaults_scrypt_and_passphrase_flags() -> None:
    from synapse_channel.core.at_rest import (
        DEFAULT_SCRYPT_N,
        DEFAULT_SCRYPT_P,
        DEFAULT_SCRYPT_R,
    )

    args = cli.build_parser().parse_args(["encrypt-key", "generate", "/tmp/k"])  # nosec B108
    assert args.from_passphrase is False
    assert args.scrypt_n == DEFAULT_SCRYPT_N
    assert args.scrypt_r == DEFAULT_SCRYPT_R
    assert args.scrypt_p == DEFAULT_SCRYPT_P


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
