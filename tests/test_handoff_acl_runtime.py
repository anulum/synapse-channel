# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — actual lease handoff ACL target consistency
"""Check handoff admission and durable transfer over actual hub sockets."""

from __future__ import annotations

from pathlib import Path

import pytest
from websockets.asyncio.client import ClientConnection, connect

from hub_e2e_helpers import read_json, read_until_type, running_hub, send_json
from synapse_channel.core.acl import CLAIM, AclPolicy, AclRule
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.persistence import EventStore

_OWNER = "P/handoff-owner"
_RECIPIENT = "P/handoff-recipient"


async def handoff_reply(connection: ClientConnection) -> dict[str, object]:
    """Return the actual ACL refusal, handler denial, or durable grant."""
    for _ in range(20):
        frame = await read_json(connection)
        if frame.get("type") in {"error", "handoff_denied", "handoff_granted"}:
            return frame
    raise AssertionError("handoff did not produce a terminal wire reply")


@pytest.mark.parametrize(
    "selectors",
    [
        {"payload": "allowed"},
        {"task_id": "", "payload": "allowed"},
        {"id": "allowed", "payload": "allowed"},
        {"task_id": "  ", "payload": "allowed"},
        {"task_id": "restricted", "payload": "allowed", "id": "allowed"},
        {"task_id": "allowed", "paths": ["src/module/../secret.py"]},
    ],
)
async def test_handoff_acl_uses_only_its_actual_task_and_preserves_path_checks(
    tmp_path: Path, selectors: dict[str, object]
) -> None:
    """Ignored aliases cannot choose an ACL target; legitimate handoff still persists."""
    with EventStore(tmp_path / "handoff.db") as journal:
        async with running_hub(SynapseHub(journal=journal, hub_id="handoff-test")) as (_, uri):
            async with connect(uri) as sender:
                await send_json(sender, type="heartbeat", sender=_OWNER, payload="online")
                for task in ("allowed", "restricted"):
                    await send_json(
                        sender,
                        type="claim",
                        sender=_OWNER,
                        task_id=task,
                        paths=[f"src/{task}.py"],
                    )
                    assert (await read_until_type(sender, "claim_granted"))["task_id"] == task

        policy = AclPolicy(
            [AclRule(CLAIM, "claim", "allowed", "P"), AclRule(CLAIM, "path", "src/*", "P")]
        )
        hub = SynapseHub(
            journal=journal, hub_id="handoff-test", acl_policy=policy, require_acl=True
        )
        before = journal.max_seq()
        async with running_hub(hub) as (_, uri):
            async with connect(uri) as receiver, connect(uri) as sender:
                await send_json(receiver, type="heartbeat", sender=_RECIPIENT, payload="online")
                await send_json(receiver, type="state_request", sender=_RECIPIENT)
                await read_until_type(receiver, "state_snapshot")
                await send_json(sender, type="heartbeat", sender=_OWNER, payload="online")
                await send_json(
                    sender, type="handoff", sender=_OWNER, to_agent=_RECIPIENT, **selectors
                )
                refused = await handoff_reply(sender)
                assert refused["type"] == "error", refused
                assert refused["acl_decision"] == "would_deny"
                assert {k: v.owner for k, v in hub.state.claims.items()} == {
                    "allowed": _OWNER,
                    "restricted": _OWNER,
                }
                assert not [e for e in journal.read_since(before) if e.kind == "handoff"]

                await send_json(
                    sender,
                    type="handoff",
                    sender=_OWNER,
                    to_agent=_RECIPIENT,
                    task_id=" allowed ",
                    payload="restricted",
                    id="restricted",
                    paths=["src//allowed.py"],
                )
                granted = await handoff_reply(sender)
                assert granted["type"] == "handoff_granted", granted
                assert granted["task_id"] == "allowed" and granted["owner"] == _RECIPIENT
                assert hub.state.claims["allowed"].owner == _RECIPIENT
                assert hub.state.claims["restricted"].owner == _OWNER
        transfers = [e.payload for e in journal.read_since(before) if e.kind == "handoff"]
        assert len(transfers) == 1
        assert transfers[0]["task_id"] == "allowed" and transfers[0]["owner"] == _RECIPIENT
