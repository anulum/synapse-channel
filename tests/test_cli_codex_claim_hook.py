# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Codex claim-hook CLI and config regressions

from __future__ import annotations

import argparse
import io
import json
import shlex
import sys
from pathlib import Path

import pytest

from synapse_channel import cli_codex_claim_hook as hook_cli
from synapse_channel.claim_state import fetch_state_snapshot
from synapse_channel.cli_adapters import add_parsers
from synapse_channel.file_claim_guard import GuardVerdict


def _args(*argv: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    add_parsers(parser.add_subparsers())
    return parser.parse_args(["adapters", "codex-claim-hook", *argv])


def test_render_config_is_scoped_shell_safe_and_token_safe(tmp_path: Path) -> None:
    token_file = tmp_path / "hub token"
    token_file.write_text("do-not-embed", encoding="utf-8")
    config = hook_cli.render_hook_config(
        identity="seat/one",
        uri="wss://hub.example/ws",
        ready_timeout=2.0,
        token_file=str(token_file),
        synapse_bin=sys.executable,
    )
    group = config["hooks"]["PreToolUse"][0]
    assert group["matcher"] == "Edit|Write"
    hook = group["hooks"][0]
    argv = shlex.split(hook["command"])
    assert argv[:3] == [str(Path(sys.executable).resolve()), "adapters", "codex-claim-hook"]
    assert argv[argv.index("--token-file") + 1] == str(token_file.resolve())
    assert "do-not-embed" not in json.dumps(config)
    assert hook["timeout"] > 4


@pytest.mark.parametrize("value", ["nan", "inf", "0", "300", "not-a-number"])
def test_parser_rejects_unbounded_ready_timeout(value: str) -> None:
    with pytest.raises(SystemExit):
        _args("--identity", "seat/one", "--ready-timeout", value)


def test_runtime_exception_emits_structured_denial_on_exit_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def broken(*_args: object, **_kwargs: object) -> GuardVerdict:
        raise RuntimeError("secret failure")

    monkeypatch.setattr(hook_cli, "_evaluate", broken)
    monkeypatch.setattr(sys, "stdin", io.StringIO("{}"))
    assert hook_cli._cmd_codex_claim_hook(_args("--identity", "seat/one")) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "secret failure" not in json.dumps(output)


def test_runtime_allow_is_silent(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def allow(*_args: object, **_kwargs: object) -> GuardVerdict:
        return GuardVerdict(True)

    monkeypatch.setattr(hook_cli, "_evaluate", allow)
    monkeypatch.setattr(sys, "stdin", io.StringIO("{}"))
    assert hook_cli._cmd_codex_claim_hook(_args("--identity", "seat/one")) == 0
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""


@pytest.mark.asyncio
async def test_evaluator_forwards_to_codex_guard(monkeypatch: pytest.MonkeyPatch) -> None:
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
    monkeypatch.chdir(tmp_path)
    good = _args("--identity", "seat/one", "--print-config", "--synapse-bin", sys.executable)
    assert hook_cli._cmd_codex_claim_hook(good) == 0
    assert json.loads(capsys.readouterr().out)["hooks"]["PreToolUse"]
    assert list(tmp_path.iterdir()) == []

    bad = _args(
        "--identity",
        "seat/one",
        "--print-config",
        "--synapse-bin",
        str(tmp_path / "missing"),
    )
    assert hook_cli._cmd_codex_claim_hook(bad) == 2
    assert "cannot resolve" in capsys.readouterr().err


def test_print_config_rejects_raw_token_without_echoing_it(
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = _args("--identity", "seat/one", "--print-config", "--token", "raw-secret")
    assert hook_cli._cmd_codex_claim_hook(args) == 2
    error = capsys.readouterr().err
    assert "use --token-file" in error
    assert "raw-secret" not in error
