# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — wake adapter: a waiter whose exit the client's harness reports
"""The armed waiter as a wake adapter.

Some clients cannot be woken from outside. Their session keeps a waiter
process in the background instead; the hub writes a directed message to that
waiter, the waiter exits, and the client's own harness tells the session that
it did. Nothing in that path can be started by a relay: the wake has either
already happened when the hub delivered the message, or it happens when a
receiver of the seat later takes the message from the seat's mailbox, or not
at all. Whether a receiver that connects later is handed a held message
depends on how that receiver was started: a plain waiter is not, a waiter
that reads the mailbox can be.

This adapter therefore **does nothing to the seat**. It reads the hub's own
receipt state of the message, read-only, and says what that state shows. It exists so
that such a wake is named in marks, receipts and rankings like any other, and
so that a second adapter does not wake a seat its waiter woke or may wake.

The hub keeps one receipt state per message for which a receipt was asked.
What the adapter answers for each:

=========================  ==============================================
Receipt state              Answer
=========================  ==============================================
delivered at once, the     ``accepted``: the hub wrote the message to a
seat is a recipient and    waiter of the seat.
its receiver is passive
delivered at once, the     ``refused``: another kind of receiver took it,
seat's receiver is of      or the message went to others. Another adapter
another declared kind, or  may run.
the seat is no recipient
pending, or expired        ``uncertain``: the message waits in the
                           mailbox, and a receiver that reads the mailbox
                           may still take it. Waking the seat another way
                           now could wake it twice. An expired receipt
                           means the hub stopped tracking the receipt,
                           not that the message left the mailbox.
delivered at once, the     ``uncertain``: nothing shows whether the
seat's receiver kind is    receiver was a waiter.
not declared
delivered later            ``accepted`` with a passive receiver's named
                           acknowledgement; otherwise ``uncertain``.
asked, not yet decided;    ``uncertain`` once the time limit has passed.
or no receipt at all
=========================  ==============================================

An immediate ``accepted`` proves ``written_to_passive_receiver`` and no more: the receipt
shows a delivery to a receiver that declared itself passive. It does not show
that this receiver was a waiter a harness watches, that the waiter exited, or
that the session was told. Accepted is not read.

A deferred ``accepted`` proves ``acknowledged_by_passive_receiver``: the actual
registered receiver sent an ACK while declaring itself passive. It is not a
transport-write proof. Older receipts without this provenance remain uncertain.

``uncertain`` ends the work for a message, and the only definite refusal is
a delivery at once that went elsewhere. The adapter therefore belongs only in
the adapter list of a seat whose wake path is its waiter, and only where the
hub keeps a receipt for every message the relay may be asked about. For any
other seat it would stop a wake that another adapter could have made.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from synapse_channel.core.event_row_mac import RowMacKey
from synapse_channel.core.persistence_receipt_view import (
    ReceiptProjectionError,
    read_delivery_receipt,
)
from synapse_channel.core.receipt_receiver_evidence import TRANSPORT_RESULTS
from synapse_channel.core.wake_capability import WAKE_PASSIVE, WAKE_UNKNOWN
from synapse_channel.wake_adapters import (
    ACCEPTED,
    REFUSED,
    UNCERTAIN,
    Availability,
    WakeOutcome,
    WakeTarget,
    validate_deadline,
)

KIND = "harness_waiter"
"""The adapter's name in marks and receipts."""

PROVES = "written_to_passive_receiver"
"""What an accepted wake of this adapter shows: a write, not a harness's notice."""

ACK_PROVES = "acknowledged_by_passive_receiver"
"""A deferred receipt proves an acknowledgement, not a transport write."""

DELIVERED_AT_ONCE = "immediate_delivered"
PENDING = "pending"
DELIVERED_LATER = "deferred"
EXPIRED = "expired"
"""Receipt states of the hub's store that decide an answer."""

POLL_SECONDS = 0.05
"""Pause between two looks at a receipt that is not decided yet."""


@dataclass(frozen=True, slots=True)
class ReceiptView:
    """The receipt state of one message and the receipt that set it."""

    state: str
    receipt: Mapping[str, object]


ReceiptOf = Callable[[int], ReceiptView | None]
"""Returns the receipt state of the message with that sequence number, or None."""

Sleeper = Callable[[float], None]
Clock = Callable[[], float]


