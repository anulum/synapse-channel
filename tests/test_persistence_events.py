# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — public bounded reads over real retained event rows
"""Exercise selective event reads and forensic boundaries through EventStore."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from synapse_channel.core.event_row_recovery import CORRUPT_EVENT_KIND
from synapse_channel.core.persistence import EventStore


def test_window_filters_limits_and_corruption_keep_real_sequence_boundaries(tmp_path: Path) -> None:
    """Inclusive filters and empty/zero caps preserve the stored sequence domain."""
    path = tmp_path / "window.db"
    with EventStore(path) as store:
        first, second = store.append_batch([("chat", {"id": 1}), ("finding", {"id": 2})], ts=3)
        third = store.append("chat", {"id": 3}, ts=9)
        assert [
            event.seq
            for event in store.read_window(
                min_seq=first, max_seq=third, since_ts=3, until_ts=9, kinds=["chat"], limit=10
            )
        ] == [first, third]
        assert store.read_window(kinds=[]) == []
        assert store.read_window(limit=0) == []
        assert store.read_window(limit=-1) == []
        assert store.corrupt_rows() == ()
        with sqlite3.connect(path) as operator:
            operator.execute("UPDATE events SET payload=? WHERE seq=?", ("[]", second))
        markers = store.corrupt_rows()
        assert len(markers) == 1 and markers[0].seq == second
        assert store.corrupt_rows(through_seq=first) == ()
        assert [event.kind for event in store.read_window(min_seq=second, max_seq=second)] == [
            CORRUPT_EVENT_KIND
        ]
        assert [
            event.seq
            for event in store.iter_events(after_seq=first, through_seq=third, kinds=["chat"])
        ] == [third]
