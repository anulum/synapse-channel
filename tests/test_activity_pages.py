# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — durable metadata activity paging tests
"""Exercise real SQLite bounds and strict activity query contracts."""

from __future__ import annotations

import sqlite3
import tracemalloc
from contextlib import closing
from pathlib import Path

import pytest

from synapse_channel.core.activity_pages import ActivityQuery, read_activity_page
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore


@pytest.mark.parametrize(
    "changes",
    [
        {"version": True},
        {"version": 3},
        {"unknown": True},
        {"identity": None},
        {"identity": ""},
        {"identity": " ALPHA/bot"},
        {"identity": "x" * 257},
        {"hub_id": None},
        {"hub_id": "x" * 257},
        {"since_seq": True},
        {"since_seq": -1},
        {"since_seq": 2**63},
        {"since_seq": 1, "hub_id": ""},
        {"limit": True},
        {"limit": 0},
        {"limit": 101},
    ],
)
def test_invalid_query_shape_and_bounds_refuse(changes: dict[str, object]) -> None:
    query: dict[str, object] = {
        "version": 1,
        "identity": "ALPHA/bot",
        "hub_id": "",
        "since_seq": 0,
        "limit": 50,
    }
    with pytest.raises(ValueError, match="invalid activity query"):
        ActivityQuery.from_value(query | changes)


def test_nonobject_query_refuses() -> None:
    with pytest.raises(ValueError, match="invalid activity query"):
        ActivityQuery.from_value([])


def test_scan_bound_empty_pages_and_repeated_reads_are_stable(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        for _ in range(1001):
            store.append(EventKind.CHAT, {"sender": "ALPHA/operator", "target": "all"})
        directed = store.append(
            EventKind.CHAT,
            {"sender": "ALPHA/operator", "target": "ALPHA/bot", "payload": "not returned"},
        )
        query = ActivityQuery("ALPHA/bot", "", 0, 1)
        first = read_activity_page(store, query, hub_id="hub-1")
        assert first["items"] == []
        assert first["cursor"] == 1000 and first["has_more"] is True
        assert read_activity_page(store, query, hub_id="hub-1") == first
        second = read_activity_page(
            store, ActivityQuery("ALPHA/bot", "hub-1", 1000, 1), hub_id="hub-1"
        )
        assert second["items"] == [{"seq": directed, "kind": "messages"}]
        assert second["cursor"] == directed and second["has_more"] is False
        empty = read_activity_page(
            store, ActivityQuery("ALPHA/bot", "hub-1", directed, 10), hub_id="hub-1"
        )
        assert empty["items"] == [] and empty["cursor"] == directed
        assert "not returned" not in str(second)
        with pytest.raises(ValueError, match="activity cursor is not available"):
            read_activity_page(
                store, ActivityQuery("ALPHA/bot", "foreign", directed, 10), hub_id="hub-1"
            )
        with pytest.raises(ValueError, match="activity cursor is not available"):
            read_activity_page(
                store, ActivityQuery("ALPHA/bot", "hub-1", directed + 1, 10), hub_id="hub-1"
            )
    finally:
        store.close()


def test_limit_break_keeps_unscanned_relevant_rows(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        first = store.append(EventKind.CHAT, {"sender": "ALPHA/operator", "target": "ALPHA/bot"})
        second = store.append(EventKind.CHAT, {"sender": "ALPHA/operator", "target": "ALPHA/bot"})
        page = read_activity_page(store, ActivityQuery("ALPHA/bot", "", 0, 1), hub_id="hub-1")
        assert page["cursor"] == first and page["has_more"] is True
        rest = read_activity_page(
            store, ActivityQuery("ALPHA/bot", "hub-1", first, 100), hub_id="hub-1"
        )
        assert rest["items"] == [{"seq": second, "kind": "messages"}]
    finally:
        store.close()


def test_large_foreign_payloads_are_streamed_under_page_budget(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        for _ in range(160):
            store.append(
                EventKind.CHAT,
                {
                    "sender": "ALPHA/operator",
                    "target": "all",
                    "payload": "x" * 65536,
                },
            )
        directed = store.append(
            EventKind.CHAT,
            {
                "sender": "ALPHA/operator",
                "target": "ALPHA/bot",
                "payload": "direct",
            },
        )
        tracemalloc.start()
        try:
            page = read_activity_page(store, ActivityQuery("ALPHA/bot", "", 0, 100), hub_id="hub-1")
            _current, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        assert peak < 4 * 1024 * 1024
        cursor = page["cursor"]
        assert isinstance(cursor, int)
        assert 0 < cursor < 32 and page["has_more"] is True
        assert page["items"] == []
        while page["has_more"]:
            page = read_activity_page(
                store, ActivityQuery("ALPHA/bot", "hub-1", cursor, 100), hub_id="hub-1"
            )
            next_cursor = page["cursor"]
            assert isinstance(next_cursor, int) and next_cursor > cursor
            cursor = next_cursor
        assert page["items"] == [{"seq": directed, "kind": "messages"}]
    finally:
        store.close()


def test_corrupt_candidate_row_cannot_be_silently_consumed(tmp_path: Path) -> None:
    path = tmp_path / "hub.db"
    store = EventStore(path)
    seq = store.append(EventKind.CHAT, {"sender": "ALPHA/operator", "target": "ALPHA/bot"})
    store.close()
    with closing(sqlite3.connect(path)) as database, database:
        database.execute("UPDATE events SET payload=? WHERE seq=?", ("not-json", seq))
    store = EventStore(path)
    try:
        with pytest.raises(ValueError, match="activity journal row is not available"):
            read_activity_page(store, ActivityQuery("ALPHA/bot", "", 0, 100), hub_id="hub-1")
    finally:
        store.close()
