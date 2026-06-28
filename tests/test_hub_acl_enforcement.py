# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real-socket tests for hub ACL enforcement

from __future__ import annotations

from websockets.asyncio.client import connect

from hub_e2e_helpers import collect_available, read_until_type, running_hub, send_json
from synapse_channel.core.acl import CLAIM, MESSAGE, AclPolicy, AclRule
from synapse_channel.core.hub import SynapseHub


def _policy() -> AclPolicy:
    return AclPolicy(
        [
            AclRule(CLAIM, "claim", "*", "P", "may hold P tasks"),
            AclRule(CLAIM, "path", "src/*", "P", "core may claim src"),
            AclRule(MESSAGE, "agent", "*", "P", "may message anyone"),
        ]
    )


def _enforcing_hub() -> SynapseHub:
    return SynapseHub(acl_policy=_policy(), require_acl=True)


async def test_allowed_claim_is_routed() -> None:
    async with running_hub(_enforcing_hub()) as (_hub, uri):
        async with connect(uri) as ws:
            await send_json(ws, sender="P/alice", type="heartbeat", target="System", payload="x")
            await send_json(ws, sender="P/alice", type="claim", task_id="t1", paths=["src/a.py"])
            granted = await read_until_type(ws, "claim_granted")
            assert granted["task_id"] == "t1"


async def test_denied_claim_is_refused_and_not_routed() -> None:
    async with running_hub(_enforcing_hub()) as (_hub, uri):
        async with connect(uri) as ws:
            await send_json(ws, sender="P/alice", type="heartbeat", target="System", payload="x")
            await send_json(ws, sender="P/alice", type="claim", task_id="t1", paths=["secrets/x"])
            error = await read_until_type(ws, "error")
            assert error["acl_decision"] == "would_deny"
            assert "access denied" in error["payload"]
            # The claim never reached the leasing handler.
            messages = await collect_available(ws, duration=0.2)
            assert not any(m.get("type") == "claim_granted" for m in messages)


async def test_out_of_namespace_identity_is_denied() -> None:
    async with running_hub(_enforcing_hub()) as (_hub, uri):
        async with connect(uri) as ws:
            await send_json(ws, sender="OTHER/bob", type="heartbeat", target="System", payload="x")
            await send_json(ws, sender="OTHER/bob", type="claim", task_id="t1", paths=["src/a.py"])
            error = await read_until_type(ws, "error")
            assert error["acl_decision"] == "would_deny"


async def test_ungated_chat_to_allowed_target_passes() -> None:
    async with running_hub(_enforcing_hub()) as (_hub, uri):
        async with connect(uri) as alice, connect(uri) as bob:
            await send_json(bob, sender="P/bob", type="heartbeat", target="System", payload="x")
            await send_json(alice, sender="P/alice", type="heartbeat", target="System", payload="x")
            await send_json(alice, sender="P/alice", type="chat", target="P/bob", payload="hi")
            delivered = await read_until_type(bob, "chat")
            assert delivered["payload"] == "hi"


async def test_enforcement_off_is_transparent() -> None:
    # Same denying policy, but require_acl is off: nothing is blocked.
    hub = SynapseHub(acl_policy=_policy(), require_acl=False)
    async with running_hub(hub) as (_hub, uri):
        async with connect(uri) as ws:
            await send_json(ws, sender="P/alice", type="heartbeat", target="System", payload="x")
            await send_json(ws, sender="P/alice", type="claim", task_id="t1", paths=["secrets/x"])
            granted = await read_until_type(ws, "claim_granted")
            assert granted["task_id"] == "t1"
