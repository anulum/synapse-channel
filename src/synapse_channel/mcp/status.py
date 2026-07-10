# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — MCP status projection over real hub snapshots
"""Build a compact MCP status result from correlated WHO and STATE replies."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from synapse_channel.core.mailbox_pending import parse_pending_counts
from synapse_channel.core.protocol import MessageType
from synapse_channel.waiter_identity import split_roster

Matcher = Callable[[dict[str, Any]], bool]
Sender = Callable[[], Awaitable[None]]
ReplyAwaiter = Callable[[Matcher, Sender], Awaitable[dict[str, Any] | None]]


class StatusAgent(Protocol):
    """Hub request surface needed by :func:`mcp_status`."""

    async def request_who(self) -> None:
        """Request the live roster snapshot."""

    async def request_state(self) -> None:
        """Request the live coordination-state snapshot."""


async def mcp_status(
    *,
    identity: str,
    await_reply: ReplyAwaiter,
    agent: StatusAgent,
) -> str:
    """Return the bridge identity's live hub status as stable JSON.

    Parameters
    ----------
    identity : str
        Exact MCP bridge identity.
    await_reply : ReplyAwaiter
        Correlated request/reply seam owned by the bridge.
    agent : StatusAgent
        Connected hub client used to issue WHO and STATE requests.

    Returns
    -------
    str
        Compact JSON status, or a clear no-response line when either snapshot
        does not arrive.
    """
    who = await await_reply(
        lambda data: data.get("type") == MessageType.WHO_SNAPSHOT,
        agent.request_who,
    )
    if who is None:
        return "the hub did not return MCP status roster data"
    state = await await_reply(
        lambda data: data.get("type") == MessageType.STATE_SNAPSHOT,
        agent.request_state,
    )
    if state is None:
        return "the hub did not return MCP status state data"

    raw_agents = who.get("online_agents")
    roster = [str(item) for item in raw_agents] if isinstance(raw_agents, list) else []
    agents, waiters = split_roster(roster)
    raw_snapshot = state.get("snapshot")
    snapshot = raw_snapshot if isinstance(raw_snapshot, dict) else {}
    counts = parse_pending_counts(who.get("mailbox_pending"))
    payload: dict[str, object] = {
        "active_claims": _length(snapshot.get("active_claims")),
        "identity": identity,
        "mailbox_pending": counts.get(identity, 0) if counts is not None else None,
        "mailbox_pending_available": counts is not None,
        "online_agents": len(agents),
        "resources": _length(snapshot.get("resources")),
        "waiter_online": f"{identity}-rx" in roster,
        "waiters": len(waiters),
    }
    return json.dumps(payload, indent=2, sort_keys=True)


def _length(value: object) -> int:
    """Return a collection length for list/dict snapshots, otherwise zero."""
    return len(value) if isinstance(value, (list, dict)) else 0
