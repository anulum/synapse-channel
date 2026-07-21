# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — grant-broadcast recovery and advisory-wait gate contract
"""BUG-5 actor follow-up: recover a committed claim whose grant broadcast is lost.

BUG-5 makes a durable claim mutation atomic — the serialized actor appends to the
journal and publishes the live claim before it returns, and an in-flight
cancellation still waits for the authoritative append
(``test_cancellation_waits_for_authoritative_append_before_propagating``). The
grant *broadcast*, however, is emitted by :func:`handle_claim` only *after* the
actor returns, so a cancellation in that window (a dropped requester connection
or shutdown) can leave a claim that is durably held and live-visible yet never
announced.

These tests pin two things the follow-up asked to settle:

* the recovery contract — durable/live state stays aligned across both
  cancellation windows, and the owner recovers the lost announcement with an
  idempotent re-request that renews the same claim and re-broadcasts the grant
  without a second hold, journal divergence, or a duplicate quota charge; and
* the decision that advisory wait-request mutations do **not** need the actor
  gate: they are non-durable, mutated atomically without an event-loop yield,
  and an unrelated in-flight durable commit's ``publish_from`` never touches the
  wait graph, so serialising them would add cost without closing any hazard.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.core.handlers import leasing
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind, record_claim
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.state_models import TaskClaim


class _FakeSocket:
    """A stand-in websocket; the hub transport is stubbed out per test."""

    remote_address = ("127.0.0.1", 8876)


def _journalled_hub(path: Path) -> tuple[SynapseHub, EventStore]:
    """Return a hub whose claim log uses the supplied temporary path."""
    store = EventStore(path)
    return SynapseHub(journal=store, anti_rollback_checkpoint=False), store


def _record_transport(hub: SynapseHub) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Capture broadcasts and directed sends without a real socket."""
    broadcasts: list[dict[str, Any]] = []
    sends: list[dict[str, Any]] = []

    async def broadcast(message: dict[str, Any]) -> None:
        broadcasts.append(message)

    async def send_json(_websocket: Any, message: dict[str, Any]) -> None:
        sends.append(message)

    hub._broadcast = broadcast  # type: ignore[method-assign,assignment]
    hub._send_json = send_json  # type: ignore[method-assign,assignment]
    return broadcasts, sends


def _claim_body(task_id: str, path: str) -> dict[str, Any]:
    return {"task_id": task_id, "paths": [path]}


async def test_handler_cancel_during_grant_broadcast_keeps_claim_aligned(tmp_path: Path) -> None:
    """A cancellation while the grant is broadcasting cannot unwind durable truth."""
    hub, store = _journalled_hub(tmp_path / "events.db")
    started = asyncio.Event()
    unblock = asyncio.Event()
    broadcasts: list[dict[str, Any]] = []

    async def stalled_broadcast(message: dict[str, Any]) -> None:
        broadcasts.append(message)
        started.set()
        await unblock.wait()

    hub._broadcast = stalled_broadcast  # type: ignore[method-assign,assignment]

    task = asyncio.create_task(
        leasing.handle_claim(hub, "A", _claim_body("T1", "src/a.py"), _FakeSocket())
    )
    assert await asyncio.wait_for(_reached(started), 1.0)

    # The actor already committed and published before the broadcast began.
    assert hub.state.claims["T1"].owner == "A"
    assert [event.kind for event in store.read_all()] == [EventKind.CLAIM]

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Cancelling the in-flight broadcast leaves durable and live state aligned.
    assert hub.state.claims["T1"].owner == "A"
    assert [event.kind for event in store.read_all()] == [EventKind.CLAIM]
    assert len(broadcasts) == 1
    unblock.set()
    store.close()


