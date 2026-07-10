# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the hardware-backed wrapped-key CLI (PKCS#11 / TPM 2.0 / cloud HSM)

from __future__ import annotations

from pathlib import Path

import pytest

from synapse_channel import (
    cli,
    cli_encrypt_key_hardware,
)


def test_generate_wrapped_pkcs11_requires_a_module(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # No --pkcs11-module and no PKCS11_MODULE env -> a clean exit 2 before touching the token.
    monkeypatch.delenv("PKCS11_MODULE", raising=False)
    args = cli.build_parser().parse_args(
        ["encrypt-key", "generate-wrapped-pkcs11", "--token-label", "t", str(tmp_path / "w.key")]
    )
    rc = cli_encrypt_key_hardware._cmd_generate_wrapped_pkcs11(args, pin_reader=lambda _p: "1234")
    assert rc == 2
    assert "PKCS#11 module is required" in capsys.readouterr().out
    assert not (tmp_path / "w.key").exists()


def test_generate_wrapped_pkcs11_reports_an_existing_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # A pre-existing destination is refused by the token wrapper -> a clean exit 1.
    # PKCS11_PIN set -> the env-PIN branch is taken and the prompt reader stays idle.
    monkeypatch.setenv("PKCS11_MODULE", "/opt/softhsm/libsofthsm2.so")
    monkeypatch.setenv("PKCS11_PIN", "1234")
    from synapse_channel.core import at_rest_pkcs11

    def refuse(*_args: object, **_kwargs: object) -> object:
        raise FileExistsError("refusing to overwrite existing key file")

    monkeypatch.setattr(at_rest_pkcs11, "generate_wrapped_key_file_pkcs11", refuse)

    def _forbidden_reader(_prompt: str) -> str:
        raise AssertionError("pin_reader must not run when PKCS11_PIN is set")

    args = cli.build_parser().parse_args(
        ["encrypt-key", "generate-wrapped-pkcs11", "--token-label", "t", str(tmp_path / "w.key")]
    )
    rc = cli_encrypt_key_hardware._cmd_generate_wrapped_pkcs11(args, pin_reader=_forbidden_reader)
    assert rc == 1
    assert "refusing to overwrite" in capsys.readouterr().out


def test_generate_wrapped_pkcs11_reports_a_token_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # A token/library failure surfaces as a RuntimeError -> exit 2 naming the command.
    # PKCS11_PIN unset -> the interactive prompt branch supplies the PIN.
    monkeypatch.setenv("PKCS11_MODULE", "/opt/softhsm/libsofthsm2.so")
    monkeypatch.delenv("PKCS11_PIN", raising=False)
    from synapse_channel.core import at_rest_pkcs11

    def fail(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("token 'synapse' not found on the module")

    monkeypatch.setattr(at_rest_pkcs11, "generate_wrapped_key_file_pkcs11", fail)
    args = cli.build_parser().parse_args(
        ["encrypt-key", "generate-wrapped-pkcs11", "--token-label", "t", str(tmp_path / "w.key")]
    )
    rc = cli_encrypt_key_hardware._cmd_generate_wrapped_pkcs11(args, pin_reader=lambda _p: "1234")
    assert rc == 2
    out = capsys.readouterr().out
    assert "generate-wrapped-pkcs11" in out
    assert "token 'synapse' not found" in out


def test_generate_wrapped_tpm2_reports_an_existing_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # A pre-existing destination is refused by the TPM wrapper -> a clean exit 1.
    from synapse_channel.core import at_rest_tpm2

    def refuse(*_args: object, **_kwargs: object) -> object:
        raise FileExistsError("refusing to overwrite existing key file")

    monkeypatch.setattr(at_rest_tpm2, "generate_wrapped_key_file_tpm2", refuse)
    args = cli.build_parser().parse_args(
        ["encrypt-key", "generate-wrapped-tpm2", str(tmp_path / "w.key")]
    )
    rc = cli_encrypt_key_hardware._cmd_generate_wrapped_tpm2(args)
    assert rc == 1
    assert "refusing to overwrite" in capsys.readouterr().out


def test_generate_wrapped_tpm2_reports_a_device_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # A TPM/device failure surfaces as a RuntimeError -> exit 2 naming the command.
    # An explicit --tcti exercises the flag branch of the transport resolution.
    from synapse_channel.core import at_rest_tpm2

    def fail(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("no TPM device at /dev/tpmrm0")

    monkeypatch.setattr(at_rest_tpm2, "generate_wrapped_key_file_tpm2", fail)
    args = cli.build_parser().parse_args(
        [
            "encrypt-key",
            "generate-wrapped-tpm2",
            "--tcti",
            "device:/dev/tpmrm0",
            str(tmp_path / "w.key"),
        ]
    )
    rc = cli_encrypt_key_hardware._cmd_generate_wrapped_tpm2(args)
    assert rc == 2
    out = capsys.readouterr().out
    assert "generate-wrapped-tpm2" in out
    assert "no TPM device" in out


def test_cloud_hsm_local_cli_round_trip(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from synapse_channel.core.at_rest import generate_key_file
    from synapse_channel.core.at_rest_cloud_hsm import cipher_from_wrapped_key_file_cloud_hsm

    master = generate_key_file(tmp_path / "master.key")
    dest = tmp_path / "cloud.wrapped.key"
    args = cli.build_parser().parse_args(
        [
            "encrypt-key",
            "generate-wrapped-cloud-hsm",
            "--provider",
            "local-aes-kw",
            "--master-key-file",
            str(master),
            str(dest),
        ]
    )
    assert cli_encrypt_key_hardware._cmd_generate_wrapped_cloud_hsm(args) == 0
    assert "cloud-HSM-wrapped" in capsys.readouterr().out
    cipher = cipher_from_wrapped_key_file_cloud_hsm(dest, master_key_file=master)
    assert cipher.decrypt(cipher.encrypt(b"cli-cloud")) == b"cli-cloud"


def test_cloud_hsm_cli_requires_master_key(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = cli.build_parser().parse_args(
        [
            "encrypt-key",
            "generate-wrapped-cloud-hsm",
            "--provider",
            "local-aes-kw",
            str(tmp_path / "w.key"),
        ]
    )
    assert cli_encrypt_key_hardware._cmd_generate_wrapped_cloud_hsm(args) == 2
    assert "master-key-file is required" in capsys.readouterr().out
