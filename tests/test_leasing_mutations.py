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
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind, replay
from synapse_channel.core.persistence import EventStore


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
