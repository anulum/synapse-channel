# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — bounded Git claim transport
"""Separate positive grants, denials and uncertain Git claim outcomes."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

from synapse_channel.client.agent import SynapseAgent
from synapse_channel.client.claim_confirmation import ClaimIntent, confirm_claim
from synapse_channel.connect_failures import (
    closed_after_ready,
    describe_connect_failure,
    explain_silent_outcome,
)
from synapse_channel.core.protocol import MessageType


async def claim_outcome(
    *,
    uri: str,
    intent: ClaimIntent,
    token: str | None,
    agent_factory: Callable[..., SynapseAgent],
    ready_timeout: float,
    reply_timeout: float,
    confirm_only: bool,
) -> tuple[int, bool]:
    """Connect, issue at most one claim, and confirm an uncertain result.

    Parameters
    ----------
    uri : str
        Hub WebSocket URI.
    intent : ClaimIntent
        Exact normalized scope that may authorize the caller's work.
    token : str or None
        Authentication secret passed only to the client transport.
    agent_factory : Callable[..., SynapseAgent]
        Factory for the connected client and inbound callback.
    ready_timeout : float
        Validated finite connection readiness deadline in seconds.
    reply_timeout : float
        Validated finite deadline for each send/reply exchange in seconds.
    confirm_only : bool
        Read the existing lease without issuing a claim mutation.

    Returns
    -------
    tuple[int, bool]
        CLI status (0 granted, 1 refused, 3 unknown), and whether success came
        from read-only confirmation. Cancellation joins the connection task.
    """
    pending: tuple[Callable[[dict[str, Any]], bool], asyncio.Future[dict[str, Any]]] | None = None

    async def collect(data: dict[str, Any]) -> None:
        """Resolve only the currently correlated transport request."""
        if pending is not None and not pending[1].done() and pending[0](data):
            pending[1].set_result(data)

    agent = agent_factory(intent.owner, collect, uri=uri, verbose=False, token=token)
    conn_task = asyncio.create_task(agent.connect())

    async def await_reply(
        match: Callable[[dict[str, Any]], bool], send: Callable[[], Awaitable[None]]
    ) -> dict[str, Any] | None:
        """Bound the whole send/reply exchange and unregister on every exit."""
        nonlocal pending
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        pending = (match, future)

        async def exchange() -> dict[str, Any]:
            """Send after registration and await the matching response."""
            await send()
            return await future

        try:
            return await asyncio.wait_for(exchange(), reply_timeout)
        except asyncio.TimeoutError:
            return None
        finally:
            pending = None

    try:
        if not await agent.wait_until_ready(timeout=ready_timeout) or await closed_after_ready(
            agent
        ):
            print(
                describe_connect_failure(
                    intent.owner,
                    uri,
                    close_code=agent.last_close_code,
                    close_reason=agent.last_close_reason,
                )
            )
            return 1, False
        if not confirm_only:
            reply = await await_reply(
                lambda data: (
                    data.get("task_id") == intent.task_id
                    and (
                        data.get("type") == MessageType.CLAIM_DENIED
                        or (
                            data.get("type") == MessageType.CLAIM_GRANTED
                            and intent.matches(data, now=time.time())
                        )
                    )
                ),
                lambda: agent.claim(
                    intent.task_id,
                    worktree=intent.worktree,
                    paths=intent.paths,
                    path_identity=intent.path_identity,
                    git=intent.git,
                ),
            )
            if reply is not None:
                if reply.get("type") == MessageType.CLAIM_GRANTED:
                    return 0, False
                print(
                    f"claim denied for '{intent.task_id}': {reply.get('payload') or 'claim denied'}"
                )
                return 1, False
        if await confirm_claim(agent, await_reply, intent):
            return 0, True
        print(
            explain_silent_outcome(
                intent.owner,
                uri,
                close_code=agent.last_close_code,
                close_reason=agent.last_close_reason,
                fallback=(
                    f"claim outcome unknown for '{intent.task_id}': no confirmed live lease; "
                    "use git-claim --confirm-only with the same identity and scope before working"
                ),
            )
        )
        return 3, False
    finally:
        agent.running = False
        conn_task.cancel()
        await asyncio.gather(conn_task, return_exceptions=True)
