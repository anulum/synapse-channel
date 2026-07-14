# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Claude claim-hook CLI and config recipe regressions

from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

import pytest

from synapse_channel import cli as cli_module
from synapse_channel import cli_claude_claim_hook as hook_cli
from synapse_channel.claude_claim_guard import GuardVerdict
from synapse_channel.cli_adapters import add_parsers
from synapse_channel.cli_claude_claim_hook import _cmd_claude_claim_hook, render_hook_config


def _args(*argv: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    add_parsers(parser.add_subparsers())
    return parser.parse_args(["adapters", "claude-claim-hook", *argv])


def test_nested_parser_requires_identity() -> None:
    with pytest.raises(SystemExit):
        _args("--print-config")
    args = _args("--identity", "seat/one")
    assert args.identity == "seat/one"
    assert args.ready_timeout == 2.0


@pytest.mark.parametrize("value", ["inf", "-inf", "nan", "0", "1e308", "not-a-number"])
def test_nested_parser_rejects_invalid_ready_timeout(value: str) -> None:
    with pytest.raises(SystemExit) as raised:
        _args("--identity", "seat/one", "--ready-timeout", value)
    assert raised.value.code == 2


def test_render_hook_config_is_exec_form_scoped_and_token_safe(tmp_path: Path) -> None:
    token_file = tmp_path / "hub.token"
    token_file.write_text("do-not-embed", encoding="utf-8")
    token_file.chmod(0o600)
    config = render_hook_config(
        identity="seat/one",
        uri="wss://hub.example/ws",
        ready_timeout=2.0,
        token_file=str(token_file),
        synapse_bin=sys.executable,
    )
    group = config["hooks"]["PreToolUse"][0]
    assert group["matcher"] == "Edit|Write"
    hook = group["hooks"][0]
    assert hook["type"] == "command"
    assert hook["command"] == str(Path(sys.executable).resolve())
    assert hook["timeout"] > 4
    command_args = hook["args"]
    assert command_args[:2] == ["adapters", "claude-claim-hook"]
    assert command_args[command_args.index("--identity") + 1] == "seat/one"
    assert command_args[command_args.index("--token-file") + 1] == str(token_file.resolve())
    assert "do-not-embed" not in json.dumps(config)
    assert "allow" not in json.dumps(config)


@pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan"), 0.0, 1e308])
def test_render_hook_config_rejects_unbounded_timeout(value: float) -> None:
    with pytest.raises(ValueError, match="finite"):
        render_hook_config(
            identity="seat/one",
            uri="ws://hub",
            ready_timeout=value,
            token_file=None,
            synapse_bin=sys.executable,
        )


def test_main_resolves_nested_hook_token_file_before_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token_file = tmp_path / "hub.token"
    token_file.write_text("secured-token\n", encoding="utf-8")
    token_file.chmod(0o600)
    observed: dict[str, str | None] = {}

    def capture(args: argparse.Namespace) -> int:
        observed["token"] = args.token
        observed["token_file"] = args.token_file
        return 0

    monkeypatch.setattr(hook_cli, "_cmd_claude_claim_hook", capture)
    assert (
        cli_module.main(
            [
                "adapters",
                "claude-claim-hook",
                "--identity",
                "seat/one",
                "--token-file",
                str(token_file),
            ]
        )
        == 0
    )
    assert observed == {"token": "secured-token", "token_file": str(token_file)}


def test_runtime_success_is_silent(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def allow(*_args: object, **_kwargs: object) -> GuardVerdict:
        return GuardVerdict(True)

    monkeypatch.setattr(sys, "stdin", io.StringIO("{}"))
    args = _args("--identity", "seat/one")
    assert _cmd_claude_claim_hook(args, evaluator=allow) == 0
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""


def test_runtime_denial_is_structured_json_on_exit_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def deny(*_args: object, **_kwargs: object) -> GuardVerdict:
        return GuardVerdict(False, "claim required")

    monkeypatch.setattr(sys, "stdin", io.StringIO("{}"))
    args = _args("--identity", "seat/one")
    assert _cmd_claude_claim_hook(args, evaluator=deny) == 0
    output = json.loads(capsys.readouterr().out)
    specific = output["hookSpecificOutput"]
    assert specific["hookEventName"] == "PreToolUse"
    assert specific["permissionDecision"] == "deny"
    assert specific["permissionDecisionReason"] == "claim required"


def test_runtime_exception_fails_closed_without_exit_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def broken(*_args: object, **_kwargs: object) -> GuardVerdict:
        raise RuntimeError("boom")

    monkeypatch.setattr(sys, "stdin", io.StringIO("{}"))
    args = _args("--identity", "seat/one")
    assert _cmd_claude_claim_hook(args, evaluator=broken) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "boom" not in json.dumps(output)


@pytest.mark.parametrize("value", [float("inf"), 1e308])
def test_runtime_unbounded_timeout_fails_closed_before_evaluation(
    value: float,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def must_not_run(*_args: object, **_kwargs: object) -> GuardVerdict:
        raise AssertionError("invalid timeout must fail before evaluation")

    monkeypatch.setattr(sys, "stdin", io.StringIO("{}"))
    args = _args("--identity", "seat/one")
    args.ready_timeout = value
    assert _cmd_claude_claim_hook(args, evaluator=must_not_run) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_print_config_writes_only_stdout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    args = _args(
        "--identity",
        "seat/one",
        "--print-config",
        "--synapse-bin",
        sys.executable,
    )
    assert _cmd_claude_claim_hook(args) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["hooks"]["PreToolUse"][0]["matcher"] == "Edit|Write"
    assert list(tmp_path.iterdir()) == []


def test_print_config_rejects_unresolvable_executable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = _args(
        "--identity",
        "seat/one",
        "--print-config",
        "--synapse-bin",
        str(tmp_path / "missing-synapse"),
    )
    assert _cmd_claude_claim_hook(args) == 2
    assert "cannot resolve" in capsys.readouterr().err
