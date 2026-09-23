# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the attestation policy/evidence CLI

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from synapse_channel import (
    cli,
    cli_encrypt_key_attest,
)


def test_attest_cli_policy_create_and_verify(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import hashlib

    pcr = hashlib.sha256(b"cli-pcr").hexdigest()
    policy = tmp_path / "policy.json"
    create_args = cli.build_parser().parse_args(
        [
            "encrypt-key",
            "attest-policy-create",
            "--policy-id",
            "cli-seat",
            "--pcr",
            f"0={pcr}",
            str(policy),
        ]
    )
    assert cli_encrypt_key_attest._cmd_attest_policy_create(create_args) == 0
    evidence = tmp_path / "evidence.json"
    evidence_args = cli.build_parser().parse_args(
        [
            "encrypt-key",
            "attest-create",
            "--policy",
            str(policy),
            str(evidence),
        ]
    )
    assert cli_encrypt_key_attest._cmd_attest_create(evidence_args) == 0
    verify_args = cli.build_parser().parse_args(
        [
            "encrypt-key",
            "attest-verify",
            "--policy",
            str(policy),
            "--evidence",
            str(evidence),
        ]
    )
    assert cli_encrypt_key_attest._cmd_attest_verify(verify_args) == 0
    assert "attestation ok" in capsys.readouterr().out


def test_public_attest_cli_refuses_bad_policy_and_existing_destination(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    policy = tmp_path / "policy.json"
    root = ["encrypt-key", "attest-policy-create", "--policy-id", "seat"]
    assert cli.main([*root, "--pcr", "broken", str(policy)]) == 2
    assert "invalid --pcr" in capsys.readouterr().out
    assert cli.main([*root, "--pcr=-1=" + "0" * 64, str(policy)]) == 2
    assert "non-negative" in capsys.readouterr().out
    assert cli.main([*root, "--pcr", "0=" + hashlib.sha256(b"boot").hexdigest(), str(policy)]) == 0
    assert cli.main(root + [str(policy)]) == 1
    assert "refusing to overwrite" in capsys.readouterr().out


def test_public_attest_cli_rejects_wrong_measurement_and_missing_custody(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    policy = tmp_path / "policy.json"
    evidence = tmp_path / "evidence.json"
    expected = hashlib.sha256(b"trusted-boot").hexdigest()
    other = hashlib.sha256(b"different-boot").hexdigest()
    assert (
        cli.main(
            [
                "encrypt-key",
                "attest-policy-create",
                "--policy-id",
                "seat",
                "--pcr",
                f"0={expected}",
                str(policy),
            ]
        )
        == 0
    )
    assert (
        cli.main(
            [
                "encrypt-key",
                "attest-create",
                "--policy",
                str(policy),
                "--nonce",
                "ab" * 16,
                "--pcr",
                f"0={other}",
                str(evidence),
            ]
        )
        == 0
    )
    assert (
        cli.main(
            ["encrypt-key", "attest-verify", "--policy", str(policy), "--evidence", str(evidence)]
        )
        == 2
    )
    assert "PCR 0 digest mismatch" in capsys.readouterr().out
    assert cli.main(["encrypt-key", "attest-create", "--policy", str(policy), str(evidence)]) == 1
    assert (
        cli.main(
            [
                "encrypt-key",
                "attest-create",
                "--policy",
                str(tmp_path / "missing.json"),
                str(tmp_path / "new.json"),
            ]
        )
        == 2
    )
    assert (
        cli.main(
            [
                "encrypt-key",
                "attest-verify",
                "--policy",
                str(policy),
                "--evidence",
                str(tmp_path / "absent.json"),
            ]
        )
        == 2
    )
