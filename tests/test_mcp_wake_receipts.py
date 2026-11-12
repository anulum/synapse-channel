# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — retained notification receipt and schema migration contracts
"""Read real receipt files and migrate the retained schema without resetting work."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.mcp.wake_ledger import WakeLedger
from synapse_channel.mcp.wake_pointer import MAX_WAKE_INTEGER, WakeLedgerError
from test_mcp_wake_control import operator_ledger


def test_metadata_inspection_is_bounded_and_omits_delivery_content(tmp_path: Path) -> None:
    _, ledger, delivery = operator_ledger(tmp_path)
    view = ledger.inspect()
    assert view["summary"]["unresolved_failures"] == 1
    assert view["deliveries"] == [
        {
            "id": delivery.delivery_id,
            "revision": 1,
            "state": "dead-letter",
            "attempt": 1,
            "cycle_attempt": 1,
            "http_status": 401,
            "control_action": "",
            "control_at": 0,
        }
    ]
    assert "body" not in view["deliveries"][0]


@pytest.mark.parametrize(
    "column,value",
    [
        ("cycle_base", "bad"),
        ("cycle_base", -1),
        ("cycle_base", 2),
        ("control_action", "private sentinel"),
        ("control_at", -1),
        ("control_at", "private sentinel"),
        ("control_at", float(MAX_WAKE_INTEGER) + 4096),
        ("status", "private sentinel"),
        ("status", 600),
    ],
)
def test_damaged_receipt_metadata_cannot_escape_through_inspection(
    tmp_path: Path,
    column: str,
    value: Any,
) -> None:
    _, ledger, _ = operator_ledger(tmp_path)
    with closing(sqlite3.connect(ledger.path)) as db, db:
        db.execute(f"UPDATE deliveries SET {column}=?", (value,))
    with pytest.raises(WakeLedgerError) as error:
        ledger.inspect()
    assert "sentinel" not in str(error.value)


def test_only_an_explicitly_queued_redrive_can_have_zero_attempts_in_its_cycle(
    tmp_path: Path,
) -> None:
    _, ledger, _ = operator_ledger(tmp_path)
    with closing(sqlite3.connect(ledger.path)) as db, db:
        db.execute("UPDATE deliveries SET cycle_base=attempt")
    with pytest.raises(WakeLedgerError, match="invalid stored wake attempt"):
        ledger.receipts()


def test_schema_one_migration_preserves_pointer_scan_and_failed_receipt(tmp_path: Path) -> None:
    _, ledger, original = operator_ledger(tmp_path)
    with closing(sqlite3.connect(ledger.path)) as db, db:
        for column in ("cycle_base", "control_action", "control_at"):
            db.execute("ALTER TABLE deliveries DROP COLUMN " + column)
        db.execute("PRAGMA user_version=1")
    restored = WakeLedger(ledger.path.parent, ledger.scope)
    assert restored.scan_cursor() == 1
    assert restored.summary()["unresolved_failures"] == 1
    assert restored.control_delivery("redrive", original.delivery_id, 1, 102) == "queued"
    resumed = restored.acquire(102)
    assert resumed is not None and resumed.body == original.body and resumed.attempt == 2
    with closing(sqlite3.connect(ledger.path)) as db, db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 4


def test_partial_schema_migration_rolls_back_every_added_column(tmp_path: Path) -> None:
    _, ledger, original = operator_ledger(tmp_path)
    with closing(sqlite3.connect(ledger.path)) as db, db:
        db.execute("ALTER TABLE deliveries DROP COLUMN cycle_base")
        db.execute("ALTER TABLE deliveries DROP COLUMN control_at")
        db.execute("PRAGMA user_version=1")
        before = db.execute("SELECT * FROM deliveries").fetchall()
    with pytest.raises(WakeLedgerError, match="unavailable"):
        ledger.summary()
    with closing(sqlite3.connect(ledger.path)) as db, db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        assert "cycle_base" not in {row[1] for row in db.execute("PRAGMA table_info(deliveries)")}
        assert db.execute("SELECT * FROM deliveries").fetchall() == before
        assert db.execute("SELECT body FROM deliveries").fetchone()[0] == original.body
