# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Kimi claim-hook CLI and config regressions

from __future__ import annotations

import argparse
import io
import json
import shlex
import sys
from pathlib import Path

import pytest

from synapse_channel import cli_kimi_claim_hook as hook_cli
from synapse_channel.claim_state import fetch_state_snapshot
from synapse_channel.cli_adapters import add_parsers
from synapse_channel.file_claim_guard import GuardVerdict
from synapse_channel.kimi_hook_config_file import KimiHookConfigFileError
from synapse_channel.kimi_hook_installer import render_hook_config


def _args(*argv: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    add_parsers(parser.add_subparsers())
    return parser.parse_args(["adapters", "kimi-claim-hook", *argv])


def test_render_config_is_valid_toml_shell_safe_and_token_safe(tmp_path: Path) -> None:
    if sys.version_info >= (3, 11):
        import tomllib
    else:  # pragma: no cover - exercised only on Python 3.10
        import tomli as tomllib

    token_file = tmp_path / "hub token"
    token_file.write_text("do-not-embed", encoding="utf-8")
    rendered = render_hook_config(
        identity="seat/one",
        uri="wss://hub.example/ws",
        ready_timeout=2.0,
        token_file=str(token_file),
        synapse_bin=sys.executable,
    )
    config = tomllib.loads(rendered)
    hook = config["hooks"][0]
    assert hook["event"] == "PreToolUse"
    assert hook["matcher"] == "^(Write|Edit)$"
    argv = shlex.split(hook["command"])
    assert argv[:3] == [str(Path(sys.executable).resolve()), "adapters", "kimi-claim-hook"]
    assert argv[argv.index("--token-file") + 1] == str(token_file.resolve())
    assert "do-not-embed" not in rendered
    assert hook["timeout"] > 4


def test_runtime_denial_is_structured_json_on_exit_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def deny(*_args: object, **_kwargs: object) -> GuardVerdict:
        return GuardVerdict(False, "claim required")

    monkeypatch.setattr(hook_cli, "_evaluate", deny)
    monkeypatch.setattr(sys, "stdin", io.StringIO("{}"))
    assert hook_cli._cmd_kimi_claim_hook(_args("--identity", "seat/one")) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert output["hookSpecificOutput"]["permissionDecisionReason"] == "claim required"


@pytest.mark.asyncio
async def test_evaluator_forwards_to_kimi_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    observed: dict[str, object] = {}

    async def capture(raw: str, **kwargs: object) -> GuardVerdict:
        observed["raw"] = raw
        observed.update(kwargs)
        return GuardVerdict(True)

    monkeypatch.setattr(hook_cli, "evaluate_hook_event", capture)
    verdict = await hook_cli._evaluate(
        "{}", identity="seat/one", uri="ws://hub", token=None, timeout=0.1
    )
    assert verdict.allowed
    assert observed["identity"] == "seat/one"
    assert observed["state_fetcher"] is fetch_state_snapshot


def test_print_config_is_read_only_and_missing_binary_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    if sys.version_info >= (3, 11):
        import tomllib
    else:  # pragma: no cover - exercised only on Python 3.10
        import tomli as tomllib

    monkeypatch.chdir(tmp_path)
    good = _args("--identity", "seat/one", "--print-config", "--synapse-bin", sys.executable)
    assert hook_cli._cmd_kimi_claim_hook(good) == 0
    assert tomllib.loads(capsys.readouterr().out)["hooks"]
    assert list(tmp_path.iterdir()) == []

    bad = _args(
        "--identity",
        "seat/one",
        "--print-config",
        "--synapse-bin",
        str(tmp_path / "missing"),
    )
    assert hook_cli._cmd_kimi_claim_hook(bad) == 2
    assert "cannot resolve" in capsys.readouterr().err


def test_print_config_rejects_raw_token_without_echoing_it(
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = _args("--identity", "seat/one", "--print-config", "--token", "raw-secret")
    assert hook_cli._cmd_kimi_claim_hook(args) == 2
    error = capsys.readouterr().err
    assert "use --token-file" in error
    assert "raw-secret" not in error


def test_install_config_rejects_raw_token_without_writing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "config.toml"
    args = _args(
        "--identity",
        "seat/one",
        "--install-config",
        "--token",
        "raw-secret",
        "--kimi-config",
        str(path),
    )
    assert hook_cli._cmd_kimi_claim_hook(args) == 2
    assert not path.exists()
    assert "raw-secret" not in capsys.readouterr().err


def test_conflicting_print_and_install_modes_are_rejected(
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = _args("--identity", "seat/one", "--print-config", "--install-config")
    assert hook_cli._cmd_kimi_claim_hook(args) == 2
    assert "choose exactly one" in capsys.readouterr().err


def test_runtime_requires_identity_before_reading_stdin(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert hook_cli._cmd_kimi_claim_hook(_args()) == 2
    assert "--identity is required" in capsys.readouterr().err


def test_install_config_writes_marked_toml_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    if sys.version_info >= (3, 11):
        import tomllib
    else:  # pragma: no cover - exercised only on Python 3.10
        import tomli as tomllib

    config_path = tmp_path / "config.toml"
    config_path.write_text('model = "x"\n', encoding="utf-8")
    args = _args(
        "--identity",
        "seat/one",
        "--install-config",
        "--kimi-config",
        str(config_path),
        "--synapse-bin",
        sys.executable,
    )
    assert hook_cli._cmd_kimi_claim_hook(args) == 0
    assert "installed Synapse Kimi hook" in capsys.readouterr().out

    content = config_path.read_text(encoding="utf-8")
    config = tomllib.loads(content)
    assert config["hooks"][0]["event"] == "PreToolUse"
    assert "synapse-channel:kimi-hook:begin" in content
    assert "synapse-channel:kimi-hook:end" in content


def test_install_config_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('model = "x"\n', encoding="utf-8")
    base_args = [
        "--identity",
        "seat/one",
        "--install-config",
        "--kimi-config",
        str(config_path),
        "--synapse-bin",
        sys.executable,
    ]
    assert hook_cli._cmd_kimi_claim_hook(_args(*base_args)) == 0
    first = config_path.read_text(encoding="utf-8")
    capsys.readouterr()

    assert hook_cli._cmd_kimi_claim_hook(_args(*base_args)) == 0
    second = config_path.read_text(encoding="utf-8")
    assert "Synapse Kimi hook already installed" in capsys.readouterr().out
    assert second == first
    assert second.count("synapse-channel:kimi-hook:begin") == 1


def test_install_config_reports_controlled_filesystem_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail(*_args: object, **_kwargs: object) -> None:
        raise KimiHookConfigFileError("unsafe config")

    monkeypatch.setattr(hook_cli, "install_hook_config", fail)
    args = _args(
        "--identity",
        "seat/one",
        "--install-config",
        "--kimi-config",
        str(tmp_path / "config.toml"),
    )
    assert hook_cli._cmd_kimi_claim_hook(args) == 2
    assert "cannot install Kimi claim hook: unsafe config" in capsys.readouterr().err


def test_uninstall_config_removes_block_and_keeps_other_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('model = "x"\n', encoding="utf-8")
    install_args = _args(
        "--identity",
        "seat/one",
        "--install-config",
        "--kimi-config",
        str(config_path),
        "--synapse-bin",
        sys.executable,
    )
    assert hook_cli._cmd_kimi_claim_hook(install_args) == 0

    uninstall_args = _args("--uninstall-config", "--kimi-config", str(config_path))
    assert hook_cli._cmd_kimi_claim_hook(uninstall_args) == 0
    content = config_path.read_text(encoding="utf-8")
    assert 'model = "x"' in content
    assert "synapse-channel:kimi-hook:begin" not in content


def test_uninstall_config_removes_file_when_only_block_remains(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config_path = tmp_path / "config.toml"
    install_args = _args(
        "--identity",
        "seat/one",
        "--install-config",
        "--kimi-config",
        str(config_path),
        "--synapse-bin",
        sys.executable,
    )
    assert hook_cli._cmd_kimi_claim_hook(install_args) == 0

    uninstall_args = _args("--uninstall-config", "--kimi-config", str(config_path))
    assert hook_cli._cmd_kimi_claim_hook(uninstall_args) == 0
    assert not config_path.exists()


def test_uninstall_config_reports_not_installed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "missing.toml"
    args = _args("--uninstall-config", "--kimi-config", str(path))
    assert hook_cli._cmd_kimi_claim_hook(args) == 0
    assert "not installed" in capsys.readouterr().out


def test_uninstall_config_reports_controlled_filesystem_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail(_path: Path) -> None:
        raise KimiHookConfigFileError("unsafe config")

    monkeypatch.setattr(hook_cli, "uninstall_hook_config", fail)
    args = _args("--uninstall-config", "--kimi-config", str(tmp_path / "config.toml"))
    assert hook_cli._cmd_kimi_claim_hook(args) == 2
    assert "cannot uninstall Kimi claim hook: unsafe config" in capsys.readouterr().err
