# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — the opt-in hub path of ops run, against a scripted hub
"""Declare, attempt, run and publish through ``synapse ops run --hub``.

The vendor program, catalogue, grants and journal are real files; the hub's
answers are scripted, so every branch of the path is exercised. The real-hub
journeys are in ``test_cli_ops_hub_real.py``.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import pytest

import synapse_channel.cli_ops_hub as hub_path
from synapse_channel.cli import main
from synapse_channel.cli_hub_request import HubReply
from synapse_channel.core.authorisation_intent import compose_intent, parse_intent_statement
from synapse_channel.core.protocol import MessageType
from synapse_channel.operations.hub_intent import hub_intent
from synapse_channel.operations.runner import (
    OperationRunRefused,
    OperationTarget,
    RunProfile,
    RunReport,
    plan,
)
from test_operations_runner import World

DEADLINE = int(time.time()) + 86_400
"""A day ahead: the hub refuses an intent whose deadline lies more than a year ahead."""


class ScriptedHub:
    """Answer each request with the next scripted frame, and keep every request."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.answers: list[dict[str, Any] | None] = []
        self.requests: list[tuple[str, str, dict[str, Any]]] = []
        monkeypatch.setattr(hub_path, "ask_hub", self.ask)

    async def ask(self, **kw: Any) -> HubReply:
        """Record the request and return the next answer, stamped to match it."""
        fields = kw["fields"]
        self.requests.append((kw["actor"], kw["message_type"], fields))
        answer = self.answers.pop(0)
        if answer is None:
            return HubReply(None)
        stamped = {
            **answer,
            "request_id": fields.get("request_id"),
            "request_key": fields.get("idem_key"),
        }
        assert kw["answers"](stamped)
        return HubReply(stamped)


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
    return World(tmp_path, monkeypatch)


@pytest.fixture
def hub(monkeypatch: pytest.MonkeyPatch) -> ScriptedHub:
    return ScriptedHub(monkeypatch)


def _command(world: World, *extra: str) -> list[str]:
    """Return the setting write of the fake vendor through the hub, plus ``extra``."""
    return [
        "ops", "run", "setting.write",
        "--host", "workstation", "--vendor", "acme", "--component", "cli",
        "--component-version", "1.0.0", "--os", "linux",
        "--arguments", json.dumps({"key": "mode", "value": "strict"}),
        "--operation-id", "op-1", "--subject", "PROJECT/seat",
        "--grants", str(world.grants), "--profile", "personal",
        "--journal", str(world.journal), "--catalogue", str(world.catalogue_file),
        "--verify-file", str(world.config), "--verify-sha256", world.written("strict"),
        *extra,
    ]  # fmt: skip


def _hub(*extra: str) -> list[str]:
    return [
        "--hub",
        "ws://scripted",
        "--task",
        "task-1",
        "--epoch",
        "3",
        "--intent-deadline",
        str(DEADLINE),
        *extra,
    ]


def _approved_digest(world: World) -> str:
    """Return the digest the hub composes for the planned write, as an approver sees it."""
    run = plan(
        world.catalogue,
        OperationTarget("setting.write", "workstation", "acme", "cli", "1.0.0", "linux"),
        {"key": "mode", "value": "strict"},
        profile=RunProfile("personal"),
    )
    intent = hub_intent(
        run,
        path=str(world.config),
        before="absent",
        after_sha256=world.written("strict"),
        deadline=DEADLINE,
    )
    return compose_intent(
        parse_intent_statement(intent.statement, "file"),
        task_id="task-1",
        operation_id="op-1",
        effect="file",
        target=intent.effect_target,
        actor="PROJECT/seat",
    ).sha256


