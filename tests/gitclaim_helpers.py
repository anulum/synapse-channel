# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for client-side git-scoped claims

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, cast

from synapse_channel.git.gitclaim import (
    AgentFactory,
)


class FakeAgent:
    """A SynapseAgent stand-in that records claims and exposes its callback."""

    def __init__(
        self,
        name: str,
        callback: Callable[[dict[str, Any]], Awaitable[None]],
        *,
        uri: str = "ws://test",
        verbose: bool = False,
        token: str | None = None,
    ) -> None:
        self.name = name
        self.callback = callback
        self.uri = uri
        self.token = token
        self.running = True
        self.ready = True
        self.claims: list[tuple[str, list[str], dict[str, Any] | None]] = []
        self.worktrees: list[str] = []

    async def connect(self) -> None:
        return None

    async def wait_until_ready(self, timeout: float = 5.0) -> bool:
        return self.ready

    async def claim(
        self,
        task_id: str,
        *,
        worktree: str = "",
        paths: Any = (),
        git: dict[str, Any] | None = None,
        **_kw: Any,
    ) -> None:
        self.claims.append((task_id, list(paths), git))
        self.worktrees.append(worktree)


def make_factory(*, ready: bool = True) -> tuple[AgentFactory, list[FakeAgent]]:
    """Return an agent factory plus the list it appends each created agent to."""
    created: list[FakeAgent] = []

    def factory(name: str, callback: Any, **kwargs: Any) -> FakeAgent:
        agent = FakeAgent(name, callback, **kwargs)
        agent.ready = ready
        created.append(agent)
        return agent

    return cast(AgentFactory, factory), created


async def _await_claim_sent(created: list[FakeAgent]) -> FakeAgent:
    """Spin until the flow has created an agent and sent its claim."""

    for _ in range(100):
        if created and created[0].claims:
            return created[0]
        await asyncio.sleep(0)
    raise AssertionError("claim was never sent")


# -- _default_git_runner ------------------------------------------------------
