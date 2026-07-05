# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — auto-action reactor introspection CLI regressions

from __future__ import annotations

import json
from pathlib import Path

import pytest

from synapse_channel import cli, cli_auto_action

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_parser_wires_auto_action_command() -> None:
    args = cli.build_parser().parse_args(["auto-action", "--arm", "compact,log"])

    assert args.command == "auto-action"
    assert args.func is cli_auto_action._cmd_auto_action
    assert args.arm == "compact,log"
    assert args.all is False


def test_cli_auto_action_default_arms_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli.main(["auto-action"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "Auto-action reactor" in out
    assert "(available)" in out
    assert "(armed)" not in out  # the default policy arms nothing
    assert "over-budget" in out
    assert "arming alone does not act" in out


def test_cli_auto_action_arms_selected_actions(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli.main(["auto-action", "--arm", "compact,handover"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "compact" in out
    assert "(armed)" in out
    assert "(available)" in out  # log stays available


def test_cli_auto_action_all_arms_everything_as_json(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli.main(["auto-action", "--all", "--json"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert [entry["action"] for entry in payload["actions"]] == ["compact", "log", "handover"]
    assert all(entry["armed"] for entry in payload["actions"])
    assert {entry["signal"] for entry in payload["unmapped_signals"]} == {
        "over-budget",
        "approaching-rate-limit",
    }


def test_cli_auto_action_ignores_empty_arm_segments(capsys: pytest.CaptureFixture[str]) -> None:
    # A doubled or trailing comma leaves an empty segment, which is skipped, not an error.
    exit_code = cli.main(["auto-action", "--arm", "compact,,log,", "--json"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    armed = {entry["action"] for entry in payload["actions"] if entry["armed"]}
    assert armed == {"compact", "log"}


def test_cli_auto_action_rejects_unknown_action(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli.main(["auto-action", "--arm", "nope"])

    assert exit_code == 2
    assert "unknown auto-action 'nope'" in capsys.readouterr().err


def test_cli_auto_action_arm_and_all_are_mutually_exclusive() -> None:
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["auto-action", "--arm", "log", "--all"])


def test_docs_wire_auto_action_command() -> None:
    combined = "\n".join(
        [
            (REPO_ROOT / "docs" / "cli.md").read_text(encoding="utf-8"),
        ]
    )

    assert "synapse auto-action" in combined
    assert "in-process" in combined  # the honest-scope wording: arming is not a hub-side toggle
