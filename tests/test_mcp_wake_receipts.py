# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — retained notification receipt contracts
"""Read real receipt files and refuse damaged receipts without resetting work."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

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
