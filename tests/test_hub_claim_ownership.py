# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li — end-to-end namespace-ownership routing of hub claims

from __future__ import annotations

from collections.abc import Mapping, Sequence

from hub_e2e_helpers import close_agents, connect_agent, running_hub
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.namespace_ownership import NamespaceOwnership

_NS = "SYNAPSE-CHANNEL"
_LOCAL_HUB = "syn-a"
_PEER_HUB = "syn-b"
_AGENT = f"{_NS}/alice"


def _hub(owners: dict[str, str]) -> SynapseHub:
    return SynapseHub(
        hub_id=_LOCAL_HUB,
        namespace_ownership=NamespaceOwnership(owners=owners, local_hub_id=_LOCAL_HUB),
    )


def _hub_with_assertions(
    owners: dict[str, str], asserting: Mapping[str, Sequence[str]]
) -> SynapseHub:
    """Return a hub whose ownership resolution sees a runtime feed of asserting peers."""
    return SynapseHub(
        hub_id=_LOCAL_HUB,
        namespace_ownership=NamespaceOwnership(owners=owners, local_hub_id=_LOCAL_HUB),
        observed_asserting_hubs=lambda namespace: asserting.get(namespace, ()),
    )


async def test_a_claim_in_an_owned_namespace_is_granted() -> None:
    async with running_hub(_hub({_NS: _LOCAL_HUB})) as (_, uri):
        agent = await connect_agent(_AGENT, uri)
        try:
            await agent.agent.claim("T1", paths=["src"])
            granted = await agent.recorder.wait_for(lambda m: m.get("type") == "claim_granted")
            assert granted["task_id"] == "T1"
        finally:
            await close_agents(agent)


async def test_a_claim_in_a_remote_owned_namespace_is_refused() -> None:
    async with running_hub(_hub({_NS: _PEER_HUB})) as (_, uri):
        agent = await connect_agent(_AGENT, uri)
        try:
            await agent.agent.claim("T1", paths=["src"])
            denied = await agent.recorder.wait_for(lambda m: m.get("type") == "claim_denied")
            assert denied["namespace"] == _NS
            assert denied["ownership"] == "remote"
            assert denied["owner_hub_id"] == _PEER_HUB
            assert denied["task_id"] == "T1"
            assert _NS in denied["payload"]
        finally:
            await close_agents(agent)


async def test_a_claim_in_an_ungoverned_namespace_is_refused() -> None:
    async with running_hub(_hub({"OTHER-NS": _LOCAL_HUB})) as (_, uri):
        agent = await connect_agent(_AGENT, uri)
        try:
            await agent.agent.claim("T1", paths=["src"])
            denied = await agent.recorder.wait_for(lambda m: m.get("type") == "claim_denied")
            assert denied["ownership"] == "ungoverned"
            assert denied["owner_hub_id"] is None
        finally:
            await close_agents(agent)


async def test_a_peer_asserting_an_owned_namespace_partitions_and_refuses() -> None:
    # A peer observed holding a claim in a namespace this hub also owns is a partition: the
    # hub refuses every grant until ownership is re-established, even though its static map
    # says it owns the namespace.
    hub = _hub_with_assertions({_NS: _LOCAL_HUB}, {_NS: (_PEER_HUB,)})
    async with running_hub(hub) as (_, uri):
        agent = await connect_agent(_AGENT, uri)
        try:
            await agent.agent.claim("T1", paths=["src"])
            denied = await agent.recorder.wait_for(lambda m: m.get("type") == "claim_denied")
            assert denied["ownership"] == "partitioned"
            assert denied["task_id"] == "T1"
        finally:
            await close_agents(agent)


async def test_observed_assertions_without_a_contender_still_grant() -> None:
    # The feed is consulted but reports no contesting peer for the namespace, so the local
    # owner still grants — the runtime signal only refuses an actual partition.
    hub = _hub_with_assertions({_NS: _LOCAL_HUB}, {"OTHER-NS": (_PEER_HUB,)})
    async with running_hub(hub) as (_, uri):
        agent = await connect_agent(_AGENT, uri)
        try:
            await agent.agent.claim("T1", paths=["src"])
            granted = await agent.recorder.wait_for(lambda m: m.get("type") == "claim_granted")
            assert granted["task_id"] == "T1"
        finally:
            await close_agents(agent)


async def test_an_unowned_namespace_does_not_block_non_claim_frames() -> None:
    # The ownership gate is claim-specific: a hub that owns none of the agent's namespace must
    # still process the agent's other frames. Alice's chat reaches Bob even though the hub would
    # refuse Alice's claim in the same namespace.
    async with running_hub(_hub({_NS: _PEER_HUB})) as (_, uri):
        alice = await connect_agent(_AGENT, uri)
        bob = await connect_agent(f"{_NS}/bob", uri)
        try:
            await alice.agent.chat("hello")
            echoed = await bob.recorder.wait_for(
                lambda m: m.get("type") == "chat" and m.get("payload") == "hello"
            )
            assert echoed["sender"] == _AGENT
        finally:
            await close_agents(alice, bob)
