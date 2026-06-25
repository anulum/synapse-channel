# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the Model Context Protocol bridge

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any, cast

from synapse_channel.mcp.server import (
    AgentFactory,
    SynapseHubBridge,
)


class FakeAgent:
    """A SynapseAgent stand-in that records calls instead of touching a socket."""

    def __init__(
        self,
        name: str,
        callback: Callable[[dict[str, Any]], Awaitable[None]],
        *,
        uri: str = "ws://test",
        verbose: bool = False,
        token: str | None = None,
        takeover: bool = False,
    ) -> None:
        self.name = name
        self.callback = callback
        self.uri = uri
        self.token = token
        self.running = True
        self.ready = True
        self.calls: list[tuple[Any, ...]] = []

    async def claim(self, task_id: str, *, paths: Any = (), **_kw: Any) -> None:
        self.calls.append(("claim", task_id, list(paths)))

    async def release(self, task_id: str, **_kw: Any) -> None:
        self.calls.append(("release", task_id))

    async def chat(self, payload: str, *, target: str = "all", **_kw: Any) -> None:
        self.calls.append(("chat", target, payload))

    async def handoff(self, task_id: str, to_agent: str, **_kw: Any) -> None:
        self.calls.append(("handoff", task_id, to_agent))

    async def post_task(
        self, task_id: str, *, title: str = "", depends_on: Any = (), **_kw: Any
    ) -> None:
        self.calls.append(("post_task", task_id, title, tuple(depends_on)))

    async def update_ledger_task(
        self,
        task_id: str,
        *,
        status: str | None = None,
        suggested_owner: str | None = None,
        **_kw: Any,
    ) -> None:
        self.calls.append(("update_ledger_task", task_id, status, suggested_owner))

    async def request_board(self) -> None:
        self.calls.append(("request_board",))

    async def request_state(self) -> None:
        self.calls.append(("request_state",))

    async def request_manifest(self) -> None:
        self.calls.append(("request_manifest",))

    async def wait_until_ready(self, timeout: float = 5.0) -> bool:
        return self.ready

    async def connect(self) -> None:
        self.calls.append(("connect",))


def make_bridge(*, name: str = "me", request_timeout: float = 0.1) -> SynapseHubBridge:
    """Build a bridge over a FakeAgent with a short reply timeout."""
    return SynapseHubBridge(
        agent_factory=cast(AgentFactory, FakeAgent), name=name, request_timeout=request_timeout
    )


def agent_of(bridge: SynapseHubBridge) -> FakeAgent:
    """Return the bridge's fake agent, narrowing its type for call inspection."""
    assert isinstance(bridge.agent, FakeAgent)
    return bridge.agent


async def drive(
    bridge: SynapseHubBridge,
    make_coro: Callable[[], Coroutine[Any, Any, str]],
    reply: dict[str, Any] | None = None,
) -> str:
    """Start a bridge call, wait until it has sent, optionally inject a reply, and await it."""
    task = asyncio.create_task(make_coro())
    for _ in range(50):
        if agent_of(bridge).calls:
            break
        await asyncio.sleep(0)
    if reply is not None:
        await bridge.on_message(reply)
    return await task
