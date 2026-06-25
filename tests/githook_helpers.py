# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for git-hook auto-release of branch-scoped claims

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, cast

from synapse_channel.git.gitclaim import AgentFactory


class FakeAgent:
    """A SynapseAgent stand-in that replays an inbound snapshot and records releases."""

    def __init__(
        self,
        name: str,
        callback: Callable[[dict[str, Any]], Awaitable[None]],
        *,
        uri: str = "ws://test",
        verbose: bool = False,
        token: str | None = None,
        ready: bool = True,
        inbound: list[dict[str, Any]] | None = None,
    ) -> None:
        self.name = name
        self.callback = callback
        self.uri = uri
        self.token = token
        self.running = True
        self._ready = ready
        self._inbound = inbound or []
        self.releases: list[str] = []
        self.state_requests = 0

    async def connect(self) -> None:
        for message in self._inbound:
            await self.callback(message)

    async def wait_until_ready(self, timeout: float = 5.0) -> bool:
        return self._ready

    async def request_state(self) -> None:
        self.state_requests += 1

    async def release(self, task_id: str, **_kw: Any) -> None:
        self.releases.append(task_id)


def make_factory(
    *, ready: bool = True, inbound: list[dict[str, Any]] | None = None
) -> tuple[AgentFactory, list[FakeAgent]]:
    created: list[FakeAgent] = []

    def factory(name: str, callback: Any, **kwargs: Any) -> FakeAgent:
        agent = FakeAgent(name, callback, ready=ready, inbound=inbound, **kwargs)
        created.append(agent)
        return agent

    return cast(AgentFactory, factory), created


def _snapshot(claims: list[dict[str, Any]]) -> dict[str, Any]:
    return {"type": "state_snapshot", "snapshot": {"active_claims": claims}}


# -- hooks_directory + install_hooks ------------------------------------------
