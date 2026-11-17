# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — `synapse operation stop` and `resume` over a live hub and their exit codes
"""The commands ask once, print the answer, its sequence and key, and exit 0 only on a record."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

import pytest

from hub_e2e_helpers import AgentHandle, close_agents, connect_agent, running_hub
from synapse_channel import cli_operation_stop as command
from synapse_channel.cli import build_parser
from synapse_channel.core.acl import OPERATION_RESUME, AclPolicy, AclRule
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType

OPERATOR = "H/operator"
WAIT = 15.0


async def _ask_and_see_it_leave(watcher: AgentHandle, **kwargs: Any) -> int:
    """Run the command once and wait until the hub has released the operator's name."""
    earlier = {id(frame) for frame in watcher.recorder.messages}
    code = await command.request_stop(**kwargs)
    await watcher.recorder.wait_for(
        lambda frame: (
            id(frame) not in earlier
            and frame.get("type") == MessageType.PRESENCE_UPDATE
            and frame.get("event") == "left"
            and frame.get("agent") == OPERATOR
        ),
        timeout=WAIT,
    )
    return code


async def test_a_live_hub_records_a_stop_and_a_resume_and_the_commands_say_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = EventStore(tmp_path / "hub.db")
    policy = AclPolicy([AclRule(OPERATION_RESUME, "stop-scope", "*", "H", "persons resume")])
    options: dict[str, Any] = {
        "actor": OPERATOR,
        "token": None,
        "ready_timeout": WAIT,
        "result_timeout": WAIT,
    }
    try:
        async with running_hub(SynapseHub(journal=store, acl_policy=policy)) as (_h, uri):
            watcher = await connect_agent("X/watcher", uri)
            try:
                stop_fields = {"scope": "project:P", "reason": "deploy failed", "idem_key": "s1"}
                stopped = await _ask_and_see_it_leave(
                    watcher,
                    uri=uri,
                    message_type=MessageType.OPERATION_STOP,
                    fields=stop_fields,
                    json_output=False,
                    **options,
                )
                printed = capsys.readouterr().out.splitlines()
                resume_fields = {
                    "scope": "project:P",
                    "reason": "rolled back",
                    "case": "another_person",
                    "idem_key": "r1",
                }
                resumed = await _ask_and_see_it_leave(
                    watcher,
                    uri=uri,
                    message_type=MessageType.OPERATION_RESUME,
                    fields=resume_fields,
                    json_output=True,
                    **options,
                )
                answer = json.loads(capsys.readouterr().out)
            finally:
                await close_agents(watcher)
    finally:
        store.close()
    assert stopped == command.EXIT_RECORDED
    assert printed[0] == (
        "operation stop stop_recorded for project:P: "
        "the scope project:P is stopped; 0 open attempt(s) listed"
    )
    assert re.fullmatch(r"journal seq: \d+", printed[1])
    assert printed[2] == "request key: s1"
    assert resumed == command.EXIT_RECORDED
    assert (answer["answer"], answer["resumed_from"], answer["replayed"]) == (
        "resumed",
        "stop requested",
        False,
    )
    assert answer["lifts_seq"] == int(printed[1].split(": ")[1])


class _FakeAgent:
    """A client that answers a request with a fixed frame, or with nothing."""

    def __init__(self, callback: Any, *, reply: dict[str, Any] | None, ready: bool) -> None:
        self.callback = callback
        self.reply = reply
        self.ready = ready
        self.running = True
        self.last_close_code: int | None = None
        self.last_close_reason = ""

    async def connect(self) -> None:
        """Stay connected until the command cancels this task, as it always does."""
        await asyncio.Event().wait()

    async def wait_until_ready(self, timeout: float) -> bool:
        del timeout
        return self.ready

    async def request_who(self, hub: str | None = None) -> None:
        del hub
        await self.callback({"type": MessageType.WHO_SNAPSHOT})

    async def send_message(self, msg_type: str, **fields: Any) -> None:
        del msg_type
        if self.reply is not None:
            frame = dict(self.reply)
            if frame.get("type") == MessageType.OPERATION_STOP_RESULT:
                frame["request_key"] = fields["idem_key"]
            await self.callback(frame)


