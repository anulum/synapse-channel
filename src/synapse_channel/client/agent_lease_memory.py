# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the epochs and owner lease one seat remembers
"""Memory of the lease epochs a seat holds, and the owner-lease token."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from synapse_channel.client.lease_epoch_store import LeaseEpochStore
from synapse_channel.core.release_sidecar import is_release_sidecar, release_owner


def seat_store_name(connection_name: str) -> str | None:
    """Return the seat whose per-name stores ``connection_name`` may use.

    A seat maps to itself. A release sidecar maps to that seat. A nested
    sidecar maps to ``None``, and then no store is created.
    """
    if is_release_sidecar(connection_name):
        return release_owner(connection_name)
    return connection_name


class AgentLeaseMemory:
    """Own the remembered epochs, the on-disk store, and the owner-lease token.

    The store is keyed by the seat name passed in. This object never sees the
    connection name, so a sidecar cannot open a store under its own name.
    """

    def __init__(
        self,
        *,
        store_name: str | None,
        persist: bool,
        root: Path | None,
        request_lease: bool,
        owner_lease: str,
        on_lease_granted: Callable[[str], None] | None,
        takeover: bool,
    ) -> None:
        self.epochs: dict[str, int] = {}
        self._store = (
            LeaseEpochStore(store_name, root=root) if persist and store_name is not None else None
        )
        self.request_lease = bool(request_lease)
        self.owner_lease = str(owner_lease)
        self.on_lease_granted = on_lease_granted
        self.takeover = bool(takeover)

    def recall(self, hub_id: str, task_id: str, epoch: int | None = None) -> int | None:
        """Return the epoch to name, from the argument, memory, then the store."""
        if epoch is not None:
            return epoch
        task = task_id.strip()
        remembered = self.epochs.get(task)
        if remembered is None and self._store is not None:
            remembered = self._store.load(hub_id, task)
        return remembered

    def remember(self, hub_id: str, task_id: str, epoch: int) -> None:
        """Record ``epoch`` in memory and, when a store exists, on disk."""
        self.epochs[task_id] = epoch
        if self._store is not None:
            self._store.save(hub_id, task_id, epoch)

    def forget(self, hub_id: str, task_id: str) -> None:
        """Drop ``task_id`` from memory and from the store."""
        self.epochs.pop(task_id, None)
        if self._store is not None:
            self._store.forget(hub_id, task_id)

    def remember_verified(self, hub_id: str, task_id: str, epoch: int) -> bool:
        """Save ``epoch`` and keep it only when the store reads the same value back.

        With no store the epoch is recorded in memory and this returns ``True``.
        A mismatched read-back leaves memory unchanged and returns ``False``.
        """
        if self._store is not None:
            self._store.save(hub_id, task_id, epoch)
            if self._store.load(hub_id, task_id) != epoch:
                return False
        self.epochs[task_id] = epoch
        return True

    def record_owner_lease(self, token: str) -> None:
        """Keep the owner-lease token the hub just granted."""
        self.owner_lease = token
