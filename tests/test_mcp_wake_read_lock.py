# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — shared reads of the notification ledger
"""Readers share the wake root lock; a write still excludes them."""

from __future__ import annotations

import sqlite3
import threading
import uuid
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import pytest

from synapse_channel.mcp.wake_ledger import WakeLedger
from synapse_channel.mcp.wake_limits import ROOT_CATALOGUE
from synapse_channel.mcp.wake_pointer import WakeLedgerError, WakeScope
from synapse_channel.mcp.wake_receipts import delivery_summary


def _ledger(tmp_path: Path) -> WakeLedger:
    """Create one private ledger by its first read."""
    scope = WakeScope("subject", "PROJECT", "PROJECT/receiver", "a" * 64, "hub-a")
    ledger = WakeLedger(tmp_path / "wake-state", scope)
    assert ledger.summary()["revision"] == 0
    return ledger


def test_two_readers_share_the_wake_root(tmp_path: Path) -> None:
    """Two reads overlap inside the root lock and both finish."""
    ledger = _ledger(tmp_path)
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def slow(connection: sqlite3.Connection, scope: WakeScope) -> dict[str, int]:
        barrier.wait(timeout=5)
        return delivery_summary(connection, scope)

    def read() -> None:
        try:
            ledger.summary()
        except BaseException as exc:  # the barrier reports a missed partner
            errors.append(exc)

    with patch("synapse_channel.mcp.wake_ledger.delivery_summary", slow):
        threads = [threading.Thread(target=read) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
            assert not thread.is_alive()
    assert errors == []


def test_a_reader_is_busy_while_a_write_holds_the_root(tmp_path: Path) -> None:
    """The exclusive write lock still refuses a read for the whole transaction."""
    ledger = _ledger(tmp_path)
    assert ledger.observe("messages", None, 1, relevant=True)
    held = threading.Event()
    release = threading.Event()
    minted = uuid.uuid4

    def wait() -> uuid.UUID:
        held.set()
        assert release.wait(timeout=5)
        return minted()

    def writer() -> None:
        with patch("synapse_channel.mcp.wake_ledger.uuid.uuid4", wait):
            ledger.acquire(100)

    thread = threading.Thread(target=writer)
    thread.start()
    assert held.wait(timeout=5)
    with pytest.raises(WakeLedgerError, match="wake state root is busy"):
        ledger.summary()
    release.set()
    thread.join(timeout=5)
    assert not thread.is_alive()


def test_an_unactivated_generation_is_read_on_the_exclusive_path(tmp_path: Path) -> None:
    """A creating generation is finished by the writer path, not a shared read."""
    ledger = _ledger(tmp_path)
    catalogue = ledger.path.parent / ROOT_CATALOGUE
    with closing(sqlite3.connect(catalogue)) as database, database:
        database.execute("UPDATE entries SET phase='creating'")
    assert ledger.summary()["revision"] == 0
    with closing(sqlite3.connect(catalogue)) as database:
        phase = database.execute("SELECT phase FROM entries").fetchone()
    assert phase is not None and phase[0] == "active"
