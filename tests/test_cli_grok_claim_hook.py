# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Grok claim-hook CLI and config recipe regressions

from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

import pytest

from synapse_channel import cli as cli_module
from synapse_channel import cli_grok_claim_hook as hook_cli
from synapse_channel.claim_state import fetch_state_snapshot
from synapse_channel.cli_adapters import add_parsers
from synapse_channel.cli_grok_claim_hook import _cmd_grok_claim_hook, render_hook_config
from synapse_channel.file_claim_guard import GuardVerdict


def _args(*argv: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    add_parsers(parser.add_subparsers())
    return parser.parse_args(["adapters", "grok-claim-hook", *argv])


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


def test_render_hook_config_is_shell_command_and_token_safe(tmp_path: Path) -> None:
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
    assert "search_replace" in group["matcher"]
    assert "write" in group["matcher"]
    hook = group["hooks"][0]
    assert hook["type"] == "command"
    command = hook["command"]
    assert "grok-claim-hook" in command
    assert "seat/one" in command
    assert str(token_file.resolve()) in command
    assert "do-not-embed" not in json.dumps(config)
    assert '"decision": "allow"' not in json.dumps(config)
    assert hook["timeout"] > 4


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


def test_runtime_success_is_silent(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def allow(*_args: object, **_kwargs: object) -> GuardVerdict:
        return GuardVerdict(True)

    monkeypatch.setattr(hook_cli, "_evaluate", allow)
    monkeypatch.setattr(sys, "stdin", io.StringIO("{}"))
    args = _args("--identity", "seat/one")
    assert _cmd_grok_claim_hook(args) == 0
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""


@pytest.mark.asyncio
async def test_evaluate_wrapper_uses_authoritative_state_fetcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    async def capture(raw: str, **kwargs: object) -> GuardVerdict:
        observed["raw"] = raw
        observed.update(kwargs)
        return GuardVerdict(True)

    monkeypatch.setattr(hook_cli, "evaluate_hook_event", capture)
    verdict = await hook_cli._evaluate(
        "{}",
        identity="seat/one",
        uri="ws://hub",
        token="token",
        timeout=1.5,
    )
    assert verdict.allowed
    assert observed["raw"] == "{}"
    assert observed["identity"] == "seat/one"
    assert observed["state_fetcher"] is fetch_state_snapshot


def test_runtime_denial_is_grok_native_json_on_exit_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def deny(*_args: object, **_kwargs: object) -> GuardVerdict:
        return GuardVerdict(False, "claim required")

    monkeypatch.setattr(hook_cli, "_evaluate", deny)
    monkeypatch.setattr(sys, "stdin", io.StringIO("{}"))
    args = _args("--identity", "seat/one")
    assert _cmd_grok_claim_hook(args) == 0
    output = json.loads(capsys.readouterr().out)
    assert output == {"decision": "deny", "reason": "claim required"}
    assert "permissionDecision" not in output
    assert "hookSpecificOutput" not in output


def test_runtime_exception_fails_closed_without_exit_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def broken(*_args: object, **_kwargs: object) -> GuardVerdict:
        raise RuntimeError("boom")

    monkeypatch.setattr(hook_cli, "_evaluate", broken)
    monkeypatch.setattr(sys, "stdin", io.StringIO("{}"))
    args = _args("--identity", "seat/one")
    assert _cmd_grok_claim_hook(args) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["decision"] == "deny"
    assert "boom" not in json.dumps(output)
    assert "Synapse claim verification failed" in output["reason"]


@pytest.mark.parametrize("value", [float("inf"), 1e308])
def test_runtime_unbounded_timeout_fails_closed_before_evaluation(
    value: float,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def must_not_run(*_args: object, **_kwargs: object) -> GuardVerdict:
        raise AssertionError("invalid timeout must fail before evaluation")

    monkeypatch.setattr(hook_cli, "_evaluate", must_not_run)
    monkeypatch.setattr(sys, "stdin", io.StringIO("{}"))
    args = _args("--identity", "seat/one")
    args.ready_timeout = value
    assert _cmd_grok_claim_hook(args) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["decision"] == "deny"


def test_token_file_is_read_for_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    token_file = tmp_path / "hub.token"
    token_file.write_text("secured-token\n", encoding="utf-8")
    token_file.chmod(0o600)
    seen: dict[str, str | None] = {}

    async def capture(*_args: object, **kwargs: object) -> GuardVerdict:
        raw = kwargs.get("token")
        seen["token"] = raw if isinstance(raw, str) else None
        return GuardVerdict(True)

    monkeypatch.setattr(sys, "stdin", io.StringIO("{}"))
    monkeypatch.setattr(hook_cli, "_evaluate", capture)
    assert (
        cli_module.main(
            [
                "adapters",
                "grok-claim-hook",
                "--identity",
                "seat/one",
                "--token-file",
                str(token_file),
            ]
        )
        == 0
    )
    assert seen["token"] == "secured-token"
    assert capsys.readouterr().out == ""


def test_unreadable_token_file_is_refused_before_hook_evaluation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "missing.token"

    async def must_not_run(*_args: object, **_kwargs: object) -> GuardVerdict:
        raise AssertionError("an unreadable token file must stop before evaluation")

    monkeypatch.setattr(hook_cli, "_evaluate", must_not_run)
    monkeypatch.setattr(sys, "stdin", io.StringIO("{}"))
    assert (
        cli_module.main(
            [
                "adapters",
                "grok-claim-hook",
                "--identity",
                "seat/one",
                "--token-file",
                str(missing),
            ]
        )
        == 2
    )
    assert "cannot read token file" in capsys.readouterr().err


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
    assert _cmd_grok_claim_hook(args) == 0
    output = json.loads(capsys.readouterr().out)
    assert "PreToolUse" in output["hooks"]
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
    assert _cmd_grok_claim_hook(args) == 2
    assert "cannot resolve" in capsys.readouterr().err


def test_print_config_rejects_raw_token(capsys: pytest.CaptureFixture[str]) -> None:
    args = _args(
        "--identity",
        "seat/one",
        "--print-config",
        "--token",
        "must-not-persist",
        "--synapse-bin",
        sys.executable,
    )
    assert _cmd_grok_claim_hook(args) == 2
    assert "never embed --token" in capsys.readouterr().err


def test_main_dispatch_nested_hook(monkeypatch: pytest.MonkeyPatch) -> None:
    observed: dict[str, str] = {}

    def capture(args: argparse.Namespace) -> int:
        observed["identity"] = args.identity
        return 0

    monkeypatch.setattr(hook_cli, "_cmd_grok_claim_hook", capture)
    assert (
        cli_module.main(
            [
                "adapters",
                "grok-claim-hook",
                "--identity",
                "seat/one",
            ]
        )
        == 0
    )
    assert observed == {"identity": "seat/one"}
