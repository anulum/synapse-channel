# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — bounded pending-receipt store for deferred delivery receipts

from __future__ import annotations

from synapse_channel.core.pending_receipts import (
    DEFAULT_PENDING_RECEIPTS,
    PendingReceipts,
    ReceiptEntry,
)


class TestRememberPeekClaim:
    def test_peek_returns_the_remembered_sender_and_target(self) -> None:
        store = PendingReceipts()
        store.remember(7, sender="ALICE", target="BOB", message_id=3)
        assert store.peek(7) == ReceiptEntry(sender="ALICE", target="BOB", message_id=3)

    def test_peek_leaves_the_entry_in_place(self) -> None:
        # Peek authorises an ack before it is settled, so it must not consume the entry.
        store = PendingReceipts()
        store.remember(7, sender="ALICE", target="BOB", message_id=3)
        store.peek(7)
        assert len(store) == 1
        assert store.peek(7) is not None

    def test_client_message_identity_survives_pending_storage(self) -> None:
        store = PendingReceipts()
        store.remember(
            7,
            sender="ALICE",
            target="BOB",
            message_id=3,
            client_msg_id="retry-7",
        )
        assert store.peek(7) == ReceiptEntry(
            sender="ALICE",
            target="BOB",
            message_id=3,
            client_msg_id="retry-7",
        )

    def test_claim_pops_and_returns_the_entry(self) -> None:
        store = PendingReceipts()
        store.remember(7, sender="ALICE", target="BOB", message_id=3)
        assert store.claim(7) == ReceiptEntry(sender="ALICE", target="BOB", message_id=3)
        assert len(store) == 0

    def test_claim_is_idempotent_so_a_double_ack_confirms_once(self) -> None:
        # A recipient that acks a replayed backlog twice must settle its sender once.
        store = PendingReceipts()
        store.remember(7, sender="ALICE", target="BOB", message_id=3)
        assert store.claim(7) is not None
        assert store.claim(7) is None

    def test_peek_and_claim_of_an_unknown_seq_are_none(self) -> None:
        store = PendingReceipts()
        assert store.peek(99) is None
        assert store.claim(99) is None
        assert len(store) == 0


class TestReRemember:
    def test_re_remember_updates_the_pair(self) -> None:
        # A seq re-used after a restart resets to its current sender and target.
        store = PendingReceipts()
        store.remember(7, sender="ALICE", target="BOB", message_id=3)
        store.remember(7, sender="CAROL", target="DAVE", message_id=4)
        assert store.peek(7) == ReceiptEntry(sender="CAROL", target="DAVE", message_id=4)
        assert len(store) == 1

    def test_re_remember_refreshes_recency_against_eviction(self) -> None:
        # Re-remembering the oldest entry moves it to newest, so the *next* eviction
        # drops what is now the stalest instead of the just-refreshed seq.
        store = PendingReceipts(max_entries=2)
        store.remember(1, sender="A", target="B", message_id=1)
        store.remember(2, sender="A", target="C", message_id=2)
        store.remember(1, sender="A", target="B", message_id=1)  # refresh 1 -> newest
        evicted = store.remember(3, sender="A", target="D", message_id=3)
        assert store.peek(1) is not None
        assert store.peek(2) is None
        assert store.peek(3) is not None
        assert evicted == (2, ReceiptEntry(sender="A", target="C", message_id=2))


class TestBounded:
    def test_oldest_entry_is_evicted_beyond_the_bound(self) -> None:
        store = PendingReceipts(max_entries=2)
        store.remember(1, sender="A", target="B", message_id=1)
        store.remember(2, sender="A", target="C", message_id=2)
        store.remember(3, sender="A", target="D", message_id=3)
        assert len(store) == 2
        assert store.peek(1) is None
        assert store.peek(2) is not None
        assert store.peek(3) is not None

    def test_max_entries_is_floored_at_one(self) -> None:
        store = PendingReceipts(max_entries=0)
        store.remember(1, sender="A", target="B", message_id=1)
        store.remember(2, sender="A", target="C", message_id=2)
        assert len(store) == 1
        assert store.peek(2) is not None

    def test_default_bound_is_the_module_constant(self) -> None:
        assert PendingReceipts().max_entries == DEFAULT_PENDING_RECEIPTS

    def test_restore_replays_oldest_first_under_the_live_bound(self) -> None:
        store = PendingReceipts(max_entries=2)
        store.restore(
            (
                (1, ReceiptEntry(sender="A", target="B", message_id=1)),
                (2, ReceiptEntry(sender="A", target="C", message_id=2)),
                (3, ReceiptEntry(sender="A", target="D", message_id=3)),
            )
        )
        assert store.entries() == (
            (2, ReceiptEntry(sender="A", target="C", message_id=2)),
            (3, ReceiptEntry(sender="A", target="D", message_id=3)),
        )
