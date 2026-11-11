# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — `synapse mailbox handover` and `close` report the hub's verdict
"""The commands send one request as ``--name`` and map the verdict to an exit code."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from hub_e2e_helpers import close_agents, connect_agent, running_hub
from synapse_channel import cli_mailbox_handover as command
from synapse_channel.cli import build_parser
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType


def test_the_parsers_require_an_actor_and_a_reason() -> None:
    parser = build_parser()
    args = parser.parse_args(
        ["mailbox", "handover", "P/old", "--to", "P/new", "--name", "P/boss", "--reason", "ended"]
    )
    assert args.func is command._cmd_handover
    assert command._fields(args, successor=args.to) == {
        "from": "P/old",
        "to": "P/new",
        "reason": "ended",
    }
    closed = parser.parse_args(
        ["mailbox", "close", "P/old", "--name", "P/old", "--reason", "r", "--through", "7"]
    )
    assert closed.func is command._cmd_close
    assert command._fields(closed, successor=None) == {
        "from": "P/old",
        "reason": "r",
        "through_seq": 7,
    }
    with pytest.raises(SystemExit):
        parser.parse_args(["mailbox", "close", "P/old", "--reason", "r"])


@pytest.mark.parametrize("text", ["0", "-3", "seven"])
def test_the_through_option_takes_a_positive_sequence(text: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        command._positive_seq(text)


def test_the_actions_run_one_request_each(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, dict[str, Any]]] = []

    async def fake(*, actor: str, fields: dict[str, Any], **_kwargs: Any) -> int:
        seen.append((actor, fields))
        return 0

    monkeypatch.setattr(command, "mailbox_handover", fake)
    parser = build_parser()
    handover = parser.parse_args(
        ["mailbox", "handover", "P/old", "--to", "P/new", "--name", "P/boss", "--reason", "x"]
    )
    close = parser.parse_args(["mailbox", "close", "P/old", "--name", "P/old", "--reason", "y"])
    assert command._cmd_handover(handover) == 0
    assert command._cmd_close(close) == 0
    assert seen == [
        ("P/boss", {"from": "P/old", "to": "P/new", "reason": "x"}),
        ("P/old", {"from": "P/old", "reason": "y"}),
    ]


async def test_a_live_hub_applies_a_handover_and_then_reports_nothing_left(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = EventStore(tmp_path / "hub.db")
    hub = SynapseHub(journal=store, mailbox_custodians={"P": "P/boss"})
    try:
        async with running_hub(hub) as (_hub, uri):
            peer = await connect_agent("X/peer", uri)
            try:
                await peer.agent.send_message(MessageType.CHAT, target="P/old", payload="hello")
                loop = asyncio.get_running_loop()
                deadline = loop.time() + 3.0
                while not store.read_since(0, kinds=(EventKind.CHAT,)):
                    assert loop.time() < deadline
                    await asyncio.sleep(0.01)
            finally:
                await close_agents(peer)
            options: dict[str, Any] = {
                "uri": uri,
                "actor": "P/boss",
                "fields": {"from": "P/old", "to": "P/new", "reason": "ended"},
                "token": None,
                "ready_timeout": 3.0,
                "result_timeout": 3.0,
            }
            assert await command.mailbox_handover(**options, json_output=False) == 0
            assert "handed 1 message(s) from P/old to P/new" in capsys.readouterr().out
            assert await command.mailbox_handover(**options, json_output=True) == 1
            verdict = json.loads(capsys.readouterr().out)
            assert (verdict["status"], verdict["reason"]) == ("refused", "nothing_pending")
    finally:
        store.close()


class _FakeAgent:
    """A client that answers a request with a fixed frame, or with nothing."""

    def __init__(
        self, callback: Any, *, outcome: dict[str, Any] | None, ready: bool, closed: bool
    ) -> None:
        self.callback = callback
        self.outcome = outcome
        self.ready = ready
        self.running = not closed
        self.last_close_code = 4009 if closed else None
        self.last_close_reason = "name conflict" if closed else ""

    async def connect(self) -> None:
        while self.running:
            await asyncio.sleep(1.0)

    async def wait_until_ready(self, timeout: float) -> bool:
        del timeout
        return self.ready

    async def send_message(self, _msg_type: str, **_fields: Any) -> None:
        if self.outcome is not None:
            await self.callback(self.outcome)

    async def request_who(self, hub: str | None = None) -> None:
        """Answer the acceptance check the way an accepting hub does."""
        del hub
        if self.running and self.last_close_code is None:
            await self.callback({"type": MessageType.WHO_SNAPSHOT})


def _factory(outcome: dict[str, Any] | None, *, ready: bool = True, closed: bool = False) -> Any:
    def factory(_name: str, callback: Any, **_kwargs: Any) -> _FakeAgent:
        return _FakeAgent(callback, outcome=outcome, ready=ready, closed=closed)

    return factory


async def _run(outcome: dict[str, Any] | None, *, json_output: bool = False, **flags: bool) -> int:
    return await command.mailbox_handover(
        uri="ws://hub",
        actor="P/boss",
        fields={"from": "P/old", "reason": "r"},
        token=None,
        ready_timeout=0.1,
        result_timeout=0.1,
        json_output=json_output,
        agent_factory=_factory(outcome, **flags),
    )


async def test_more_remaining_exits_three(capsys: pytest.CaptureFixture[str]) -> None:
    outcome = {
        "type": MessageType.MAILBOX_HANDOVER_RESULT,
        "status": "applied",
        "remaining": True,
        "forwarded": 200,
        "payload": "handed 200 message(s); more mail remains",
    }
    assert await _run(outcome) == command.EXIT_MORE_REMAINS
    assert "more mail remains" in capsys.readouterr().out


async def test_a_hub_error_is_a_refusal(capsys: pytest.CaptureFixture[str]) -> None:
    outcome = {"type": MessageType.ERROR, "payload": "access denied: message"}
    assert await _run(outcome, json_output=True) == command.EXIT_REFUSED
    rendered = json.loads(capsys.readouterr().out)
    assert (rendered["reason"], rendered["detail"]) == ("hub_error", "access denied: message")
    assert await _run({"type": MessageType.ERROR}) == command.EXIT_REFUSED
    assert "the hub refused the request" in capsys.readouterr().out


async def test_a_bare_result_is_read_as_a_refusal(capsys: pytest.CaptureFixture[str]) -> None:
    assert await _run({"type": MessageType.MAILBOX_HANDOVER_RESULT}) == command.EXIT_REFUSED
    assert "mailbox handover refused ()" in capsys.readouterr().out


async def test_no_verdict_and_no_connection_exit_two(capsys: pytest.CaptureFixture[str]) -> None:
    assert await _run(None) == command.EXIT_NO_VERDICT
    assert "no verdict" in capsys.readouterr().out
    assert await _run(None, ready=False) == command.EXIT_NO_VERDICT
    assert await _run(None, closed=True) == command.EXIT_NO_VERDICT
    assert "P/boss" in capsys.readouterr().out
