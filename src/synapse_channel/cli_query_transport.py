# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — read-only CLI hub-query transport
"""Shared connect, request, poll, and cleanup flow for read-only CLI queries."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from typing import Any

from synapse_channel.client.agent import SynapseAgent
from synapse_channel.connect_failures import describe_connect_failure, is_identity_refused_close

AgentFactory = Callable[..., SynapseAgent]


async def _query_attempt(
    *,
    uri: str,
    connect_name: str,
    failure_name: str,
    token: str | None,
    response_type: str,
    request: Callable[[SynapseAgent], Awaitable[None]],
    render: Callable[[Any], None],
    transform: Callable[[dict[str, Any]], Any],
    agent_factory: AgentFactory,
    attempts: int,
    ready_timeout: float,
    suppress_identity_refusal: bool,
) -> tuple[int, bool]:
    """Run one query connection and classify an identity-only refusal."""
    results: list[Any] = []

    async def collect(data: dict[str, Any]) -> None:
        if data.get("type") == response_type:
            results.append(transform(data))

    agent = agent_factory(connect_name, collect, uri=uri, verbose=False, token=token)
    conn_task = asyncio.create_task(agent.connect())
    try:
        if not await agent.wait_until_ready(timeout=ready_timeout):
            identity_refused = is_identity_refused_close(
                agent.last_close_code, agent.last_close_reason
            )
            if not (identity_refused and suppress_identity_refusal):
                print(
                    describe_connect_failure(
                        failure_name,
                        uri,
                        close_code=agent.last_close_code,
                        close_reason=agent.last_close_reason,
                    )
                )
            return 1, identity_refused
        await request(agent)
        for _ in range(attempts):
            if results:
                break
            if agent.last_close_code is not None:
                break
            await asyncio.sleep(0.05)
        if results:
            render(results[-1])
            return 0, False
        if agent.last_close_code is not None:
            identity_refused = is_identity_refused_close(
                agent.last_close_code, agent.last_close_reason
            )
            if not (identity_refused and suppress_identity_refusal):
                print(
                    describe_connect_failure(
                        failure_name,
                        uri,
                        close_code=agent.last_close_code,
                        close_reason=agent.last_close_reason,
                    )
                )
            return 1, identity_refused
        return 0, False
    finally:
        agent.running = False
        conn_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await conn_task


async def _drop_message(_data: dict[str, Any]) -> None:
    """Discard a hub message — for probes that only need the connection to open."""
    return None


def _identity(data: dict[str, Any]) -> dict[str, Any]:
    """Return the message unchanged — the default reply transform for a query."""
    return data


async def _query_hub(
    *,
    uri: str,
    name: str,
    token: str | None,
    response_type: str,
    request: Callable[[SynapseAgent], Awaitable[None]],
    render: Callable[[Any], None],
    transform: Callable[[dict[str, Any]], Any] = _identity,
    agent_factory: AgentFactory = SynapseAgent,
    attempts: int = 50,
    ready_timeout: float = 5.0,
    identity_fallback_name: str | None = None,
) -> int:
    """Connect, issue one request, await the matching reply, render it, and exit.

    The shared connect → ready → request → poll → cleanup flow behind ``who``,
    ``state``, ``board``, ``manifest``, and the task writes; a caller supplies only
    the reply ``response_type``, how to ``request`` it, what to ``render``, and an
    optional ``transform`` from the raw message to the rendered value.

    Parameters
    ----------
    uri, name : str
        Hub URI and the requester's display name.
    token : str or None
        Shared-secret token for a secured hub.
    response_type : str
        The inbound message type that answers the request.
    request : Callable
        Coroutine that issues the request on the connected agent.
    render : Callable
        Renders the latest (transformed) reply; it prints and returns nothing.
    transform : Callable, optional
        Maps the raw reply to the value handed to ``render``. Identity by default.
    agent_factory : AgentFactory, optional
        Factory for the client agent; injectable for testing.
    attempts : int, optional
        Poll attempts (50 ms each) for the reply before giving up. Defaults to ``50``.
    ready_timeout : float, optional
        Seconds to wait for the welcome handshake before treating the hub as
        unreachable. Defaults to ``5.0``.
    identity_fallback_name : str or None, optional
        A separately scoped signed identity used only when the primary name is
        refused with the exact identity-binding close class. Network, capacity,
        ownership, and protocol failures never trigger the fallback.

    Returns
    -------
    int
        ``0`` once a reply is rendered (or none arrives), ``1`` when the hub is unreachable.
    """
    fallback = str(identity_fallback_name or "").strip()
    primary_code, identity_refused = await _query_attempt(
        uri=uri,
        connect_name=name,
        failure_name=name,
        token=token,
        response_type=response_type,
        request=request,
        render=render,
        transform=transform,
        agent_factory=agent_factory,
        attempts=attempts,
        ready_timeout=ready_timeout,
        suppress_identity_refusal=bool(fallback and fallback != name),
    )
    if not identity_refused or not fallback or fallback == name:
        return primary_code
    fallback_code, _ = await _query_attempt(
        uri=uri,
        connect_name=fallback,
        failure_name=name,
        token=token,
        response_type=response_type,
        request=request,
        render=render,
        transform=transform,
        agent_factory=agent_factory,
        attempts=attempts,
        ready_timeout=ready_timeout,
        suppress_identity_refusal=False,
    )
    return fallback_code
