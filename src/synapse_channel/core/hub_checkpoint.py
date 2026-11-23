# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — the live checkpoint one hub anchors
"""The checkpoint chain one hub verifies, anchors and releases."""

from __future__ import annotations

import asyncio
from pathlib import Path

from synapse_channel.core.merkle_checkpoint import LiveCheckpoint, MerkleCheckpointStore
from synapse_channel.core.persistence import EventStore


def open_checkpoint(journal: EventStore, path: Path) -> LiveCheckpoint:
    """Verify and anchor the chain, releasing the database on any refused start."""
    store = MerkleCheckpointStore(path)
    try:
        store.verify(journal)
        return LiveCheckpoint(store, journal)
    except BaseException:
        store.close()
        raise


class HubCheckpoint:
    """Own the configured checkpoint path and the chain opened from it.

    The constructor keywords are the path, the chain already opened for this
    hub, and the anchor interval. Serving reopens a closed chain. Closing
    anchors once. A construction that fails releases the chain without
    anchoring.
    """

    def __init__(
        self,
        *,
        path: Path | None,
        live: LiveCheckpoint | None,
        interval: float,
    ) -> None:
        self._path = path
        self._live = live
        self._interval = interval

    def reopen(self, journal: EventStore) -> LiveCheckpoint:
        """Reopen and verify the checkpoint at this graph's configured path.

        Parameters
        ----------
        journal:
            Current caller-owned journal to verify against the retained chain.

        Returns
        -------
        LiveCheckpoint
            The verified checkpoint acquired by the same factory as the
            initial checkpoint. The serving hub owns its eventual close.

        Raises
        ------
        ValueError
            If this graph was configured without a checkpoint path.
        Exception
            The original acquisition or verification error. The factory
            releases its partial acquisition before propagating a refusal.
        """
        path = self._path
        if path is None:
            raise ValueError("checkpoint reopening requires a configured checkpoint path")
        return open_checkpoint(journal, path)

    def open(self, journal: EventStore) -> None:
        """Open the checkpoint chain, verify the log against it, then anchor it.

        Fail closed BEFORE serving: a truncated or rewritten log is a hard error
        at startup, never a quiet restart. Only then is the current state anchored
        as the newest chain link. The bound factory retains the configured
        checkpoint location and releases its partial connection on refusal.
        """
        self._live = self.reopen(journal)

    def reopen_if_closed(self, journal: object) -> None:
        """Open only when a path is configured, nothing is open, and the journal is a store."""
        if self._path is not None and self._live is None and isinstance(journal, EventStore):
            # A hub served again after a clean shutdown re-verifies before serving.
            self.open(journal)

    def current(self) -> LiveCheckpoint | None:
        """Return the open checkpoint, or ``None`` when anchoring is off or closed."""
        return self._live

    @property
    def store(self) -> MerkleCheckpointStore | None:
        """The open checkpoint chain, or ``None`` when anchoring is off or closed."""
        return None if self._live is None else self._live.store

    async def anchor_loop(self, live: LiveCheckpoint) -> None:
        """Anchor the live log every checkpoint interval seconds while serving.

        The loop is cancelled before :meth:`close` releases ``live``.
        """
        while True:
            await asyncio.sleep(self._interval)
            live.anchor()

    def close(self) -> None:
        """Anchor the final state and release the checkpoint store (clean shutdown)."""
        live = self._live
        if live is None:
            return
        self._live = None
        try:
            live.anchor()
        finally:
            live.close()

    def release_without_anchor(self) -> None:
        """Close the chain without anchoring, for a construction that failed.

        A repeated call does nothing. The chain is no longer reachable.
        """
        live = self._live
        if live is None:
            return
        self._live = None
        live.close()
