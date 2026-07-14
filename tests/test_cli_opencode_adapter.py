# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li

import argparse
import io
import json
import os
from pathlib import Path

import pytest

from synapse_channel import cli_opencode_adapter
from synapse_channel.file_claim_guard import GuardVerdict
from synapse_channel.opencode_claim_guard import MAX_HOOK_EVENT_BYTES


def _install_args(tmp_path: Path, **overrides: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "scope": "project",
        "project": str(tmp_path),
        "home": None,
        "config_root": None,
        "identity": "seat/one",
        "uri": "ws://127.0.0.1:8876",
        "token": None,
        "token_file": None,
        "synapse_bin": "synapse",
        "ready_timeout": 2.0,
        "mcp_timeout_ms": 30_000,
        "dry_run": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def _path_args(tmp_path: Path) -> argparse.Namespace:
    return argparse.Namespace(scope="project", project=str(tmp_path), home=None, config_root=None)


def _stdin(value: str) -> io.TextIOWrapper:
    return io.TextIOWrapper(io.BytesIO(value.encode("utf-8")), encoding="utf-8")


def test_install_status_uninstall_round_trip_preserves_user_config(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / ".opencode" / "opencode.json"
    config.parent.mkdir(mode=0o700)
    config.write_text('{"theme":"dark"}\n')
    os.chmod(config, 0o600)
    assert cli_opencode_adapter._cmd_install(_install_args(tmp_path)) == 0
    installed = json.loads(config.read_text())
    assert installed["theme"] == "dark"
    assert installed["mcp"]["synapse"]["environment"] == {
        "SYNAPSE_ADAPTER_OWNER": "synapse-channel"
    }
    plugin = tmp_path / ".opencode" / "plugins" / "synapse-claim-guard.js"
    assert "Bun.spawn(HOOK_ARGV" in plugin.read_text()
    assert cli_opencode_adapter._cmd_status(_path_args(tmp_path)) == 0
    assert cli_opencode_adapter._cmd_uninstall(_path_args(tmp_path)) == 0
    assert json.loads(config.read_text()) == {"theme": "dark"}
    assert not plugin.exists()
    assert "installed OpenCode" in capsys.readouterr().out


def test_install_is_idempotent_and_token_file_path_is_persisted_not_secret(tmp_path: Path) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("private-secret\n")
    os.chmod(token_file, 0o600)
    args = _install_args(tmp_path, token_file=str(token_file))
    assert cli_opencode_adapter._cmd_install(args) == 0
    assert cli_opencode_adapter._cmd_install(args) == 0
    combined = "\n".join(
        path.read_text() for path in (tmp_path / ".opencode").rglob("*") if path.is_file()
    )
    assert str(token_file.resolve()) in combined
    assert "private-secret" not in combined


def test_dry_run_and_raw_token_refusal_do_not_write(tmp_path: Path) -> None:
    assert cli_opencode_adapter._cmd_install(_install_args(tmp_path, dry_run=True)) == 0
    assert not (tmp_path / ".opencode").exists()
    assert (
        cli_opencode_adapter._cmd_install(
            _install_args(tmp_path, token="raw-secret", token_file=None)
        )
        == 2
    )
    assert not (tmp_path / ".opencode").exists()


def test_print_config_never_contains_raw_token(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = _install_args(tmp_path)
    args.asset = "config"
    assert cli_opencode_adapter._cmd_print_config(args) == 0
    output = capsys.readouterr().out
    assert "SYNAPSE_ADAPTER_OWNER" in output
    assert '--token"' not in output


def test_print_plugin_and_render_failure_paths(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = _install_args(tmp_path)
    args.asset = "plugin"
    assert cli_opencode_adapter._cmd_print_config(args) == 0
    assert "Bun.spawn(HOOK_ARGV" in capsys.readouterr().out
    args.synapse_bin = "definitely-missing-synapse"
    assert cli_opencode_adapter._cmd_print_config(args) == 2


def test_claim_hook_emits_one_explicit_verdict(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def deny(*args: object, **kwargs: object) -> GuardVerdict:
        del args, kwargs
        return GuardVerdict(False, "claim required")

    monkeypatch.setattr(cli_opencode_adapter, "_evaluate", deny)
    monkeypatch.setattr("sys.stdin", _stdin("{}"))
    args = argparse.Namespace(identity="seat/one", uri="ws://unused", token=None, ready_timeout=1.0)
    assert cli_opencode_adapter._cmd_opencode_claim_hook(args) == 0
    assert json.loads(capsys.readouterr().out) == {
        "allowed": False,
        "reason": "claim required",
    }


def test_claim_hook_allow_and_exception_are_explicit(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def allow(*args: object, **kwargs: object) -> GuardVerdict:
        del args, kwargs
        return GuardVerdict(True)

    args = argparse.Namespace(identity="seat/one", uri="ws://unused", token=None, ready_timeout=1.0)
    monkeypatch.setattr("sys.stdin", _stdin("{}"))
    monkeypatch.setattr(cli_opencode_adapter, "_evaluate", allow)
    assert cli_opencode_adapter._cmd_opencode_claim_hook(args) == 0
    assert json.loads(capsys.readouterr().out) == {"allowed": True}

    async def fail(*args: object, **kwargs: object) -> GuardVerdict:
        del args, kwargs
        raise RuntimeError("private")

    monkeypatch.setattr("sys.stdin", _stdin("{}"))
    monkeypatch.setattr(cli_opencode_adapter, "_evaluate", fail)
    assert cli_opencode_adapter._cmd_opencode_claim_hook(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result == {
        "allowed": False,
        "reason": "Synapse claim verification failed closed.",
    }

    called = False

    async def record_call(*args: object, **kwargs: object) -> GuardVerdict:
        nonlocal called
        del args, kwargs
        called = True
        return GuardVerdict(True)

    monkeypatch.setattr("sys.stdin", _stdin("x" * (MAX_HOOK_EVENT_BYTES + 1)))
    monkeypatch.setattr(cli_opencode_adapter, "_evaluate", record_call)
    assert cli_opencode_adapter._cmd_opencode_claim_hook(args) == 0
    assert called is False
    assert json.loads(capsys.readouterr().out) == {
        "allowed": False,
        "reason": "Synapse claim verification failed closed.",
    }


def test_status_detects_partial_or_invalid_install(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli_opencode_adapter._cmd_install(_install_args(tmp_path)) == 0
    plugin = tmp_path / ".opencode" / "plugins" / "synapse-claim-guard.js"
    plugin.unlink()
    assert cli_opencode_adapter._cmd_status(_path_args(tmp_path)) == 1
    config = tmp_path / ".opencode" / "opencode.json"
    config.write_text("not-json")
    assert cli_opencode_adapter._cmd_status(_path_args(tmp_path)) == 2
    config.write_text('{"mcp": []}')
    assert cli_opencode_adapter._cmd_status(_path_args(tmp_path)) == 2
    assert "cannot inspect" in capsys.readouterr().err


def test_uninstall_removes_empty_owned_config_and_refuses_unowned_plugin(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli_opencode_adapter._cmd_install(_install_args(tmp_path)) == 0
    config = tmp_path / ".opencode" / "opencode.json"
    plugin = tmp_path / ".opencode" / "plugins" / "synapse-claim-guard.js"
    assert cli_opencode_adapter._cmd_uninstall(_path_args(tmp_path)) == 0
    assert not config.exists()
    plugin.parent.mkdir(parents=True, exist_ok=True)
    plugin.write_text("export const user = 1;")
    os.chmod(plugin.parent, 0o700)
    os.chmod(plugin, 0o600)
    assert cli_opencode_adapter._cmd_uninstall(_path_args(tmp_path)) == 2
    assert plugin.exists()
    assert "cannot uninstall" in capsys.readouterr().err


def test_private_token_file_rejects_permissive_mode(tmp_path: Path) -> None:
    token = tmp_path / "token"
    token.write_text("secret")
    os.chmod(token, 0o644)
    assert cli_opencode_adapter._cmd_install(_install_args(tmp_path, token_file=str(token))) == 2


def test_nested_parsers_register_lifecycle_and_hook_commands() -> None:
    parser = argparse.ArgumentParser()
    root = parser.add_subparsers(dest="root", required=True)
    cli_opencode_adapter.add_opencode_claim_hook_parser(root)
    cli_opencode_adapter.add_opencode_adapter_parser(root)
    hook = parser.parse_args(
        ["opencode-claim-hook", "--identity", "seat/one", "--ready-timeout", "1"]
    )
    install = parser.parse_args(["opencode", "install", "--identity", "seat/one"])
    assert hook.func is cli_opencode_adapter._cmd_opencode_claim_hook
    assert install.func is cli_opencode_adapter._cmd_install