def _output(capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    captured = capsys.readouterr()
    lines = captured.out.splitlines()
    assert len(lines) == 1, captured.out
    result: dict[str, Any] = json.loads(lines[0])
    return result


def test_declare_sends_the_intent_and_runs_nothing(
    world: World, hub: ScriptedHub, capsys: pytest.CaptureFixture[str]
) -> None:
    hub.answers = [
        {
            "type": MessageType.OPERATION_RECORD_RESULT,
            "status": "accepted",
            "intent_sha256": "f" * 64,
        }
    ]

    assert main(_command(world, *_hub("--declare"))) == 0
    answer = _output(capsys)
    actor, message_type, fields = hub.requests[0]
    assert (answer["status"], answer["intent_sha256"]) == ("accepted", "f" * 64)
    assert (actor, message_type, fields["record"], fields["epoch"]) == (
        "PROJECT/seat",
        MessageType.OPERATION_RECORD,
        "declare",
        3,
    )
    assert fields["effect_target"] == {"path": str(world.config), "sha256": world.written("strict")}
    assert fields["intent"]["pre_state"] == "absent"
    assert world.invocations() == []


@pytest.mark.parametrize(
    ("answer", "status", "printed"),
    [
        (None, 2, "no_verdict"),
        (
            {
                "type": MessageType.OPERATION_RECORD_RESULT,
                "status": "refused",
                "reason": "epoch_stale",
            },
            1,
            "refused",
        ),
        ({"type": MessageType.OPERATION_RECORD_RESULT, "status": "accepted"}, 1, "accepted"),
    ],
)
def test_a_declaration_without_a_kept_intent_is_not_success(
    world: World, hub: ScriptedHub, capsys: pytest.CaptureFixture[str],
    answer: dict[str, Any] | None, status: int, printed: str,
) -> None:  # fmt: skip
    hub.answers = [answer]

    assert main(_command(world, *_hub("--declare"))) == status
    assert _output(capsys)["status"] == printed


def test_an_approved_attempt_runs_once_and_publishes_the_outcome(
    world: World, hub: ScriptedHub, capsys: pytest.CaptureFixture[str]
) -> None:
    hub.answers = [
        {
            "type": MessageType.OPERATION_ATTEMPT_RESULT,
            "answer": "approved_fresh",
            "attempt_id": "att-1",
            "live": {"attempt_state": "open"},
        },
        {"type": MessageType.OPERATION_RECORD_RESULT, "status": "accepted"},
    ]

    assert main(_command(world, *_hub("--intent-sha256", _approved_digest(world)))) == 0
    report = _output(capsys)
    assert world.invocations() == ["setting set mode strict"]
    assert report["hub_governed"] is True
    assert report["authority"].endswith("; hub_attempt:att-1")
    assert report["outcome_published"] == {"status": "accepted", "reason": ""}
    _actor, _type, attempt = hub.requests[0]
    _actor, _type, outcome = hub.requests[1]
    assert attempt["intent_sha256"] == _approved_digest(world) and attempt["epoch"] == 3
    assert (outcome["record"], outcome["report"]["state"]) == ("outcome", "confirmed_done")
    assert outcome["report"]["external_effect"] == "confirmed_done"


def test_a_changed_digest_or_missing_grant_refuses_before_the_hub_is_asked(
    world: World, hub: ScriptedHub, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(_command(world, *_hub("--intent-sha256", "0" * 64))) == 2
    assert "changed since the intent was declared" in capsys.readouterr().err
    world.grant(("operation-read",))
    assert main(_command(world, *_hub("--intent-sha256", _approved_digest(world)))) == 2
    assert "holds no unexpired grant" in capsys.readouterr().err
    assert hub.requests == [] and world.invocations() == []


@pytest.mark.parametrize(
    ("answer", "status"),
    [
        (None, 3),
        ({"type": MessageType.OPERATION_ATTEMPT_RESULT, "answer": "approval_missing"}, 1),
        (
            {
                "type": MessageType.OPERATION_ATTEMPT_RESULT,
                "answer": "approved_fresh",
                "live": {"attempt_state": "sealed"},
            },
            1,
        ),
    ],
)
def test_an_attempt_that_does_not_open_runs_nothing(
    world: World, hub: ScriptedHub, capsys: pytest.CaptureFixture[str],
    answer: dict[str, Any] | None, status: int,
) -> None:  # fmt: skip
    hub.answers = [answer]

    assert (
        main(
            _command(world, *_hub("--intent-sha256", _approved_digest(world), "--idem-key", "k-1"))
        )
        == status
    )
    assert _output(capsys)["attempt"]["request_key"] == "k-1"
    assert world.invocations() == []
    assert len(hub.requests) == 1


_OPENED = {
    "type": MessageType.OPERATION_ATTEMPT_RESULT,
    "answer": "approved_fresh",
    "attempt_id": "a",
    "live": {"attempt_state": "open"},
}


def test_an_outcome_the_hub_did_not_take_leaves_the_run_unknown(
    world: World, hub: ScriptedHub, capsys: pytest.CaptureFixture[str]
) -> None:
    hub.answers = [_OPENED, None]

    assert main(_command(world, *_hub("--intent-sha256", _approved_digest(world)))) == 3
    assert _output(capsys)["outcome_published"]["status"] == "no_verdict"
    assert world.invocations() == ["setting set mode strict"]


def test_a_refusal_after_the_attempt_opened_publishes_not_started(
    world: World,
    hub: ScriptedHub,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(*_args: Any, **_kwargs: Any) -> Any:
        raise OperationRunRefused("the program was replaced since planning")

    monkeypatch.setattr(hub_path, "execute", refuse)
    hub.answers = [_OPENED, {"type": MessageType.OPERATION_RECORD_RESULT, "status": "accepted"}]

    assert main(_command(world, *_hub("--intent-sha256", _approved_digest(world)))) == 2
    late = _output(capsys)
    assert late["refused"] == "the program was replaced since planning"
    outcome = hub.requests[-1][2]["report"]
    assert (outcome["state"], outcome["external_effect"]) == ("not_started", "not_started")
    assert world.invocations() == []


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        (("--task", "task-1"), "--task needs --hub"),
        (("--declare",), "--declare needs --hub"),
        (("--hub", "ws://scripted", "--declare"), "needs --task and --epoch"),
        (("--hub", "ws://scripted", "--task", "t", "--epoch", "1"), "exactly one of --declare"),
        (
            ("--hub", "ws://scripted", "--task", "t", "--epoch", "1", "--declare"),
            "--intent-deadline",
        ),
    ],
)
def test_incomplete_hub_options_are_refused(
    world: World,
    hub: ScriptedHub,
    capsys: pytest.CaptureFixture[str],
    extra: tuple[str, ...],
    message: str,
) -> None:
    assert main(_command(world, *extra)) == 2
    assert message in capsys.readouterr().err
    assert hub.requests == [] and world.invocations() == []


def test_a_run_outside_the_hub_says_so(world: World, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(_command(world)) == 0
    assert _output(capsys)["hub_governed"] is False


def test_the_files_state_is_its_digest_or_absent_and_a_directory_is_refused(tmp_path: Path) -> None:
    assert hub_path.file_state(tmp_path / "none") == "absent"
    (tmp_path / "f").write_bytes(b"x")
    assert (
        hub_path.file_state(tmp_path / "f")
        == "2d711642b726b04401627ca9fbac32f5c8530fb1903cc4db02258717921a4881"
    )
    with pytest.raises(OperationRunRefused, match="cannot be read"):
        hub_path.file_state(tmp_path)
    assert os.path.isdir(tmp_path)


def test_the_outcome_names_an_effect_only_its_verifier_saw() -> None:
    """A finished run with no verified effect is published with the effect unknown."""
    failed = hub_path.outcome_document(RunReport("op-9", "confirmed_failed", ""))

    assert (failed["state"], failed["external_effect"]) == ("confirmed_failed", "unknown")
    assert failed["observation"] == "confirmed_failed"
    assert failed["schema"] == "synapse.execution-outcome.v1"
