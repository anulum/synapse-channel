# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real-hub recovery tests for corrupt durable event rows

from __future__ import annotations

import json
from pathlib import Path

from hub_e2e_helpers import close_agents, connect_agent, http_get, running_hub
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import record_claim
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.state import TaskClaim


def _claim(task_id: str, *, epoch: int) -> TaskClaim:
    return TaskClaim(
        task_id=task_id,
        owner="seed-owner",
        note="seed",
        claimed_at=1000.0,
        lease_expires_at=9_999_999_999.0,
        status="claimed",
        data_ref="",
        worktree="wt",
        paths=("src",),
        epoch=epoch,
    )


async def test_real_hub_is_inspectable_but_refuses_mutations_after_corrupt_replay(
    tmp_path: Path,
) -> None:
    store = EventStore(tmp_path / "events.db")
    record_claim(store, _claim("SAFE", epoch=1))
    record_claim(store, _claim("CORRUPT", epoch=2))
    corrupt_seq = store.max_seq()
    store._conn.execute(
        "UPDATE events SET payload = 'operator-secret-not-json' WHERE seq = ?",
        (corrupt_seq,),
    )
    store._conn.commit()
    before_count = store.count()
    hub = SynapseHub(journal=store, enable_metrics=True, clock=lambda: 2000.0)

    async with running_hub(hub) as (_, uri):
        status, _, body = await http_get(uri, "/health")
        health = json.loads(body)
        assert status == 200
        assert health["status"] == "degraded"
        assert health["journal_corrupt_rows"] == 1

        alice = await connect_agent("alice", uri)
        try:
            await alice.agent.send_message("state_request")
            snapshot = await alice.recorder.wait_for(
                lambda message: message.get("type") == "state_snapshot"
            )
            assert "SAFE" in str(snapshot)

            await alice.agent.claim("NEW")
            denial = await alice.recorder.wait_for(
                lambda message: message.get("journal_recovery_required") is True
            )
            assert denial["type"] == "error"
            assert denial["first_corrupt_seq"] == corrupt_seq
            assert "operator-secret-not-json" not in str(denial)
        finally:
            await close_agents(alice)

    assert "NEW" not in hub.state.claims
    assert store.count() == before_count
    store.close()