def journal_receipts(
    db_path: str | Path,
    *,
    key_file: str | Path | None = None,
    row_mac: RowMacKey | None = None,
) -> ReceiptOf:
    """Return a reader of receipt states from a hub's event store.

    The store is only read. Every look opens the existing file read-only,
    reads the receipt state of one message together with the journal entry
    that set it, and closes the file again; nothing is created, migrated or
    changed, and a look made later sees what the hub wrote in between.

    Parameters
    ----------
    db_path : str or pathlib.Path
        Path of the hub's event store.
    key_file : str or pathlib.Path or None
        Owner-only key when the store is encrypted.
    row_mac : RowMacKey or None
        The enrolled row key of a store whose entries are authenticated.
        Without it such a store gives no receipt.

    Returns
    -------
    ReceiptOf
        A function returning the receipt state of one message, or None when
        the store is absent, busy, of an unknown layout or cannot vouch for
        the receipt. None is neither a delivery nor a refusal.

    Raises
    ------
    ReceiptProjectionError
        From the returned function, when a reader for an encrypted store
        cannot be set up.
    """
    path = Path(db_path)

    def receipt_of(message_seq: int) -> ReceiptView | None:
        view = read_delivery_receipt(path, message_seq, key_file=key_file, row_mac=row_mac)
        return None if view is None else ReceiptView(view.state, view.receipt)

    return receipt_of


class HarnessWaiterAdapter:
    """Report a wake that the hub's delivery to an armed waiter already was."""

    def __init__(
        self,
        receipt_of: ReceiptOf,
        *,
        sleeper: Sleeper = time.sleep,
        clock: Clock = time.monotonic,
        expected_target: WakeTarget | None = None,
    ) -> None:
        """Keep the read-only receipt source and optional exact owner-declared target."""
        self._receipt_of = receipt_of
        self._sleeper = sleeper
        self._clock = clock
        self._expected_target = expected_target

    @property
    def kind(self) -> str:
        """Return ``harness_waiter``."""
        return KIND

    def available(self, target: WakeTarget) -> Availability:
        """Say whether a receipt can be asked about for this seat.

        Whether a waiter took one message is known only per message, so this
        answers for the seat alone: a seat must be named.
        """
        if not target.seat:
            return Availability(False, "no seat is named")
        if self._expected_target is not None and target != self._expected_target:
            return Availability(
                False, "the seat, native session or execution host differs from its binding"
            )
        return Availability(True)

    def wake(
        self, target: WakeTarget, wake_id: int, prompt: str, deadline_seconds: float
    ) -> WakeOutcome:
        """Read the receipt of message ``wake_id`` and say what it shows.

        Nothing is sent to the seat and the prompt is not used.
        """
        del prompt
        if not target.seat:
            return WakeOutcome(REFUSED, "", "not asked: no seat is named")
        if isinstance(wake_id, bool) or not isinstance(wake_id, int) or wake_id < 1:
            return WakeOutcome(REFUSED, "", "not asked: no message is named")
        if not self.available(target).available:
            return WakeOutcome(REFUSED, "", "not asked: the target differs from its binding")
        try:
            seconds = validate_deadline(deadline_seconds)
        except ValueError:
            return WakeOutcome(REFUSED, "", "not asked: invalid wake deadline")
        end = self._clock() + seconds
        while True:
            try:
                view = self._receipt_of(wake_id)
            except ReceiptProjectionError:
                return WakeOutcome(UNCERTAIN, "", "the receipt reader could not be set up")
            outcome = None if view is None else _judge(view, target.seat)
            if outcome is not None:
                return outcome
            if self._clock() >= end:
                return WakeOutcome(
                    UNCERTAIN, "", "no decided receipt of the message within the time limit"
                )
            self._sleeper(POLL_SECONDS)


def _judge(view: ReceiptView, seat: str) -> WakeOutcome | None:
    """Return what one receipt state shows about a waiter of ``seat``.

    None means that the hub has not decided the message yet.
    """
    if view.state in {PENDING, EXPIRED}:
        return WakeOutcome(
            UNCERTAIN, "", "the message waits in the mailbox; a later receiver may take it"
        )
    if view.state not in {DELIVERED_AT_ONCE, DELIVERED_LATER}:
        return None
    recipients = view.receipt.get("recipients")
    if not isinstance(recipients, list) or seat not in recipients:
        return WakeOutcome(REFUSED, "", "the receipt does not list the seat as a recipient")
    capabilities = view.receipt.get("recipient_wake_capabilities")
    capability = capabilities.get(seat) if isinstance(capabilities, Mapping) else None
    if view.state == DELIVERED_LATER:
        receiver = view.receipt.get("ack_receiver")
        if not isinstance(receiver, str) or not receiver or capability != WAKE_PASSIVE:
            return WakeOutcome(
                UNCERTAIN,
                "",
                "taken from the mailbox later; no passive acknowledgement is recorded",
            )
        return WakeOutcome(
            ACCEPTED, ACK_PROVES, "the named passive receiver acknowledged the message"
        )
    if view.receipt.get("recipient_wake_capabilities_source") != TRANSPORT_RESULTS:
        return WakeOutcome(UNCERTAIN, "", "delivered; concrete transport provenance is unavailable")
    if capability == WAKE_PASSIVE:
        return WakeOutcome(ACCEPTED, PROVES, "the hub delivered the message to the seat's waiter")
    if not isinstance(capability, str) or capability == WAKE_UNKNOWN:
        return WakeOutcome(
            UNCERTAIN, "", "delivered; the kind of the seat's receiver is not declared"
        )
    return WakeOutcome(REFUSED, "", "the message was delivered to a receiver of another kind")
