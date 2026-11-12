# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — `synapse operation attempt` over a live hub and its exit codes
"""The command asks once, prints its key on every call, and exits 0 only on an open attempt."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

import pytest

import operation_attempt_scenario as scene
from hub_e2e_helpers import AgentHandle, close_agents, connect_agent, running_hub
from synapse_channel import cli_operation_attempt as command
from synapse_channel.cli import build_parser
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType


async def _ask_and_see_it_leave(
    watcher: AgentHandle, options: dict[str, Any], fields: dict[str, Any], *, json_output: bool
) -> int:
    """Run the command once and wait until the hub has released its name.

    Each call connects as the worker; the next call under that name is refused
    while the hub still holds the previous session, so the watcher waits for the
    hub's own announcement that the name left.
    """
    earlier = {id(frame) for frame in watcher.recorder.messages}
    code = await command.request_attempt(fields=fields, json_output=json_output, **options)
    await watcher.recorder.wait_for(
        lambda frame: id(frame) not in earlier and _worker_left(frame), timeout=scene.WAIT
    )
    return code


def _worker_left(frame: dict[str, Any]) -> bool:
    return (
        frame.get("type") == MessageType.PRESENCE_UPDATE
        and frame.get("event") == "left"
        and frame.get("agent") == scene.WORKER
    )


def _fields(epoch: int, digest: str, key: str) -> dict[str, Any]:
    return {
        "task_id": scene.TASK,
        "operation_id": scene.OPERATION,
        "epoch": epoch,
        "intent_sha256": digest,
        "idem_key": key,
    }


async def test_a_live_hub_opens_once_and_the_command_says_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(SynapseHub(journal=store, acl_policy=scene.policy())) as (_h, uri):
            worker = await connect_agent(scene.WORKER, uri)
            reviewer = await connect_agent(scene.REVIEWER, uri)
            approver = await connect_agent(scene.APPROVER, uri)
            watcher = await connect_agent("X/watcher", uri)
            try:
                epoch = await scene.claim(worker)
                digest = await scene.declare(worker, epoch, intent=scene.statement())
                await scene.review(reviewer, digest)
                await scene.approve(approver, digest)
            finally:
                await close_agents(worker, reviewer, approver)
            options: dict[str, Any] = {
                "uri": uri,
                "actor": scene.WORKER,
                "token": None,
                "ready_timeout": scene.WAIT,
                "result_timeout": scene.WAIT,
            }
            try:
                # The worker's own session must be gone before the command takes its name.
                await watcher.recorder.wait_for(_worker_left, timeout=scene.WAIT)
                opened = await _ask_and_see_it_leave(
                    watcher, options, _fields(epoch, digest, "k1"), json_output=False
                )
                printed = capsys.readouterr().out.splitlines()
                again = await _ask_and_see_it_leave(
                    watcher, options, _fields(epoch, digest, "k1"), json_output=True
                )
                replayed_out = capsys.readouterr().out
                assert again == command.EXIT_OPEN, replayed_out
                replay = json.loads(replayed_out)
                refused = await _ask_and_see_it_leave(
                    watcher, options, _fields(epoch, digest, "k2"), json_output=False
                )
                refusal = capsys.readouterr().out.splitlines()
            finally:
                await close_agents(watcher)
    finally:
        store.close()
    assert opened == command.EXIT_OPEN
    assert printed[0] == f"operation attempt approved_fresh for {scene.OPERATION}: attempt opened"
    assert re.fullmatch(r"attempt [0-9a-f]{32} is open", printed[1])
    assert printed[2] == "request key: k1"
    assert again == command.EXIT_OPEN
    assert (replay["answer"], replay["attempt_state"], replay["replayed"]) == (
        "approved_fresh",
        "open",
        True,
    )
    assert replay["request_key"] == "k1" and isinstance(replay["approval_seq"], int)
    assert refused == command.EXIT_REFUSED
    assert refusal == [
        f"operation attempt attempt_exists for {scene.OPERATION}: "
        "an attempt of the operation is open",
        "request key: k2",
    ]


class _FakeAgent:
    """A client that answers a request with a fixed frame, or with nothing."""

    def __init__(
        self,
        callback: Any,
        *,
        reply: dict[str, Any] | None,
        ready: bool,
        close_code: int | None = None,
    ) -> None:
        self.callback = callback
        self.reply = reply
        self.ready = ready
        self.running = True
        self.last_close_code = close_code
        self.last_close_reason = "" if close_code is None else "name in use"
        self.sent: list[str] = []

    async def connect(self) -> None:
        """Stay connected until the command cancels this task, as it always does."""
        await asyncio.Event().wait()

    async def wait_until_ready(self, timeout: float) -> bool:
        del timeout
        return self.ready

    async def request_who(self, hub: str | None = None) -> None:
        """Answer the acceptance check only while the hub has not closed the connection."""
        del hub
        if self.last_close_code is None:
            await self.callback({"type": MessageType.WHO_SNAPSHOT})

    async def send_message(self, msg_type: str, **fields: Any) -> None:
        self.sent.append(msg_type)
        if self.reply is not None:
            frame = dict(self.reply)
            if frame.get("type") == MessageType.OPERATION_ATTEMPT_RESULT:
                frame["request_key"] = fields["idem_key"]
            await self.callback({"type": MessageType.OPERATION_ATTEMPT_RESULT, "request_key": "x"})
            await self.callback(frame)


async def _run(
    reply: dict[str, Any] | None,
    *,
    ready: bool = True,
    close_code: int | None = None,
    agents: list[_FakeAgent] | None = None,
) -> int:
    def factory(_name: str, callback: Any, **_kwargs: Any) -> Any:
        agent = _FakeAgent(callback, reply=reply, ready=ready, close_code=close_code)
        if agents is not None:
            agents.append(agent)
        return agent

    return await command.request_attempt(
        uri="ws://hub",
        actor=scene.WORKER,
        fields=_fields(3, "d" * 64, "key-9"),
        token=None,
        ready_timeout=0.1,
        result_timeout=0.1,
        json_output=False,
        agent_factory=factory,
    )


async def test_old_hubs_errors_silence_and_refusals_have_their_own_exit_codes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    unknown = {"type": MessageType.ERROR, "payload": "Unknown message type 'operation_attempt'."}
    assert await _run(unknown) == command.EXIT_UNSUPPORTED
    assert capsys.readouterr().out.splitlines() == [
        "operation attempt unsupported_by_hub for op-1: Unknown message type 'operation_attempt'.",
        "request key: key-9",
    ]
    assert await _run({"type": MessageType.ERROR, "error_code": "rate_limited"}) == (
        command.EXIT_REFUSED
    )
    assert capsys.readouterr().out.startswith("operation attempt rate_limited for op-1: ")
    assert await _run({"type": MessageType.ERROR}) == command.EXIT_REFUSED
    assert capsys.readouterr().out.startswith("operation attempt hub_error for op-1: ")
    bare = {"type": MessageType.OPERATION_ATTEMPT_RESULT, "answer": "approved_fresh"}
    assert await _run(bare) == command.EXIT_REFUSED
    assert "request key: key-9" in capsys.readouterr().out
    assert await _run(None) == command.EXIT_NO_VERDICT
    assert capsys.readouterr().out.splitlines() == [
        "operation attempt failed: the hub returned no answer",
        "request key: key-9",
    ]
    assert await _run(None, ready=False) == command.EXIT_NO_VERDICT
    assert capsys.readouterr().out.splitlines()[-1] == "request key: key-9"


async def test_a_hub_that_closes_after_its_welcome_receives_no_request(
    capsys: pytest.CaptureFixture[str],
) -> None:
    agents: list[_FakeAgent] = []
    reply = {"type": MessageType.OPERATION_ATTEMPT_RESULT, "answer": "approved_fresh"}
    assert await _run(reply, close_code=4009, agents=agents) == command.EXIT_NO_VERDICT
    printed = capsys.readouterr().out.splitlines()
    assert printed[-1] == "request key: key-9"
    assert "4009" in printed[0]
    assert agents[0].sent == []


def test_the_command_makes_a_key_unless_one_is_given(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []

    async def capture(**kwargs: Any) -> int:
        seen.append(kwargs)
        return command.EXIT_OPEN

    monkeypatch.setattr(command, "request_attempt", capture)
    base = [
        "operation",
        "attempt",
        "--task",
        "T1",
        "--name",
        scene.WORKER,
        "--operation-id",
        "op-1",
        "--epoch",
        "3",
        "--intent-sha256",
        "d" * 64,
    ]
    made = build_parser().parse_args(base)
    assert made.func(made) == command.EXIT_OPEN
    given = build_parser().parse_args([*base, "--idem-key", "mine"])
    assert given.func(given) == command.EXIT_OPEN
    assert re.fullmatch(r"[0-9a-f]{32}", seen[0]["fields"]["idem_key"])
    assert seen[1]["fields"] == {
        "task_id": "T1",
        "operation_id": "op-1",
        "epoch": 3,
        "intent_sha256": "d" * 64,
        "idem_key": "mine",
    }
    assert (seen[1]["actor"], seen[1]["json_output"]) == (scene.WORKER, False)
