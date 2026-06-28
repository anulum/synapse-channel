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
