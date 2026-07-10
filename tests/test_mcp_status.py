# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — MCP status projection tests

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from synapse_channel.mcp.status import Matcher, Sender, mcp_status


class RecordingStatusAgent:
    """Record which real hub snapshot requests the status projection issues."""

    def __init__(self) -> None:
        self.requests: list[str] = []

    async def request_who(self) -> None:
        self.requests.append("who")

    async def request_state(self) -> None:
        self.requests.append("state")


def _scripted_awaiter(
    replies: list[dict[str, Any] | None],
) -> Callable[[Matcher, Sender], Awaitable[dict[str, Any] | None]]:
    queue = list(replies)

    async def await_reply(match: Matcher, send: Sender) -> dict[str, Any] | None:
        await send()
        reply = queue.pop(0)
        assert reply is None or match(reply)
        return reply

    return await_reply


async def test_status_projects_presence_work_and_mailbox_count() -> None:
    agent = RecordingStatusAgent()
    await_reply = _scripted_awaiter(
        [
            {
                "type": "who_snapshot",
                "online_agents": ["PROJ/client", "PROJ/client-rx", "OTHER"],
                "mailbox_pending": {"PROJ/client": 3},
            },
            {
                "type": "state_snapshot",
                "snapshot": {
                    "active_claims": [{"task_id": "T"}],
                    "resources": {"R": {}},
                },
            },
        ]
    )

    payload = json.loads(
        await mcp_status(identity="PROJ/client", await_reply=await_reply, agent=agent)
    )

    assert agent.requests == ["who", "state"]
    assert payload == {
        "active_claims": 1,
        "identity": "PROJ/client",
        "mailbox_pending": 3,
        "mailbox_pending_available": True,
        "online_agents": 2,
        "resources": 1,
        "waiter_online": True,
        "waiters": 1,
    }


async def test_status_preserves_unavailable_and_malformed_snapshots() -> None:
    agent = RecordingStatusAgent()
    await_reply = _scripted_awaiter(
        [
            {"type": "who_snapshot", "online_agents": "bad", "mailbox_pending": "bad"},
            {"type": "state_snapshot", "snapshot": "bad"},
        ]
    )

    payload = json.loads(await mcp_status(identity="A", await_reply=await_reply, agent=agent))

    assert payload["mailbox_pending"] is None
    assert payload["mailbox_pending_available"] is False
    assert payload["online_agents"] == 0
    assert payload["active_claims"] == 0


async def test_status_reports_each_missing_snapshot() -> None:
    agent = RecordingStatusAgent()
    missing_who = await mcp_status(
        identity="A",
        await_reply=_scripted_awaiter([None]),
        agent=agent,
    )
    missing_state = await mcp_status(
        identity="A",
        await_reply=_scripted_awaiter([{"type": "who_snapshot"}, None]),
        agent=agent,
    )

    assert missing_who == "the hub did not return MCP status roster data"
    assert missing_state == "the hub did not return MCP status state data"
