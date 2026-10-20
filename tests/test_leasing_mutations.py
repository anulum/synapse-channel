# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — leasing mutations public runtime journeys

from __future__ import annotations

from pathlib import Path

import pytest

from hub_e2e_helpers import close_agents, connect_agent, running_hub
from synapse_channel.core.handlers import leasing
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind, replay
from synapse_channel.core.persistence import EventStore


async def test_public_claim_application_durably_refuses_a_missing_task_id(tmp_path: Path) -> None:
    with EventStore(tmp_path / "invalid-claim.db") as store:
        hub = SynapseHub(journal=store)
        async with running_hub(hub):
            result = await leasing.apply_claim_async(hub, "LEASE/owner", {"task_id": ""})
            assert not result.ok and result.claim is None
            assert result.reason_code == "TASK_ID_REQUIRED"
            assert hub.state.claims == {}
            events = tuple(store.read_all())
            assert len(events) == 1 and events[0].kind == EventKind.CLAIM_DENIAL
            assert events[0].payload["reason_code"] == "TASK_ID_REQUIRED"


async def test_public_nondurable_renewal_keeps_the_original_fence() -> None:
    hub = SynapseHub()
    async with running_hub(hub):
        acquired = await leasing.apply_claim_async(hub, "LEASE/owner", {"task_id": "lease"})
        assert acquired.ok and acquired.claim is not None
        epoch = acquired.claim.epoch
        renewed = leasing.apply_claim(
            hub, "LEASE/owner", {"task_id": "lease", "renew_epoch": epoch, "renewal_id": "renew-1"}
        )
        assert renewed.ok and renewed.claim is not None
        assert renewed.claim.epoch == epoch
        assert renewed.claim.owner == "LEASE/owner" and renewed.renewal_id == "renew-1"


@pytest.mark.parametrize(
    ("verb", "reply"),
    [
        ("claim", "claim_granted"),
        ("task_update", "task_updated"),
        ("checkpoint", "checkpoint_saved"),
        ("handoff", "handoff_granted"),
        ("release", "release_granted"),
    ],
)
async def test_public_handler_replay_keeps_a_single_durable_mutation(
    tmp_path: Path, verb: str, reply: str
) -> None:
    """Handlers retain idempotency when invoked by a caller outside the socket router."""
    with EventStore(tmp_path / "replay.db") as store:
        hub = SynapseHub(journal=store, hub_id="handler-replay")
        async with running_hub(hub) as (_, uri):
            owner = await connect_agent("REPLAY/owner", uri)
            recipient = await connect_agent("REPLAY/recipient", uri)
            try:
                if verb != "claim":
                    await owner.agent.claim("lease")
                    await owner.recorder.wait_for(lambda m: m.get("type") == "claim_granted")
                owner.recorder.messages.clear()
                frame = {
                    "sender": owner.agent.name,
                    "type": verb,
                    "task_id": "lease",
                    "idem_key": "durable-" + verb,
                    "note": "retained",
                    "checkpoint": "resume",
                    "to_agent": recipient.agent.name,
                }
                handler = getattr(leasing, "handle_" + verb)
                socket = hub.clients.agent_sockets[owner.agent.name]
                await handler(hub, owner.agent.name, frame, socket)
                first = await owner.recorder.wait_for(lambda m: m.get("type") == reply)
                events = tuple(store.read_all())
                operations = store.read_operations()
                assert len(operations) == 1
                owner.recorder.messages.clear()

                await handler(hub, owner.agent.name, frame, socket)

                repeated = await owner.recorder.wait_for(lambda m: m.get("type") == reply)
                assert repeated == first
                assert tuple(store.read_all()) == events
                assert store.read_operations() == operations
            finally:
                await close_agents(owner, recipient)


@pytest.mark.parametrize("durable", [False, True])
async def test_lease_lifecycle_keeps_coercion_checkpoint_and_handoff_fencing(
    tmp_path: Path, durable: bool
) -> None:
    store = EventStore(tmp_path / "lifecycle.db") if durable else None
    hub = SynapseHub(journal=store, require_fencing_epoch=True)
    try:
        async with running_hub(hub) as (_, uri):
            owner = await connect_agent("MUTATION/owner", uri)
            recipient = await connect_agent("MUTATION/recipient", uri)
            try:
                await owner.agent.claim("lease", note="original")
                grant = await owner.recorder.wait_for(lambda m: m.get("type") == "claim_granted")
                epoch = int(grant["epoch"])
                await owner.agent.send_message(
                    "task_update",
                    task_id="lease",
                    epoch=epoch,
                    status="",
                    note=False,
                    data_ref=19,
                )
                update = await owner.recorder.wait_for(lambda m: m.get("type") == "task_updated")
                assert (hub.state.claims["lease"].note, update["data_ref"]) == ("False", "19")
                await owner.agent.save_checkpoint("lease", "resume-token", epoch=epoch)
                await owner.recorder.wait_for(lambda m: m.get("type") == "checkpoint_saved")
                await owner.agent.handoff("lease", recipient.agent.name, epoch=epoch)
                handoff = await recipient.recorder.wait_for(
                    lambda m: m.get("type") == "handoff_granted"
                )
                assert handoff["checkpoint"] == "resume-token"
                assert handoff["note"] == "False" and int(handoff["epoch"]) > epoch
                await owner.agent.release("lease", epoch=epoch)
                await owner.recorder.wait_for(lambda m: m.get("type") == "release_denied")
                assert hub.state.claims["lease"].owner == recipient.agent.name
                await recipient.agent.release("lease", epoch=int(handoff["epoch"]))
                await recipient.recorder.wait_for(lambda m: m.get("type") == "release_granted")
                assert "lease" not in hub.state.claims
                if store is not None:
                    restored = replay(store).state
                    assert "lease" not in restored.claims
                    checkpoints = [
                        e.payload["checkpoint"]
                        for e in store.read_all()
                        if e.kind == EventKind.CHECKPOINT
                    ]
                    assert checkpoints == ["resume-token"]
            finally:
                await close_agents(owner, recipient)
    finally:
        if store is not None:
            store.close()
