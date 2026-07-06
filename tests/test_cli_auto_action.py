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


def test_parser_wires_arm_subcommand() -> None:
    args = cli.build_parser().parse_args(["auto-action", "arm", "compact,log", "--store", "/tmp/p"])

    assert args.command == "auto-action"
    assert args.policy_command == "arm"
    assert args.func is cli_auto_action._cmd_arm
    assert args.actions == "compact,log"
    assert args.store == "/tmp/p"


def test_arm_persists_and_is_cumulative(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    store = tmp_path / "policy.json"

    assert cli.main(["auto-action", "arm", "compact", "--store", str(store)]) == 0
    assert cli.main(["auto-action", "arm", "log", "--store", str(store)]) == 0

    out = capsys.readouterr().out
    assert "Armed auto-actions: compact" in out
    assert "Armed auto-actions: compact, log" in out  # the second arm unions, not replaces
    document = json.loads(store.read_text(encoding="utf-8"))
    assert document["armed"] == ["compact", "log"]


def test_show_reflects_the_persisted_policy(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = tmp_path / "policy.json"
    cli.main(["auto-action", "arm", "handover", "--store", str(store)])
    capsys.readouterr()

    assert cli.main(["auto-action", "show", "--store", str(store)]) == 0

    out = capsys.readouterr().out
    assert "persisted" in out
    assert "handover" in out
    assert "(armed)" in out


def test_show_missing_store_arms_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = tmp_path / "absent.json"

    assert cli.main(["auto-action", "show", "--store", str(store)]) == 0

    out = capsys.readouterr().out
    assert "not yet created" in out
    assert "(armed)" not in out


def test_show_json_carries_store_metadata(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = tmp_path / "policy.json"
    cli.main(["auto-action", "arm", "compact", "--store", str(store)])
    capsys.readouterr()

    assert cli.main(["auto-action", "show", "--store", str(store), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["store"] == {"path": str(store), "exists": True}
    assert {entry["action"] for entry in payload["actions"] if entry["armed"]} == {"compact"}


def test_disarm_removes_a_single_action(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    store = tmp_path / "policy.json"
    cli.main(["auto-action", "arm", "compact,log", "--store", str(store)])
    capsys.readouterr()

    assert cli.main(["auto-action", "disarm", "log", "--store", str(store)]) == 0

    assert "Armed auto-actions: compact" in capsys.readouterr().out
    assert json.loads(store.read_text(encoding="utf-8"))["armed"] == ["compact"]


def test_clear_disarms_everything(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    store = tmp_path / "policy.json"
    cli.main(["auto-action", "arm", "compact,log,handover", "--store", str(store)])
    capsys.readouterr()

    assert cli.main(["auto-action", "clear", "--store", str(store)]) == 0

    assert "No auto-actions armed." in capsys.readouterr().out
    assert json.loads(store.read_text(encoding="utf-8"))["armed"] == []


def test_clear_recovers_a_corrupt_store(tmp_path: Path) -> None:
    # clear writes a fresh policy without reading, so it can repair an unreadable file.
    store = tmp_path / "policy.json"
    store.write_text("{not json", encoding="utf-8")

    assert cli.main(["auto-action", "clear", "--store", str(store)]) == 0
    assert json.loads(store.read_text(encoding="utf-8"))["armed"] == []


def test_arm_rejects_unknown_action(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    store = tmp_path / "policy.json"

    assert cli.main(["auto-action", "arm", "teleport", "--store", str(store)]) == 2

    assert "unknown auto-action 'teleport'" in capsys.readouterr().err
    assert not store.exists()  # a rejected selection writes nothing


def test_show_on_corrupt_store_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    store = tmp_path / "policy.json"
    store.write_text(json.dumps({"version": 99, "armed": []}), encoding="utf-8")

    assert cli.main(["auto-action", "show", "--store", str(store)]) == 2

    assert "unsupported version" in capsys.readouterr().err


def test_disarm_on_corrupt_store_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = tmp_path / "policy.json"
    store.write_text("{not json", encoding="utf-8")

    assert cli.main(["auto-action", "disarm", "log", "--store", str(store)]) == 2

    assert "not valid JSON" in capsys.readouterr().err


def test_store_defaults_to_the_coordination_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # With no --store, the policy lands in $SYN_HOME/auto_action_policy.json.
    monkeypatch.setenv("SYN_HOME", str(tmp_path))

    assert cli.main(["auto-action", "arm", "compact"]) == 0

    persisted = tmp_path / "auto_action_policy.json"
    assert persisted.exists()
    assert json.loads(persisted.read_text(encoding="utf-8"))["armed"] == ["compact"]
    assert str(persisted) in capsys.readouterr().out


def test_docs_wire_auto_action_command() -> None:
    combined = "\n".join(
        [
            (REPO_ROOT / "docs" / "cli.md").read_text(encoding="utf-8"),
        ]
    )

    assert "synapse auto-action" in combined
    assert "in-process" in combined  # the honest-scope wording: arming is not a hub-side toggle
