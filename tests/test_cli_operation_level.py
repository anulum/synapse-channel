# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — `synapse operation level` and its exit codes
"""The command asks once, prints the answer and its key, and exits 0 only when the level holds."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import pytest

from synapse_channel import cli_operation_level as command
from synapse_channel.cli import build_parser
from synapse_channel.core.protocol import MessageType

OPERATOR = "H/operator"


class _FakeAgent:
    """A client whose hub answers a request with a fixed frame, or with nothing."""

    def __init__(self, callback: Any, *, reply: dict[str, Any] | None, ready: bool) -> None:
        self.callback = callback
        self.reply = reply
        self.ready = ready
        self.running = True
        self.last_close_code: int | None = None
        self.last_close_reason = ""

    async def connect(self) -> None:
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
            if frame.get("type") == MessageType.OPERATION_LEVEL_RESULT:
                frame["request_key"] = fields["idem_key"]
            await self.callback(frame)


async def _run(
    reply: dict[str, Any] | None, *, ready: bool = True, json_output: bool = False
) -> int:
    def factory(_name: str, callback: Any, **_kwargs: Any) -> Any:
        return _FakeAgent(callback, reply=reply, ready=ready)

    return await command.request_level(
        uri="ws://hub",
        actor=OPERATOR,
        fields={"scope": "project:P", "level": "A2", "reason": "r", "idem_key": "key-9"},
        token=None,
        ready_timeout=0.1,
        result_timeout=0.1,
        json_output=json_output,
        agent_factory=factory,
    )


def _result(answer: str, payload: str) -> dict[str, Any]:
    return {
        "type": MessageType.OPERATION_LEVEL_RESULT,
        "answer": answer,
        "payload": payload,
        "seq": 7,
    }


async def test_each_answer_is_printed_with_its_key_and_exit_code(
    capsys: pytest.CaptureFixture[str],
) -> None:
    recorded = _result("level_recorded", "the scope project:P is at A2 (from A1)")
    assert await _run(recorded) == command.EXIT_RECORDED
    assert capsys.readouterr().out.splitlines() == [
        "operation level level_recorded for project:P: the scope project:P is at A2 (from A1)",
        "request key: key-9",
    ]
    assert await _run(_result("already_at_level", "at A2"), json_output=True) == 0
    assert json.loads(capsys.readouterr().out) == {
        "answer": "already_at_level",
        "detail": "at A2",
        "scope": "project:P",
        "level": "A2",
        "seq": 7,
        "request_key": "key-9",
    }
    assert await _run(_result("raise_stopped", "stopped")) == command.EXIT_REFUSED
    capsys.readouterr()
    unknown = {"type": MessageType.ERROR, "payload": "Unknown message type 'operation_level'."}
    assert await _run(unknown) == command.EXIT_UNSUPPORTED
    assert "unsupported_by_hub" in capsys.readouterr().out
    assert await _run({"type": MessageType.ERROR, "payload": "denied"}) == command.EXIT_REFUSED
    assert capsys.readouterr().out.startswith("operation level hub_error for project:P")
    assert await _run(None) == command.EXIT_NO_VERDICT
    assert capsys.readouterr().out.splitlines() == [
        "operation level failed: the hub returned no answer",
        "request key: key-9",
    ]
    assert await _run(None, ready=False) == command.EXIT_NO_VERDICT
    assert capsys.readouterr().out.splitlines()[-1] == "request key: key-9"


def test_the_command_makes_a_key_unless_one_is_given(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []

    async def capture(**kwargs: Any) -> int:
        seen.append(kwargs)
        return command.EXIT_RECORDED

    monkeypatch.setattr(command, "request_level", capture)
    base = ["operation", "level", "--name", OPERATOR, "--scope", "project:P", "--level", "A3"]
    made = build_parser().parse_args([*base, "--reason", "trusted"])
    assert made.func(made) == command.EXIT_RECORDED
    given = build_parser().parse_args([*base, "--reason", "trusted", "--idem-key", "mine"])
    assert given.func(given) == command.EXIT_RECORDED
    assert re.fullmatch(r"[0-9a-f]{32}", seen[0]["fields"]["idem_key"])
    assert seen[1]["fields"] == {
        "scope": "project:P",
        "level": "A3",
        "reason": "trusted",
        "idem_key": "mine",
    }
    with pytest.raises(SystemExit):
        build_parser().parse_args([*base[:-1], "A7", "--reason", "r"])
