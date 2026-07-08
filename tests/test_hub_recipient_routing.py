# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — recipient routing: directed messages reach only their audience

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from hub_e2e_helpers import close_agents, connect_agent, running_hub
from synapse_channel.core.acl import OBSERVE, AclPolicy, AclRule
from synapse_channel.core.handlers.messaging import _directed_audience
from synapse_channel.core.hub import SynapseHub


def _is_chat(payload: str) -> Callable[[dict[str, Any]], bool]:
    return lambda message: message.get("type") == "chat" and message.get("payload") == payload


class TestDirectedAudience:
    def test_includes_recipients_their_sidecars_and_observers(self) -> None:
        assert _directed_audience(["A", "B"], ["OPS/mon"]) == [
            "A",
            "A-rx",
            "B",
            "B-rx",
            "OPS/mon",
        ]

    def test_empty_recipients_yields_only_observers(self) -> None:
        assert _directed_audience([], ["OPS/mon"]) == ["OPS/mon"]


class TestObservingIdentities:
    def test_no_policy_means_no_observers(self) -> None:
        hub = SynapseHub()
        hub.clients.set_agent_socket("OPS/mon", object())

        assert hub.observing_identities("BETA") == ()

    def test_returns_only_granted_observers(self) -> None:
        hub = SynapseHub(acl_policy=AclPolicy([AclRule(OBSERVE, "agent", "*", "OPS")]))
        hub.clients.set_agent_socket("OPS/mon", object())
        hub.clients.set_agent_socket("X/other", object())

        assert hub.observing_identities("BETA") == ("OPS/mon",)


async def test_directed_message_reaches_only_its_recipient_under_routing() -> None:
    hub = SynapseHub(hub_id="rr", private_directed_messages=True)
    async with running_hub(hub) as (_hub, uri):
        alpha = await connect_agent("ALPHA", uri)
        beta = await connect_agent("BETA", uri)
        gamma = await connect_agent("GAMMA", uri)
        try:
            await alpha.agent.chat("secret", target="BETA")
            await beta.recorder.wait_for(_is_chat("secret"))
            with pytest.raises(TimeoutError):
                await gamma.recorder.wait_for(_is_chat("secret"), timeout=0.5)
        finally:
            await close_agents(alpha, beta, gamma)


async def test_directed_message_reaches_everyone_when_routing_off() -> None:
    # Default posture: an uninvolved socket still sees directed traffic (the behaviour
    # recipient routing exists to change).
    async with running_hub(SynapseHub(hub_id="open")) as (_hub, uri):
        alpha = await connect_agent("ALPHA", uri)
        beta = await connect_agent("BETA", uri)
        gamma = await connect_agent("GAMMA", uri)
        try:
            await alpha.agent.chat("secret", target="BETA")
            await gamma.recorder.wait_for(_is_chat("secret"))
        finally:
            await close_agents(alpha, beta, gamma)


async def test_broadcast_all_still_reaches_everyone_under_routing() -> None:
    hub = SynapseHub(hub_id="rr", private_directed_messages=True)
    async with running_hub(hub) as (_hub, uri):
        alpha = await connect_agent("ALPHA", uri)
        beta = await connect_agent("BETA", uri)
        try:
            await alpha.agent.chat("hello", target="all")
            await beta.recorder.wait_for(_is_chat("hello"))
        finally:
            await close_agents(alpha, beta)


async def test_granted_observer_receives_directed_traffic_under_routing() -> None:
    hub = SynapseHub(
        hub_id="rr",
        private_directed_messages=True,
        acl_policy=AclPolicy([AclRule(OBSERVE, "agent", "*", "OPS")]),
    )
    async with running_hub(hub) as (_hub, uri):
        alpha = await connect_agent("ALPHA", uri)
        beta = await connect_agent("BETA", uri)
        monitor = await connect_agent("OPS/mon", uri)
        try:
            await alpha.agent.chat("watched", target="BETA")
            await beta.recorder.wait_for(_is_chat("watched"))
            await monitor.recorder.wait_for(_is_chat("watched"))
        finally:
            await close_agents(alpha, beta, monitor)
