# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — track directed chats awaiting a deferred delivery receipt
"""Track directed chats that dead-lettered but may still be delivered later.

When a sender asks for a delivery receipt on a directed message and no recipient
is live, the hub answers ``delivered: false`` at once — honest for that instant,
but it never revises the verdict when the recipient reconnects and drains the
message from the journal backlog. This store closes that gap: it remembers the
few facts a *deferred* receipt needs — the durable journal ``seq``, who sent it,
and the target it was addressed to — keyed on the ``seq`` the reconnecting
recipient echoes back in its ``ACK``. On that ack the hub can finally tell the
original sender ``delivered: true, deferred: true`` and forget the entry.

It holds no message body (the journal already does) and is bounded: a flood of
receipt-requested messages to blackholed targets evicts the oldest pending entry
rather than growing the hub. An entry that is never acked — the recipient never
comes back — simply ages out under that bound, because a deferred receipt is a
best-effort upgrade of the immediate ``delivered: false``, never a guarantee.
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_PENDING_RECEIPTS = 1024
"""Bounded number of directed messages tracked for a deferred receipt.

A receipt-requested directed message that reaches no live recipient adds one
entry; the oldest is evicted once the bound is crossed, so a burst of messages to
blackholed targets cannot grow the hub without limit. The window is generous
because an entry is small (a seq and two names) and is claimed the moment its
recipient reconnects and acks, so only genuinely unacked messages occupy slots.
"""


@dataclass(frozen=True)
class ReceiptEntry:
    """The sender and target a deferred delivery receipt must be addressed from and to.

    Attributes
    ----------
    sender : str
        Who sent the directed message and awaits its deferred receipt.
    target : str
        The name, project, or role the message was addressed to — checked against
        an acking client so only a genuine recipient can trigger the receipt.
    """

    sender: str
    target: str


class PendingReceipts:
    """Bounded ``seq -> (sender, target)`` map of directed chats awaiting an ack.

    Parameters
    ----------
    max_entries : int
        Distinct sequence numbers retained (floored at ``1``); remembering one
        beyond the bound evicts the oldest still-pending entry, so a flood of
        receipt-requested messages to blackholed targets cannot grow the hub.
    """

    def __init__(self, max_entries: int = DEFAULT_PENDING_RECEIPTS) -> None:
        self.max_entries = max(1, int(max_entries))
        self._entries: dict[int, ReceiptEntry] = {}

    def __len__(self) -> int:
        """Return how many directed messages are currently awaiting a deferred receipt."""
        return len(self._entries)

    def remember(self, seq: int, *, sender: str, target: str) -> None:
        """Record that the directed message at ``seq`` awaits a deferred receipt.

        Re-remembering a known ``seq`` refreshes it to newest so the pair stays
        consistent and the entry outlives eviction as long as it keeps recurring.
        Once the store is over its bound the oldest still-pending entry is dropped.

        Parameters
        ----------
        seq : int
            The durable journal sequence number of the directed message.
        sender : str
            Who sent it and awaits confirmation.
        target : str
            The name, project, or role it was addressed to.
        """
        self._entries.pop(seq, None)
        self._entries[seq] = ReceiptEntry(sender=sender, target=target)
        if len(self._entries) > self.max_entries:
            oldest = next(iter(self._entries))
            del self._entries[oldest]

    def peek(self, seq: int) -> ReceiptEntry | None:
        """Return the pending entry for ``seq`` without removing it, or ``None``.

        Used to authorise an ack against the target before the entry is claimed,
        so a spoofed ack from a non-recipient cannot destroy a genuine one.

        Parameters
        ----------
        seq : int
            The sequence number an ack referenced.

        Returns
        -------
        ReceiptEntry or None
            The awaiting sender and target, or ``None`` when nothing is pending.
        """
        return self._entries.get(seq)

    def claim(self, seq: int) -> ReceiptEntry | None:
        """Remove and return the pending entry for ``seq``, or ``None`` if absent.

        Claiming is idempotent: a second ack for the same ``seq`` finds nothing and
        raises no deferred receipt, so a recipient that acks a replayed backlog more
        than once still confirms its sender exactly once.

        Parameters
        ----------
        seq : int
            The sequence number to settle.

        Returns
        -------
        ReceiptEntry or None
            The claimed sender and target, or ``None`` when nothing was pending.
        """
        return self._entries.pop(seq, None)