async def test_actor_cancel_during_append_commits_without_announcing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cancelled mid-append: the claim commits and lives, but no grant is broadcast."""
    hub, store = _journalled_hub(tmp_path / "events.db")
    broadcasts, _sends = _record_transport(hub)
    append_started = threading.Event()
    allow_commit = threading.Event()

    def delayed_record_claim(target: EventStore, claim: TaskClaim) -> None:
        append_started.set()
        assert allow_commit.wait(timeout=2.0)
        record_claim(target, claim)

    monkeypatch.setattr(leasing, "record_claim", delayed_record_claim)
    task = asyncio.create_task(
        leasing.handle_claim(hub, "A", _claim_body("T1", "src/a.py"), _FakeSocket())
    )
    assert await asyncio.to_thread(append_started.wait, 1.0)
    task.cancel()
    allow_commit.set()

    with pytest.raises(asyncio.CancelledError):
        await task

    # The actor's own shield still commits and publishes the claim...
    assert hub.state.claims["T1"].owner == "A"
    assert [event.kind for event in store.read_all()] == [EventKind.CLAIM]
    # ...but handle_claim never reached the broadcast, so nobody was told.
    assert broadcasts == []
    store.close()


async def test_idempotent_reclaim_recovers_the_unannounced_grant(tmp_path: Path) -> None:
    """The owner recovers a lost announcement by re-requesting the same claim."""
    hub, store = _journalled_hub(tmp_path / "events.db")
    broadcasts, _sends = _record_transport(hub)

    # A committed-but-unannounced claim: apply_claim_async commits and publishes
    # through the actor but emits no grant broadcast (the lost-broadcast outcome).
    application = await leasing.apply_claim_async(hub, "A", _claim_body("T1", "src/a.py"))
    assert application.claim is not None
    assert hub.state.claims["T1"].owner == "A"
    assert broadcasts == []
    lease_before = hub.state.claims["T1"].lease_expires_at
    granted_before = hub.counters.claims_granted

    # Recovery: the owner re-requests the identical claim through the full handler.
    await leasing.handle_claim(hub, "A", _claim_body("T1", "src/a.py"), _FakeSocket())

    # Exactly one grant is now announced, for the same task and owner.
    assert len(broadcasts) == 1
    assert broadcasts[0]["task_id"] == "T1"
    assert broadcasts[0]["owner"] == "A"

    # No divergence: a single live hold, renewed (never earlier) lease, and a
    # journal that is only the original claim plus its renewal.
    assert list(hub.state.claims) == ["T1"]
    assert hub.state.claims["T1"].owner == "A"
    assert hub.state.claims["T1"].lease_expires_at >= lease_before
    assert [event.kind for event in store.read_all()] == [EventKind.CLAIM, EventKind.CLAIM]
    # The renewal is free for the same principal — the owner is not double-charged.
    assert hub.counters.claims_granted == granted_before + 1
    assert _principal_claim_count(hub, "A") == 1
    store.close()


async def test_advisory_wait_needs_no_actor_gate_and_survives_in_flight_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A wait registered during an unrelated in-flight append is not lost on publish."""
    hub, store = _journalled_hub(tmp_path / "events.db")
    _broadcasts, sends = _record_transport(hub)
    assert hub.state.claim("H", "T1", paths=["src/t1.py"])[0]

    append_started = threading.Event()
    allow_commit = threading.Event()

    def delayed_record_claim(target: EventStore, claim: TaskClaim) -> None:
        append_started.set()
        assert allow_commit.wait(timeout=2.0)
        record_claim(target, claim)

    monkeypatch.setattr(leasing, "record_claim", delayed_record_claim)
    claim_task = asyncio.create_task(
        leasing.apply_claim_async(hub, "A", _claim_body("T2", "src/t2.py"))
    )
    assert await asyncio.to_thread(append_started.wait, 1.0)

    # The append is stalled: its candidate is not published yet, so a waiter sees
    # the complete pre-commit live state. The advisory wait is recorded directly,
    # without taking the actor gate.
    await leasing.handle_wait_request(hub, "W", {"task_id": "T1"}, _FakeSocket())
    assert hub._waits.get("W") == {"T1"}
    assert sends and sends[-1]["task_id"] == "T1"

    # Publishing the unrelated durable commit does not touch the wait graph:
    # publish_from replaces SynapseState only, never the hub's advisory waits.
    allow_commit.set()
    await claim_task
    assert hub._waits.get("W") == {"T1"}
    assert "T1" in hub.state.claims
    assert "T2" in hub.state.claims
    store.close()


async def _reached(event: asyncio.Event) -> bool:
    """Await an event and report that it fired (keeps wait_for readable)."""
    await event.wait()
    return True


def _principal_claim_count(hub: SynapseHub, agent: str) -> int:
    """Count live claims a single agent holds, ignoring expiry bookkeeping."""
    return sum(1 for claim in hub.state.claims.values() if claim.owner == agent)