async def _run(reply: dict[str, Any] | None, *, ready: bool = True) -> int:
    def factory(_name: str, callback: Any, **_kwargs: Any) -> Any:
        return _FakeAgent(callback, reply=reply, ready=ready)

    return await command.request_stop(
        uri="ws://hub",
        actor=OPERATOR,
        message_type=MessageType.OPERATION_STOP,
        fields={"scope": "installation", "reason": "r", "idem_key": "key-9"},
        token=None,
        ready_timeout=0.1,
        result_timeout=0.1,
        json_output=False,
        agent_factory=factory,
    )


async def test_the_listed_attempts_the_warning_and_each_refusal_are_printed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    recorded = {
        "type": MessageType.OPERATION_STOP_RESULT,
        "answer": "stop_recorded",
        "payload": "the scope installation is stopped; 1 open attempt(s) listed",
        "seq": 12,
        "epoch": 1,
        "listed": [{"operation_id": "op-1", "attempt_id": "a" * 32, "executor": "P/worker"}],
        "warning": "this hub has no access file",
        "live": {"replayed": True},
    }
    assert await _run(recorded) == command.EXIT_RECORDED
    assert capsys.readouterr().out.splitlines() == [
        "operation stop stop_recorded for installation (repeated request): "
        "the scope installation is stopped; 1 open attempt(s) listed",
        "journal seq: 12",
        f"listed attempt {'a' * 32} of op-1 by P/worker",
        "warning: this hub has no access file",
        "request key: key-9",
    ]
    standing = {
        "type": MessageType.OPERATION_STOP_RESULT,
        "answer": "already_stopped",
        "payload": "the scope installation is already stopped (journal seq 12)",
        "standing_seq": 12,
    }
    assert await _run(standing) == command.EXIT_REFUSED
    assert capsys.readouterr().out.splitlines() == [
        "operation stop already_stopped for installation: "
        "the scope installation is already stopped (journal seq 12)",
        "request key: key-9",
    ]


async def test_old_hubs_gate_refusals_and_silence_have_their_own_exit_codes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    unknown = {"type": MessageType.ERROR, "payload": "Unknown message type 'operation_stop'."}
    assert await _run(unknown) == command.EXIT_UNSUPPORTED
    assert capsys.readouterr().out.startswith("operation stop unsupported_by_hub for installation")
    denied = {"type": MessageType.ERROR, "payload": "access denied: operation-stop on x"}
    assert await _run(denied) == command.EXIT_REFUSED
    assert capsys.readouterr().out.startswith("operation stop hub_error for installation: ")
    assert await _run(None) == command.EXIT_NO_VERDICT
    assert capsys.readouterr().out.splitlines() == [
        "operation stop failed: the hub returned no answer",
        "request key: key-9",
    ]
    assert await _run(None, ready=False) == command.EXIT_NO_VERDICT
    assert capsys.readouterr().out.splitlines()[-1] == "request key: key-9"


def test_the_commands_make_a_key_unless_one_is_given(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []

    async def capture(**kwargs: Any) -> int:
        seen.append(kwargs)
        return command.EXIT_RECORDED

    monkeypatch.setattr(command, "request_stop", capture)
    stop = ["operation", "stop", "--name", OPERATOR, "--scope", "effect:file", "--reason", "r"]
    made = build_parser().parse_args(stop)
    assert made.func(made) == command.EXIT_RECORDED
    resume = ["operation", "resume", "--name", OPERATOR, "--scope", "effect:file"]
    given = build_parser().parse_args(
        [*resume, "--reason", "ok", "--case", "only_person", "--idem-key", "mine"]
    )
    assert given.func(given) == command.EXIT_RECORDED
    assert seen[0]["message_type"] == MessageType.OPERATION_STOP
    assert re.fullmatch(r"[0-9a-f]{32}", seen[0]["fields"]["idem_key"])
    assert seen[1]["message_type"] == MessageType.OPERATION_RESUME
    assert seen[1]["fields"] == {
        "scope": "effect:file",
        "reason": "ok",
        "case": "only_person",
        "idem_key": "mine",
    }
    with pytest.raises(SystemExit):
        build_parser().parse_args([*resume, "--reason", "ok", "--case", "someone"])
