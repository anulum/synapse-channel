# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — staged claim-check CLI tests

from __future__ import annotations

import argparse
from typing import Any

import pytest

from synapse_channel import cli
from synapse_channel.cli_git_claim_check import _cmd_git_claim_check, _phase_timeout
from synapse_channel.git.staged_claim_check import StagedClaimCheckResult


def _namespace() -> argparse.Namespace:
    return argparse.Namespace(name="agent", uri="ws://hub", token_file="/token", timeout=3.0)


def test_parser_requires_staged_mode_and_has_no_inline_token() -> None:
    parser = cli.build_parser(command="git-claim-check")
    args = parser.parse_args(["git-claim-check", "--staged", "--name", "agent"])
    assert args.name == "agent"
    assert args.uri is None
    assert parser._subparsers is not None
    action = parser._subparsers._group_actions[0]
    assert isinstance(action, argparse._SubParsersAction)
    options = {
        option
        for parser_action in action.choices["git-claim-check"]._actions
        for option in parser_action.option_strings
    }
    assert "--token-file" in options
    assert "--token" not in options
    with pytest.raises(SystemExit):
        parser.parse_args(["git-claim-check"])


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "301", "not-a-number"])
def test_timeout_parser_refuses_unbounded_values(value: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        _phase_timeout(value)


def test_timeout_parser_accepts_the_closed_interval() -> None:
    assert _phase_timeout("0.1") == 0.1
    assert _phase_timeout("300") == 300.0


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (StagedClaimCheckResult(True, ()), "no staged paths"),
        (StagedClaimCheckResult(True, ("a", "b")), "OK (2 paths)"),
    ],
)
def test_command_reports_success(
    result: StagedClaimCheckResult, expected: str, capsys: pytest.CaptureFixture[str]
) -> None:
    captured: dict[str, Any] = {}

    async def checker(**kwargs: Any) -> StagedClaimCheckResult:
        captured.update(kwargs)
        return result

    assert _cmd_git_claim_check(_namespace(), checker=checker) == 0
    assert expected in capsys.readouterr().out
    assert captured == {
        "identity": "agent",
        "uri": "ws://hub",
        "token_file": "/token",
        "timeout": 3.0,
    }


def test_command_denies_with_repair_instruction(capsys: pytest.CaptureFixture[str]) -> None:
    async def checker(**kwargs: Any) -> StagedClaimCheckResult:
        return StagedClaimCheckResult(False, ("a.py",), 'no covering claim: "a.py"')

    assert _cmd_git_claim_check(_namespace(), checker=checker) == 1
    error = capsys.readouterr().err
    assert "staged claim coverage denied" in error
    assert "synapse git-init --name <exact-owner>" in error


def test_lazy_owner_is_the_dedicated_small_module() -> None:
    assert cli._unit_owning("git-claim-check") == "synapse_channel.cli_git_claim_check:add_parser"
