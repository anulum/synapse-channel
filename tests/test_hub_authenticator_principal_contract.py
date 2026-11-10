# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the hub's admission contract for a configured authenticator
"""A configured authenticator must name a quota principal for every connection it admits.

``HubAuthConfig`` accepts any :class:`TokenAuthenticator`, a subclass included. The
hub charges an admitted connection's claims to the principal the authenticator
returns. These tests run a real hub built through ``SynapseHub.from_config`` with an
authenticator that reports success without a principal, and connect a real client:

- the connection is never admitted, no name is bound and no quota principal is set;
- the same hub still refuses a wrong token through its normal denial.
"""

from __future__ import annotations

import asyncio

from hub_e2e_helpers import Recorder, running_hub
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.auth import TokenAuthenticator
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.hub_config import HubAuthConfig, HubConfig
from synapse_channel.core.protocol import MessageType


class _PrincipalLessAuthenticator(TokenAuthenticator):
    """A token authenticator that breaks the contract: success without a principal."""

    def authenticate_with_principal(self, token: str, agent: str) -> tuple[bool, str, str | None]:
        """Return the base verdict with the principal removed."""
        ok, reason, _principal = super().authenticate_with_principal(token, agent)
        return ok, reason, None


async def _attempt(uri: str, name: str, token: str) -> tuple[bool, SynapseAgent, Recorder]:
    """Connect a real client, wait a bounded time for the welcome, then stop it."""
    recorder = Recorder()
    agent = SynapseAgent(
        name, recorder, uri=uri, heartbeat_interval=60.0, verbose=False, token=token
    )
    task = asyncio.create_task(agent.connect())
    try:
        ready = await agent.wait_until_ready(1.5)
    finally:
        agent.running = False
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    return ready, agent, recorder


async def test_success_without_a_principal_is_never_admitted() -> None:
    """The hub refuses an admission it cannot charge to a principal, and keeps serving."""
    config = HubConfig(auth=HubAuthConfig(authenticator=_PrincipalLessAuthenticator(["secret"])))
    async with running_hub(SynapseHub.from_config(config)) as (hub, uri):
        ready, _agent, recorder = await _attempt(uri, "P/principal-less", "secret")

        assert ready is False
        assert not any(m.get("type") == MessageType.WELCOME for m in recorder.messages)
        assert hub.clients.socket_agent == {}
        assert hub.clients.socket_quota_principals == {}

        denied_ready, _denied, denied = await _attempt(uri, "P/wrong-token", "wrong")

        assert denied_ready is False
        assert any(m.get("type") == MessageType.AUTH_DENIED for m in denied.messages)
        assert hub.clients.socket_agent == {}
