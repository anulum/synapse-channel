# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the lease-serialising CLI commands (lock/release)

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any


class FakeAgent:
    """Configurable stand-in for SynapseAgent used by the lock/release tests."""

    def __init__(
        self,
        name: str,
        callback: Callable[[dict[str, Any]], Awaitable[None]],
        *,
        uri: str,
        verbose: bool,
        token: str | None = None,
        ready: bool = True,
        inbound: list[dict[str, Any]] | None = None,
        idle: bool = True,
    ) -> None:
        self.name = name
        self.callback = callback
        self.uri = uri
        self.token = token
        self.running = True
        self.claims: list[str] = []
        self.claim_worktrees: list[str] = []
        self.releases: list[str] = []
        self._ready = ready
        self._inbound = inbound or []
        self._idle = idle

    async def connect(self) -> None:
        for message in self._inbound:
            await self.callback(message)
        if self._idle:
            await asyncio.Event().wait()  # block until cancelled

    async def wait_until_ready(self, timeout: float = 5.0) -> bool:
        return self._ready

    async def claim(
        self,
        task_id: str,
        *,
        note: str = "",
        ttl_seconds: float | None = None,
        worktree: str = "",
        paths: Any = (),
        idem_key: str | None = None,
    ) -> None:
        self.claims.append(task_id)
        self.claim_worktrees.append(worktree)

    async def release(self, task_id: str, *, idem_key: str | None = None) -> None:
        self.releases.append(task_id)


def _factory(
    holder: list[FakeAgent],
    *,
    ready: bool = True,
    inbound: list[dict[str, Any]] | None = None,
    idle: bool = True,
) -> Callable[..., Any]:
    def make(
        name: str,
        callback: Any,
        *,
        uri: str,
        verbose: bool,
        token: str | None = None,
    ) -> Any:
        agent = FakeAgent(
            name,
            callback,
            uri=uri,
            verbose=verbose,
            token=token,
            ready=ready,
            inbound=inbound,
            idle=idle,
        )
        holder.append(agent)
        return agent

    return make


# --- lock (serialised commands) ----------------------------------------------
