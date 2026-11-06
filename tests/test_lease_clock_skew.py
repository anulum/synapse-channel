# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — only the authoritative hub's clock sets and ends a lease
"""Pin the lease clock model of INV-CK-3 against skewed and stepped clocks."""

from __future__ import annotations

import json
import time

from websockets.asyncio.client import connect

from hub_e2e_helpers import read_until_type, running_hub
from synapse_channel.cli_lock_supervision import STOP_ALLOWANCE, lease_duration
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.state import SynapseState

SKEWED = "P/skewed"


async def test_a_claims_own_time_fields_cannot_move_its_lease() -> None:
    hub = SynapseHub(hub_id="skew")
    async with running_hub(hub) as (_, uri), connect(uri) as websocket:
        await read_until_type(websocket, MessageType.WELCOME)
        registration = {
            "sender": SKEWED,
            "type": MessageType.HEARTBEAT,
            "target": "System",
            "payload": "online",
        }
        await websocket.send(json.dumps(registration))
        before = time.time()
        await websocket.send(
            json.dumps(
                {
                    "sender": SKEWED,
                    "type": MessageType.CLAIM,
                    "target": "System",
                    "task_id": "T-SKEW",
                    "paths": ["src/a.py"],
                    "ttl_seconds": 120,
                    # A client whose clock is far ahead, plus forged lease fields.
                    "timestamp": time.time() + 10_000_000,
                    "claimed_at": 0,
                    "lease_expires_at": 1e15,
                    "lease_duration_seconds": 1e9,
                }
            )
        )
        granted = await read_until_type(websocket, MessageType.CLAIM_GRANTED)
        after = time.time()
    claim = hub.state.claims["T-SKEW"]
    assert granted["lease_duration_seconds"] == 120
    assert before + 120 <= granted["lease_expires_at"] <= after + 120
    assert before <= claim.claimed_at <= after


def test_a_forward_hub_clock_step_ends_the_lease_and_fencing_refuses_the_old_holder() -> None:
    state = SynapseState(default_ttl_seconds=300)
    assert state.claim("P/a", "T1", now=1000.0, paths=["src/a.py"])[0]
    old = state.claims["T1"].epoch
    # The hub clock steps forward past the lease: it has ended, and another agent takes it.
    assert state.claim("P/b", "T1", now=1400.0, paths=["src/a.py"])[0]
    assert state.claims["T1"].owner == "P/b"
    assert state.claims["T1"].epoch > old
    assert state.release("P/a", "T1", now=1400.5, epoch=old) == (
        False,
        "Task 'T1' is owned by P/b, not P/a.",
    )
    renewal = state.renew_claim("P/a", "T1", now=1400.5, epoch=old, paths=("src/a.py",))
    assert renewal == (False, "Task 'T1' renewal lost its original ownership.")
    assert state.claims["T1"].owner == "P/b"


def test_after_a_clock_step_the_same_name_reclaiming_fences_its_older_process() -> None:
    state = SynapseState(default_ttl_seconds=300)
    assert state.claim("P/a", "T1", now=1000.0, paths=["src/a.py"])[0]
    old = state.claims["T1"].epoch
    # The lease ends early on a forward step; a new process of the same name claims again.
    assert state.claim("P/a", "T1", now=1400.0, paths=["src/a.py"])[0]
    current = state.claims["T1"].epoch
    assert current > old
    released, detail = state.release("P/a", "T1", now=1400.5, epoch=old)
    updated, update_detail = state.update_task("P/a", "T1", status="done", epoch=old, now=1400.5)
    assert (released, updated) == (False, False)
    assert "epoch is stale" in detail
    assert "epoch is stale" in update_detail
    assert state.release("P/a", "T1", now=1400.5, epoch=current)[0]


def test_a_backward_hub_clock_step_never_frees_the_lease_and_keeps_its_duration() -> None:
    state = SynapseState(default_ttl_seconds=300)
    assert state.claim("P/a", "T1", now=1000.0, paths=["src/a.py"])[0]
    epoch = state.claims["T1"].epoch
    # The hub clock steps back: the lease is still held, and a renewal keeps its length.
    assert state.claim("P/b", "T1", now=900.0, paths=["src/a.py"])[0] is False
    assert state.renew_claim("P/a", "T1", now=900.0, epoch=epoch, paths=("src/a.py",))[0]
    claim = state.claims["T1"]
    assert claim.owner == "P/a"
    assert claim.lease_expires_at - claim.claimed_at == 300


def test_lock_supervision_times_a_lease_only_by_its_relative_duration() -> None:
    # Absolute hub timestamps are never read, so a skewed client clock cannot misjudge them.
    skewed = {"lease_duration_seconds": 600.0, "lease_expires_at": 0.0, "claimed_at": 4e9}
    assert lease_duration(skewed) == 600.0
    assert lease_duration({"lease_expires_at": 1e15, "claimed_at": 0.0}) is None
    assert lease_duration({"lease_duration_seconds": STOP_ALLOWANCE}) is None


def test_a_holder_cannot_renew_a_lease_that_expired_unswept() -> None:
    state = SynapseState(default_ttl_seconds=300)
    assert state.claim("P/a", "T1", now=1000.0, paths=["src/a.py"])[0]
    epoch = state.claims["T1"].epoch
    # Nothing else touches the state before the renewal arrives past the expiry.
    renewal = state.renew_claim("P/a", "T1", now=1300.0, epoch=epoch, paths=("src/a.py",))
    assert renewal == (False, "Task 'T1' renewal lost its original ownership.")
    assert "T1" not in state.claims
    # Just before the expiry the same renewal is granted and keeps the duration.
    assert state.claim("P/a", "T2", now=2000.0, paths=["src/b.py"])[0]
    second = state.claims["T2"].epoch
    assert state.renew_claim("P/a", "T2", now=2299.0, epoch=second, paths=("src/b.py",))[0]
    assert state.claims["T2"].lease_expires_at == 2599.0
