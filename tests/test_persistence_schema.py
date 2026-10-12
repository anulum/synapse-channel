# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — real event-store schema migration and outbox retention
"""Exercise schema families through opened stores and actual retained SQLite files."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from synapse_channel.core.persistence import EventStore


def test_enrolled_outbox_retains_pending_event_across_reopen_and_compaction(
    tmp_path: Path,
) -> None:
    path = tmp_path / "outbox.db"
    with EventStore(path, aef_outbox_kinds=("chat",)) as store:
        seq = store.append("chat", {"sender": "WORK/seat", "payload": "retain"})
        assert store.delete((seq,)) == 0
    with EventStore(path, aef_outbox_kinds=("chat",)) as store:
        pending = store.pending_aef_events()
        assert len(pending) == 1 and pending[0].seq == seq
        assert pending[0].payload["payload"] == "retain"
        store.mark_aef_delivered(seq, "receipt-one")
        assert store.aef_delivery(seq) == "receipt-one"
        assert store.pending_aef_events() == ()
        assert store.delete((seq,)) == 1
        next_seq = store.append("chat", {"sender": "WORK/seat", "payload": "next"})
        assert next_seq > seq
    with EventStore(path, aef_outbox_kinds=("chat",)) as store:
        assert [event.seq for event in store.pending_aef_events()] == [next_seq]


def test_legacy_receipt_migration_preserves_original_rows_and_next_sequence(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    requested = {"message_seq": 1, "message_id": 7, "sender": "WORK/a", "target": "WORK/b"}
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "CREATE TABLE events (seq INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, "
            "kind TEXT NOT NULL, payload TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO events VALUES (?, ?, ?, ?)",
            (1, 1000.0, "chat", json.dumps({"sender": "WORK/a", "payload": "kept"})),
        )
        connection.execute(
            "INSERT INTO events VALUES (?, ?, ?, ?)",
            (2, 1000.0, "delivery_receipt_requested", json.dumps(requested)),
        )
        connection.commit()
    finally:
        connection.close()
    with EventStore(path) as store:
        assert [event.seq for event in store.read_all()] == [1, 2]
        aggregate = store.delivery_receipt_aggregate(1)
        assert aggregate is not None
        assert aggregate.state == "requested" and aggregate.updated_event_seq == 2
        assert store.pending_delivery_notifications() == ()
        assert store.pending_operation_intents() == ()
        assert store.read_all()[0].payload["payload"] == "kept"
        assert store.append("chat", {"payload": "after migration"}) == 3
    with EventStore(path) as store:
        assert [event.seq for event in store.read_all()] == [1, 2, 3]
