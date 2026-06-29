# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — unit tests for the hub's idempotency/quota/message-id ledger

from __future__ import annotations

from pathlib import Path
from typing import Any

from synapse_channel.core.hub_ledger_guard import HubLedgerGuard
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType


def _guard(
    *,
    max_findings_per_agent: int = 3,
    journal: EventStore | None = None,
    message_seq: int = 0,
    finding_counts: dict[str, int] | None = None,
    idempotency_seed: tuple[tuple[str, dict[str, Any]], ...] = (),
) -> HubLedgerGuard:
    return HubLedgerGuard(
        max_findings_per_agent=max_findings_per_agent,
        journal=journal,
        message_seq=message_seq,
        finding_counts=finding_counts,
        idempotency_seed=idempotency_seed,
    )


def test_next_msg_id_is_strictly_increasing_from_the_seed() -> None:
    guard = _guard(message_seq=5)
    assert guard.message_seq == 5
    assert guard.next_msg_id() == 6
    assert guard.next_msg_id() == 7
    assert guard.message_seq == 7


def test_idempotency_key_reads_the_client_field_or_empty() -> None:
    assert HubLedgerGuard.idempotency_key({"idem_key": "k1"}) == "k1"
    assert HubLedgerGuard.idempotency_key({"idem_key": ""}) == ""
    assert HubLedgerGuard.idempotency_key({}) == ""


def test_remember_caches_keyed_responses_only() -> None:
    guard = _guard()
    guard.remember({"idem_key": "k1"}, {"type": "claim_granted"})
    assert "k1" in guard.idempotency
    assert guard.idempotency.get("k1") == {"type": "claim_granted"}

    # A response without an idempotency key is never cached.
    guard.remember({}, {"type": "claim_granted"})
    assert guard.idempotency.get("") is None


def test_remember_journals_when_a_log_is_attached(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    guard = _guard(journal=store)

    guard.remember({"idem_key": "k1"}, {"type": "claim_granted", "task_id": "T1"})

    assert "k1" in guard.idempotency
    assert store.count() == 1


def test_reserve_finding_slot_admits_until_the_quota_then_rejects() -> None:
    guard = _guard(max_findings_per_agent=2)

    first_ok, _ = guard.reserve_finding_slot("A")
    second_ok, _ = guard.reserve_finding_slot("  A  ")  # whitespace is stripped to one owner
    third_ok, reason = guard.reserve_finding_slot("A")

    assert first_ok is True
    assert second_ok is True
    assert third_ok is False
    assert "quota" in reason


def test_finding_counts_resume_from_the_seed() -> None:
    guard = _guard(max_findings_per_agent=2, finding_counts={"A": 2})
    admitted, reason = guard.reserve_finding_slot("A")
    assert admitted is False
    assert "reached" in reason


async def test_maybe_replay_duplicate_replays_a_cached_mutation() -> None:
    guard = _guard(idempotency_seed=(("k1", {"type": "claim_granted"}),))
    sent: list[tuple[Any, dict[str, Any]]] = []

    async def _send(websocket: Any, data: dict[str, Any]) -> None:
        sent.append((websocket, data))

    socket = object()
    replayed = await guard.maybe_replay_duplicate(
        MessageType.CLAIM, {"idem_key": "k1"}, socket, _send
    )

    assert replayed is True
    assert sent == [(socket, {"type": "claim_granted"})]


async def test_maybe_replay_duplicate_passes_through_non_duplicates() -> None:
    guard = _guard(idempotency_seed=(("k1", {"type": "claim_granted"}),))

    async def _send(websocket: Any, data: dict[str, Any]) -> None:  # pragma: no cover
        raise AssertionError("a pass-through must not re-send anything")

    socket = object()

    # Not a mutating type -> never a duplicate.
    assert (
        await guard.maybe_replay_duplicate(MessageType.CHAT, {"idem_key": "k1"}, socket, _send)
        is False
    )
    # Mutating but no idempotency key.
    assert await guard.maybe_replay_duplicate(MessageType.CLAIM, {}, socket, _send) is False
    # Mutating, keyed, but a cache miss.
    assert (
        await guard.maybe_replay_duplicate(MessageType.CLAIM, {"idem_key": "miss"}, socket, _send)
        is False
    )
