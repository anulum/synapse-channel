# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — entitlement ledger refusals on real POSIX directories and pages
"""Refuse unusable homes, a damaged ledger and an event the ledger cannot hold."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from synapse_channel.core.entitlement_store import (
    EntitlementStoreError,
    append_event,
    read_events,
)


def _account(index: int = 1) -> dict[str, object]:
    return {
        "event_id": f"account-record-{index}",
        "kind": "account",
        "recorded_at": "2026-09-19T10:00:00Z",
        "source": "operator:owner",
        "confidence": "operator",
        "account_id": f"opaque-{index}",
        "label": "Private account",
        "status": "active",
    }


def test_home_and_directory_that_cannot_be_created_are_refused(tmp_path: Path) -> None:
    """A home under a file, a shared home or a read-only home cannot hold the ledger."""
    blocker = tmp_path / "blocker"
    blocker.write_text("a regular file, not a directory", encoding="utf-8")
    with pytest.raises(EntitlementStoreError, match="^cannot create private entitlement home$"):
        append_event(blocker / "home" / "ledger" / "ledger.sqlite3", _account())

    shared = tmp_path / "shared-home"
    shared.mkdir(mode=0o700)
    shared.chmod(0o755)
    with pytest.raises(
        EntitlementStoreError, match=r"^entitlement home: .* \(mode 755\); must be owner-only"
    ):
        append_event(shared / "ledger" / "ledger.sqlite3", _account())
    assert list(shared.iterdir()) == []

    home = tmp_path / "read-only-home"
    home.mkdir(mode=0o700)
    home.chmod(0o500)
    try:
        with pytest.raises(
            EntitlementStoreError, match="^cannot create private entitlement directory$"
        ):
            append_event(home / "ledger" / "ledger.sqlite3", _account())
        assert list(home.iterdir()) == []
    finally:
        home.chmod(0o700)


def test_ledger_file_that_sqlite_cannot_create_is_refused(tmp_path: Path) -> None:
    """An owner-only but read-only ledger directory refuses before any connection exists."""
    directory = tmp_path / "home" / "ledger"
    directory.parent.mkdir(mode=0o700)
    directory.mkdir(mode=0o700)
    directory.chmod(0o500)
    try:
        with pytest.raises(EntitlementStoreError, match="^cannot open entitlement store$"):
            append_event(directory / "ledger.sqlite3", _account())
        assert list(directory.iterdir()) == []
    finally:
        directory.chmod(0o700)


def test_damaged_event_index_fails_the_integrity_check(tmp_path: Path) -> None:
    """A ledger whose primary-key index is damaged is refused, not read."""
    path = tmp_path / "home" / "ledger" / "ledger.sqlite3"
    for index in range(3):
        assert append_event(path, _account(index))
    with closing(sqlite3.connect(path)) as connection, connection:
        page_size = connection.execute("PRAGMA page_size").fetchone()[0]
        (root,) = connection.execute(
            "SELECT rootpage FROM sqlite_master WHERE name = 'sqlite_autoindex_events_1'"
        ).fetchone()
    connection.close()
    damaged = bytearray(path.read_bytes())
    # Offset 3 of a b-tree page header is its cell count; an impossible count is
    # caught by PRAGMA quick_check while the file header still opens normally.
    offset = (root - 1) * page_size + 3
    damaged[offset : offset + 2] = b"\xff\xff"
    path.write_bytes(bytes(damaged))
    with pytest.raises(EntitlementStoreError, match="^entitlement store integrity check failed$"):
        read_events(path)


def test_event_that_conflicts_with_the_ledger_is_not_appended(tmp_path: Path) -> None:
    """A pool for an account the ledger never recorded is refused and leaves no row."""
    path = tmp_path / "home" / "ledger" / "ledger.sqlite3"
    assert append_event(path, _account())
    orphan_pool = {
        "event_id": "pool-record-1",
        "kind": "pool",
        "recorded_at": "2026-09-19T10:05:00Z",
        "source": "operator:owner",
        "confidence": "operator",
        "pool_id": "pool-1",
        "account_id": "never-recorded",
        "unit": "tasks",
    }
    with pytest.raises(
        EntitlementStoreError,
        match="^event conflicts with ledger: pool refers to an unknown account$",
    ):
        append_event(path, orphan_pool)
    assert read_events(path) == (_account(),)
