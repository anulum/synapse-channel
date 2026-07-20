# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — crash-recoverable legacy-to-AEF outbox drain
"""Reconcile atomically queued legacy rows into the independent AEF chain."""

from __future__ import annotations

from collections.abc import Callable

from synapse_channel.core.aef_emission import AefReceiptLog
from synapse_channel.core.aef_legacy_mapping import legacy_event_to_aef
from synapse_channel.core.errors import SynapseError
from synapse_channel.core.persistence import EventStore, StoredEvent


class AefOutboxError(SynapseError, RuntimeError):
    """A queued legacy row cannot be reconciled safely."""

    code = "aef_outbox"


AfterEmitHook = Callable[[StoredEvent, dict[str, object]], None]


def drain_aef_outbox(
    store: EventStore,
    log: AefReceiptLog,
    *,
    limit: int = 100,
    after_emit: AfterEmitHook | None = None,
) -> int:
    """Drain pending rows in legacy sequence order and return the settled count.

    If a process dies after native emission but before the outbox acknowledgement,
    the next drain finds the receipt by signed ``legacy_seq``, checks that it is
    the same deterministic projection, and marks the legacy row delivered without
    appending a duplicate AEF receipt.
    """
    settled = 0
    for event in store.pending_aef_events(limit=limit):
        request = legacy_event_to_aef(event)
        if request is None:
            raise AefOutboxError(f"legacy event kind {event.kind!r} has no AEF mapping")
        receipt = log.receipt_for_legacy_seq(event.seq)
        if receipt is None:
            receipt = request.emit(log)
            if after_emit is not None:
                after_emit(event, receipt)
        elif not request.matches(receipt):
            raise AefOutboxError(
                f"legacy sequence {event.seq} conflicts with its existing AEF receipt"
            )
        receipt_id = receipt.get("receipt_id")
        if not isinstance(receipt_id, str) or not receipt_id:
            raise AefOutboxError("emitted AEF receipt has no stable identity")
        store.mark_aef_delivered(event.seq, receipt_id)
        settled += 1
    return settled
