# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the threshold key-escrow CLI

from __future__ import annotations

from pathlib import Path

import pytest

from synapse_channel import (
    cli,
    cli_encrypt_key_escrow,
)


def test_escrow_split_and_recover_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from synapse_channel.core.at_rest import generate_key_file, load_key_file

    key = generate_key_file(tmp_path / "store.key")
    original = load_key_file(key)
    shares_dir = tmp_path / "shares"
    split_args = cli.build_parser().parse_args(
        [
            "encrypt-key",
            "escrow-split",
            "--key",
            str(key),
            "--threshold",
            "2",
            "--shares",
            "3",
            "--out-dir",
            str(shares_dir),
        ]
    )
    assert cli_encrypt_key_escrow._cmd_escrow_split(split_args) == 0
    assert "wrote 3 escrow shares" in capsys.readouterr().out
    out_key = tmp_path / "recovered.key"
    recover_args = cli.build_parser().parse_args(
        [
            "encrypt-key",
            "escrow-recover",
            "--share",
            str(shares_dir / "share-01.json"),
            "--share",
            str(shares_dir / "share-03.json"),
            "--out",
            str(out_key),
        ]
    )
    assert cli_encrypt_key_escrow._cmd_escrow_recover(recover_args) == 0
    assert load_key_file(out_key) == original


def test_escrow_cli_refusals_exit_nonzero_without_writing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A bad threshold, an existing output and too few shares each fail closed."""
    from synapse_channel.core.at_rest import generate_key_file

    key = generate_key_file(tmp_path / "store.key")
    parser = cli.build_parser()
    refused_dir = tmp_path / "refused"
    weak = parser.parse_args(
        [
            "encrypt-key",
            "escrow-split",
            "--key",
            str(key),
            "--threshold",
            "1",
            "--shares",
            "3",
            "--out-dir",
            str(refused_dir),
        ]
    )
    assert cli_encrypt_key_escrow._cmd_escrow_split(weak) == 2
    assert capsys.readouterr().out == (
        "synapse encrypt-key escrow-split: escrow threshold must be at least 2\n"
    )
    assert not refused_dir.exists()

    shares_dir = tmp_path / "shares"
    split = parser.parse_args(
        [
            "encrypt-key",
            "escrow-split",
            "--key",
            str(key),
            "--threshold",
            "2",
            "--shares",
            "3",
            "--out-dir",
            str(shares_dir),
        ]
    )
    assert cli_encrypt_key_escrow._cmd_escrow_split(split) == 0
    capsys.readouterr()

    existing = tmp_path / "existing.key"
    existing.write_bytes(b"keep me")
    overwrite = parser.parse_args(
        [
            "encrypt-key",
            "escrow-recover",
            "--share",
            str(shares_dir / "share-01.json"),
            "--share",
            str(shares_dir / "share-02.json"),
            "--out",
            str(existing),
        ]
    )
    assert cli_encrypt_key_escrow._cmd_escrow_recover(overwrite) == 1
    assert capsys.readouterr().out == f"refusing to overwrite existing key file: {existing}\n"
    assert existing.read_bytes() == b"keep me"

    lonely = tmp_path / "one-share.key"
    too_few = parser.parse_args(
        [
            "encrypt-key",
            "escrow-recover",
            "--share",
            str(shares_dir / "share-01.json"),
            "--out",
            str(lonely),
        ]
    )
    assert cli_encrypt_key_escrow._cmd_escrow_recover(too_few) == 2
    assert capsys.readouterr().out == (
        "synapse encrypt-key escrow-recover: need at least 2 shares to recover, got 1\n"
    )
    assert not lonely.exists()
