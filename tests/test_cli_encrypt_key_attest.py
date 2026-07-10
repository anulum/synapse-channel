# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the attestation policy/evidence CLI

from __future__ import annotations

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
