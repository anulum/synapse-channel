# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — generate-wrapped-cloud-hsm refusals write no key and reach no cloud
"""Every refused cloud-HSM wrap names its cause, writes no key file and never calls AWS."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import pytest

from synapse_channel import cli
from synapse_channel.cli_encrypt_key_hardware import _cmd_generate_wrapped_cloud_hsm
from synapse_channel.core.at_rest import generate_key_file

_PREFIX = "synapse encrypt-key generate-wrapped-cloud-hsm: "


def _wrap(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str]:
    code = cli.main(["encrypt-key", "generate-wrapped-cloud-hsm", *argv])
    return code, capsys.readouterr().out


def test_aws_kms_needs_a_key_id(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Without --kms-key-id the aws-kms provider is refused before any client exists."""
    destination = tmp_path / "wrapped.key"
    assert _wrap(capsys, "--provider", "aws-kms", str(destination)) == (
        2,
        f"{_PREFIX}--kms-key-id is required for provider aws-kms\n",
    )
    assert not destination.exists()


def test_aws_kms_without_boto3_names_the_extra(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """With boto3 unimportable the wrap stops at client creation; no request can leave."""
    monkeypatch.setitem(sys.modules, "boto3", None)
    destination = tmp_path / "wrapped.key"
    argv = ("--provider", "aws-kms", "--kms-key-id", "alias/synapse", "--region", "eu-central-1")
    assert _wrap(capsys, *argv, str(destination)) == (
        2,
        f"{_PREFIX}the AWS KMS cloud-HSM backend requires the optional 'boto3' dependency; "
        "install it with: pip install synapse-channel[cloud-hsm]\n",
    )
    assert not destination.exists()


def test_an_existing_destination_is_never_overwritten(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A present key file exits 1 and keeps its bytes."""
    master = generate_key_file(tmp_path / "master.key")
    destination = tmp_path / "wrapped.key"
    destination.write_bytes(b"keep")
    argv = ("--provider", "local-aes-kw", "--master-key-file", str(master), str(destination))
    assert _wrap(capsys, *argv) == (
        1,
        f"refusing to overwrite existing key file: {destination}\n",
    )
    assert destination.read_bytes() == b"keep"


def test_a_malformed_master_key_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An owner-only master key of the wrong length exits 2 with the length reason."""
    master = tmp_path / "master.key"
    master.write_bytes(b"12345")
    os.chmod(master, 0o600)
    destination = tmp_path / "wrapped.key"
    argv = ("--provider", "local-aes-kw", "--master-key-file", str(master), str(destination))
    assert _wrap(capsys, *argv) == (
        2,
        f"{_PREFIX}key file must hold exactly 32 bytes: {master}\n",
    )
    assert not destination.exists()


def test_a_provider_outside_the_parser_choices_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A programmatic caller passing an unknown provider gets exit 2, not a fallback."""
    destination = tmp_path / "wrapped.key"
    args = argparse.Namespace(
        path=str(destination),
        provider="gcp-kms",
        master_key_file=None,
        kms_key_id=None,
        region=None,
    )
    assert _cmd_generate_wrapped_cloud_hsm(args) == 2
    assert capsys.readouterr().out == f"{_PREFIX}unsupported provider 'gcp-kms'\n"
    assert not destination.exists()
