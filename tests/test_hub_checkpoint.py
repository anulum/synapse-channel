# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — the checkpoint component, exercised without a listener
"""Open, anchor and release one checkpoint without starting a hub listener."""

from __future__ import annotations

import asyncio
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.core.hub_checkpoint import HubCheckpoint, open_checkpoint
from synapse_channel.core.journal import EventKind
from synapse_channel.core.merkle_checkpoint import (
    AntiRollbackError,
    MerkleCheckpointStore,
    checkpoint_path_for,
)
from synapse_channel.core.persistence import EventStore


def _remember(store: EventStore, seq: int) -> None:
    """Append one durable row the checkpoint can anchor."""
    store.append(EventKind.RECALL, {"actor": "alice", "seq": seq}, ts=float(seq))


def _cut_after(db: Path, seq: int) -> None:
    """Delete every event after ``seq`` through a separate connection."""
    connection = sqlite3.connect(str(db))
    connection.execute("DELETE FROM events WHERE seq > ?", (seq,))
    connection.commit()
    connection.close()


def test_constructor_is_keyword_only() -> None:
    """The path, the chain and the interval are keywords."""
    factory: Any = HubCheckpoint
    with pytest.raises(TypeError):
        factory(None, None, 1.0)


def test_unconfigured_reopen_refuses_and_a_closed_chain_stays_closed(tmp_path: Path) -> None:
    """No path means no open, and repeating a release does not anchor."""
    checkpoint = HubCheckpoint(path=None, live=None, interval=1.0)
    with EventStore(tmp_path / "events.db") as journal:
        checkpoint.reopen_if_closed(journal)
        checkpoint.reopen_if_closed(object())
        assert checkpoint.current() is None
        assert checkpoint.store is None
        with pytest.raises(ValueError, match="configured checkpoint path"):
            checkpoint.reopen(journal)
    checkpoint.close()
    checkpoint.release_without_anchor()
    assert checkpoint.current() is None


def test_reopen_skips_a_foreign_journal_and_an_open_chain(tmp_path: Path) -> None:
    """A second open keeps the same chain, and a non-store does not open one."""
    db = tmp_path / "events.db"
    with EventStore(db) as journal:
        _remember(journal, 1)
        path = checkpoint_path_for(db)
        live = open_checkpoint(journal, path)
        checkpoint = HubCheckpoint(path=path, live=live, interval=30.0)
        checkpoint.reopen_if_closed(object())
        assert checkpoint.current() is live
        assert checkpoint.store is live.store
        closed = HubCheckpoint(path=path, live=None, interval=30.0)
        closed.reopen_if_closed(object())
        assert closed.current() is None
        closed.reopen_if_closed(journal)
        reopened = closed.current()
        assert reopened is not None and reopened is not live
        closed.reopen_if_closed(journal)
        assert closed.current() is reopened
        checkpoint.release_without_anchor()
        closed.release_without_anchor()


def test_close_anchors_once_and_a_failed_construction_does_not(tmp_path: Path) -> None:
    """Shutdown anchors the last write. A failed construction only closes the chain."""
    db = tmp_path / "events.db"
    with EventStore(db) as journal:
        _remember(journal, 1)
        path = checkpoint_path_for(db)
        anchored = HubCheckpoint(path=path, live=open_checkpoint(journal, path), interval=30.0)
        _remember(journal, 2)
        anchored.close()
        anchored.close()
        assert anchored.current() is None
        assert anchored.store is None
        with closing(MerkleCheckpointStore(path)) as chain:
            latest = chain.latest()
            assert latest is not None and latest.seq == 2
        released = HubCheckpoint(path=path, live=open_checkpoint(journal, path), interval=30.0)
        _remember(journal, 3)
        released.release_without_anchor()
        released.release_without_anchor()
        assert released.current() is None
        with closing(MerkleCheckpointStore(path)) as chain:
            latest = chain.latest()
            assert latest is not None and latest.seq == 2
        refused = HubCheckpoint(path=path, live=None, interval=30.0)
        _cut_after(db, 1)
        with pytest.raises(AntiRollbackError):
            refused.open(journal)
        assert refused.current() is None
        with closing(MerkleCheckpointStore(path)) as chain:
            chain.latest()


async def test_anchor_loop_folds_a_new_row_and_stops_when_cancelled(tmp_path: Path) -> None:
    """The interval loop anchors, and cancellation leaves the chain open."""
    db = tmp_path / "events.db"
    with EventStore(db) as journal:
        _remember(journal, 1)
        path = checkpoint_path_for(db)
        live = open_checkpoint(journal, path)
        checkpoint = HubCheckpoint(path=path, live=live, interval=0.01)
        _remember(journal, 2)
        task = asyncio.create_task(checkpoint.anchor_loop(live))
        deadline = asyncio.get_running_loop().time() + 5.0

        def anchored() -> bool:
            """Report whether the loop has folded the second row."""
            latest = live.store.latest()
            return latest is not None and latest.seq == 2

        while not anchored():
            assert asyncio.get_running_loop().time() < deadline
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert checkpoint.current() is live
        checkpoint.close()
        assert checkpoint.store is None
