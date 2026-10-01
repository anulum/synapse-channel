# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — causal-parent refusal and recovery through real clients
"""Exercise malformed documents and separately injected parser faults on a live hub."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Literal

import pytest

from hub_e2e_helpers import AgentHandle, close_agents, connect_agent, running_hub
from synapse_channel.core.handlers import planning
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.task_causality import TASK_CAUSAL_PARENT_FIELD, TaskCausalParent

_Operation = Literal["declare", "update"]
_DIAGNOSTIC = "PRIVATE_PARSER_CANARY /private/causal-parent.db"
_FINGERPRINT = "a" * 64
_VALID_PARENT = {"hub_id": "west", "seq": 7, "event_fingerprint": _FINGERPRINT}


async def _valid_write(poster: AgentHandle, operation: _Operation) -> None:
    """Retry through the public typed SDK using the same durable operation key."""
    parent = TaskCausalParent.from_value(_VALID_PARENT)
    if operation == "declare":
        await poster.agent.post_task("T", "Recovered", causal_parent=parent, idem_key="retry")
    else:
        await poster.agent.update_ledger_task(
            "T", status="done", causal_parent=parent, idem_key="retry"
        )


async def _refusal_and_recovery(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    operation: _Operation,
    value: object,
    reason: str,
    fault: type[Exception] | None = None,
) -> None:
    """Prove private inert refusal, socket reuse, successful retry, replay and restart.

    Malformed-document cases use the unchanged parser and actual JSON transport.
    Fault cases inject only the parser failure; hub, clients and SQLite are real.
    The distinction prevents injected classification evidence being presented as
    an independently reproduced internal parser defect.
    """
    path = tmp_path / "events.db"
    store = EventStore(path)
    hub = SynapseHub(journal=store, anti_rollback_checkpoint=False)
    accepted = "ledger_task_posted" if operation == "declare" else "ledger_task_updated"
    try:
        async with running_hub(hub) as (_, uri):
            poster = await connect_agent("P", uri)
            observer = await connect_agent("WATCH", uri)
            try:
                if operation == "update":
                    await poster.agent.post_task("T", "Original")
                    await observer.recorder.wait_for(
                        lambda message: message.get("type") == "ledger_task_posted"
                    )
                before_board = hub.blackboard.snapshot()
                before_events = store.read_all()
                before_operations = store.read_operations()
                observer_start = len(observer.recorder.messages)
                with monkeypatch.context() as patch:
                    if fault is not None:

                        def fail(_value: object) -> TaskCausalParent | None:
                            """Inject an unexpected parser diagnostic for boundary testing."""
                            raise fault(_DIAGNOSTIC)

                        patch.setattr(planning, "parse_task_causal_parent", fail)
                    await poster.agent.send_message(
                        "ledger_task" if operation == "declare" else "ledger_task_update",
                        target="System",
                        task_id="T",
                        title="Refused",
                        status="done",
                        causal_parent=value,
                        idem_key="retry",
                    )
                    error = await poster.recorder.wait_for(
                        lambda message: message.get("type") == "error"
                    )
                assert error["payload"] == reason
                assert error["target"] == "P"
                assert _DIAGNOSTIC not in json.dumps(poster.recorder.messages)
                assert hub.blackboard.snapshot() == before_board
                assert store.read_all() == before_events
                assert store.read_operations() == before_operations
                if fault is not None:
                    assert _DIAGNOSTIC in caplog.text
                await poster.agent.chat("after-refusal", target="WATCH")
                await observer.recorder.wait_for(
                    lambda message: message.get("payload") == "after-refusal"
                )
                failed_stream = observer.recorder.messages[observer_start:]
                assert not any(
                    message.get("type") in {"error", accepted} for message in failed_stream
                )
                poster.recorder.messages.clear()
                observer.recorder.messages.clear()
                await _valid_write(poster, operation)
                await observer.recorder.wait_for(lambda message: message.get("type") == accepted)
                recovered_board = hub.blackboard.snapshot()
                recovered_events = store.read_all()
                writes = [
                    event for event in recovered_events if event.kind == EventKind.LEDGER_TASK
                ]
                assert len(writes) == int(operation == "update") + 1
                assert writes[-1].payload[TASK_CAUSAL_PARENT_FIELD] == _VALID_PARENT
                assert len(store.read_operations()) == len(before_operations) + 1
                poster.recorder.messages.clear()
                await _valid_write(poster, operation)
                await poster.recorder.wait_for(lambda message: message.get("type") == accepted)
                await poster.agent.chat("after-replay", target="WATCH")
                await observer.recorder.wait_for(
                    lambda message: message.get("payload") == "after-replay"
                )
                after_replay = store.read_all()
                assert after_replay[:-1] == recovered_events
                assert after_replay[-1].kind == EventKind.CHAT
                assert after_replay[-1].payload["payload"] == "after-replay"
                assert len(store.read_operations()) == len(before_operations) + 1
                assert hub.blackboard.snapshot() == recovered_board
                assert (
                    sum(message.get("type") == accepted for message in observer.recorder.messages)
                    == 1
                )
            finally:
                await close_agents(poster, observer)
    finally:
        store.close()
    reopened = EventStore(path)
    try:
        restarted = SynapseHub(journal=reopened, anti_rollback_checkpoint=False)
        async with running_hub(restarted) as (_, uri):
            reader = await connect_agent("RESTART", uri)
            try:
                await reader.agent.send_message("board_request")
                snapshot = await reader.recorder.wait_for(
                    lambda message: message.get("type") == "board_snapshot"
                )
                assert snapshot["board"] == recovered_board
                assert _DIAGNOSTIC not in json.dumps(reader.recorder.messages)
            finally:
                await close_agents(reader)
    finally:
        reopened.close()


@pytest.mark.parametrize("operation", ["declare", "update"])
@pytest.mark.parametrize(
    ("value", "reason"),
    [
        (5, "causal_parent must be an object"),
        (["private-key"], "causal_parent must be an object"),
        (
            {"private-key": 1},
            "causal_parent must contain exactly hub_id, seq, and event_fingerprint",
        ),
        (
            {**_VALID_PARENT, "seq": True},
            "causal_parent seq must be an integer from 1 through 2^63-1",
        ),
        (
            {**_VALID_PARENT, "seq": "bad"},
            "causal_parent seq must be an integer from 1 through 2^63-1",
        ),
        ({**_VALID_PARENT, "seq": 0}, "causal parent seq must be an integer from 1 through 2^63-1"),
        (
            {**_VALID_PARENT, "seq": 1 << 63},
            "causal parent seq must be an integer from 1 through 2^63-1",
        ),
        (
            {**_VALID_PARENT, "hub_id": 5},
            "causal_parent hub_id and event_fingerprint must be strings",
        ),
        (
            {**_VALID_PARENT, "hub_id": ""},
            "causal parent hub_id must be non-empty and at most 512 bytes",
        ),
        (
            {**_VALID_PARENT, "hub_id": "h" * 513},
            "causal parent hub_id must be non-empty and at most 512 bytes",
        ),
        ({**_VALID_PARENT, "hub_id": "\ud800"}, "causal parent hub_id must be valid UTF-8"),
        (
            {**_VALID_PARENT, "event_fingerprint": "A" * 64},
            "causal parent event_fingerprint must be lowercase SHA-256",
        ),
    ],
)
async def test_malformed_parent_is_privately_refused_and_recovers(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    operation: _Operation,
    value: object,
    reason: str,
) -> None:
    """Run actual malformed JSON without replacing any parser or transport."""
    await _refusal_and_recovery(
        tmp_path, caplog, monkeypatch, operation, value, f"Malformed frame: {reason}"
    )


@pytest.mark.parametrize("operation", ["declare", "update"])
@pytest.mark.parametrize("fault", [ValueError, TypeError, KeyError, RuntimeError])
async def test_unexpected_parser_fault_is_private_and_recoverable(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    operation: _Operation,
    fault: type[Exception],
) -> None:
    """Route a separately injected fault through the real socket and journal workflow."""
    await _refusal_and_recovery(
        tmp_path,
        caplog,
        monkeypatch,
        operation,
        _VALID_PARENT,
        "Causal parent validation failed; task was not changed.",
        fault,
    )


@pytest.mark.parametrize("operation", ["declare", "update"])
@pytest.mark.parametrize("raw_seq", ["PRIVATE_INT_CANARY", "0", "1" * 4500])
def test_real_cli_refuses_parent_without_interpreter_text(operation: str, raw_seq: str) -> None:
    """Actual CLI conversion/size errors retain the authored reason and exit2."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "synapse_channel.cli",
            "task",
            operation,
            "T",
            "--causal-parent",
            f"west:{raw_seq}:{_FINGERPRINT}",
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 2
    assert (
        "argument --causal-parent: causal parent seq must be an integer from 1 through 2^63-1"
        in result.stderr
    )
    assert "PRIVATE_INT_CANARY" not in result.stderr
    assert "invalid literal" not in result.stderr
    assert "Exceeds the limit" not in result.stderr
