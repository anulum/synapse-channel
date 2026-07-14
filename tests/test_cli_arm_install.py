# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — permanent waiter installer CLI tests

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import pytest

from synapse_channel import cli, cli_arm
from synapse_channel.cli_arm_install import maybe_install_arm
from synapse_channel.service_setup import ArmServiceInstallResult


def _parse(*argv: str) -> argparse.Namespace:
    return cli.build_parser(command="arm").parse_args(["arm", *argv])


def test_arm_install_dispatches_linux_service_install(tmp_path: Path) -> None:
    token_file = tmp_path / "token"
    captured: dict[str, Any] = {}

    def installer(**kwargs: Any) -> ArmServiceInstallResult:
        captured.update(kwargs)
        return ArmServiceInstallResult(True, ("wrote unit", "enabled exact identity"))

    args = _parse(
        "install",
        "--identity",
        "repo/ux",
        "--start",
        "--synapse-bin",
        "/bin/synapse",
        "--uri",
        "wss://hub.example:8876",
        "--token-file",
        str(token_file),
    )

    assert maybe_install_arm(args, installer=installer, platform_name="linux") == 0
    assert captured == {
        "identity": "repo/ux",
        "uri": "wss://hub.example:8876",
        "synapse_bin": "/bin/synapse",
        "token_file": str(token_file.resolve()),
        "start": True,
    }


def test_arm_install_prints_installer_lines(
    capsys: pytest.CaptureFixture[str],
) -> None:
    def installer(**_kwargs: Any) -> ArmServiceInstallResult:
        return ArmServiceInstallResult(True, ("wrote unit", "run systemctl"))

    code = maybe_install_arm(
        _parse("install", "--identity", "repo/ux"),
        installer=installer,
        platform_name="linux",
    )

    assert code == 0
    assert capsys.readouterr().out == "wrote unit\nrun systemctl\n"


def test_arm_install_returns_one_for_service_failure() -> None:
    def installer(**_kwargs: Any) -> ArmServiceInstallResult:
        return ArmServiceInstallResult(False, ("failed",))

    assert (
        maybe_install_arm(
            _parse("install", "--identity", "repo/ux"),
            installer=installer,
            platform_name="linux",
        )
        == 1
    )


def test_arm_install_requires_explicit_identity(capsys: pytest.CaptureFixture[str]) -> None:
    assert maybe_install_arm(_parse("install"), platform_name="linux") == 2
    assert "requires --identity" in capsys.readouterr().err


def test_arm_install_refuses_non_linux(capsys: pytest.CaptureFixture[str]) -> None:
    assert (
        maybe_install_arm(
            _parse("install", "--identity", "repo/ux"),
            platform_name="win32",
        )
        == 2
    )
    error = capsys.readouterr().err
    assert "Linux systemd" in error
    assert "WSL" in error


def test_arm_install_refuses_to_persist_raw_token(capsys: pytest.CaptureFixture[str]) -> None:
    args = _parse("install", "--identity", "repo/ux", "--token", "secret")

    assert maybe_install_arm(args, platform_name="linux") == 2
    assert "will not embed --token" in capsys.readouterr().err


def test_arm_install_refuses_raw_token_even_with_token_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = _parse(
        "install",
        "--identity",
        "repo/ux",
        "--token",
        "secret",
        "--token-file",
        str(tmp_path / "token"),
    )

    assert args.raw_token_supplied is True
    assert maybe_install_arm(args, platform_name="linux") == 2
    assert "will not embed --token" in capsys.readouterr().err


@pytest.mark.parametrize(
    "argv",
    (
        ("--identity", "repo/ux"),
        ("--start",),
        ("--synapse-bin", "/bin/synapse"),
    ),
)
def test_install_only_options_require_install_action(
    argv: tuple[str, ...], capsys: pytest.CaptureFixture[str]
) -> None:
    assert maybe_install_arm(_parse(*argv), platform_name="linux") == 2
    assert "require `synapse arm install`" in capsys.readouterr().err


def test_bare_arm_parser_and_dispatch_remain_unchanged() -> None:
    args = _parse("--name", "repo/ux", "--for", "repo/ux", "--mailbox")

    assert args.arm_action is None
    assert args.name == "repo/ux"
    assert args.for_name == "repo/ux"
    assert args.mailbox is True
    assert args.func is cli_arm._cmd_arm
    assert maybe_install_arm(args, platform_name="linux") is None


def test_bare_arm_still_accepts_raw_token() -> None:
    args = _parse("--name", "repo/ux", "--token", "fixture-value")

    assert args.token == "fixture-value"
    assert args.raw_token_supplied is True
    assert maybe_install_arm(args, platform_name="linux") is None


def test_cli_main_installs_waiter_into_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("SYNAPSE_TOKEN", raising=False)

    code = cli.main(
        [
            "arm",
            "install",
            "--identity",
            "repo/ux",
            "--synapse-bin",
            "/bin/synapse",
        ]
    )

    assert code == 0
    unit = tmp_path / ".config" / "systemd" / "user" / "synapse-arm@.service"
    assert unit.exists()
    assert "--for=%I --directed-only --mailbox" in unit.read_text(encoding="utf-8")


def test_cli_main_persists_token_file_path_not_raw_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token_file = tmp_path / "coordination-token"
    token_file.write_text("fixture-value\n", encoding="utf-8")
    token_file.chmod(0o600)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("SYNAPSE_TOKEN", raising=False)

    code = cli.main(
        [
            "arm",
            "install",
            "--identity",
            "repo/ux",
            "--synapse-bin",
            "/bin/synapse",
            "--token-file",
            str(token_file),
        ]
    )

    assert code == 0
    unit = tmp_path / ".config" / "systemd" / "user" / "synapse-arm@.service"
    text = unit.read_text(encoding="utf-8")
    assert f"--token-file={token_file}" in text
    assert "fixture-value" not in text


def test_cli_main_refuses_ambient_token_for_persistent_unit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("SYNAPSE_TOKEN", "fixture-value")

    code = cli.main(
        [
            "arm",
            "install",
            "--identity",
            "repo/ux",
            "--synapse-bin",
            "/bin/synapse",
        ]
    )

    assert code == 2
    assert "pass --token-file PATH" in capsys.readouterr().err
    assert not (tmp_path / ".config").exists()
