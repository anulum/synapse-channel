# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — transient ownership of forwarding exchanges and receipts
"""Coalesce overlapping work for one ledger and forward without blocking other forwards."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Literal
from weakref import WeakKeyDictionary

if TYPE_CHECKING:
    from synapse_channel.core.message_forward_ledger import MessageForwardLedger

_active: WeakKeyDictionary[MessageForwardLedger, set[tuple[str, str]]] = WeakKeyDictionary()


@contextmanager
def own_forward_work(
    ledger: MessageForwardLedger, forward_id: str, kind: Literal["attempt", "notification"]
) -> Iterator[bool]:
    """Acquire one event-loop-local forward operation or coalesce it with its current owner.

    Parameters
    ----------
    ledger : MessageForwardLedger
        The outbox owning the forward. Different ledgers have independent ownership.
    forward_id : str
        The durable forward identifier.
    kind : {"attempt", "notification"}
        Exchanges and receipt projection use independent ownership slots.

    Yields
    ------
    bool
        Whether this invocation owns the operation. A coalesced caller must not perform it.

    Notes
    -----
    Acquisition contains no await; callers retain ownership across their network await.
    Cancellation and failure release ownership in finally. Empty sets are removed and weak
    ledger references prevent retaining retired hubs. Durable progress remains in the ledger.
    """
    active = _active.setdefault(ledger, set())
    key = (forward_id, kind)
    if key in active:
        yield False
        return
    active.add(key)
    try:
        yield True
    finally:
        active.remove(key)
        if not active:
            del _active[ledger]
