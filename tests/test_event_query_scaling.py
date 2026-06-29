# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — event-query selective-read scaling regressions

from __future__ import annotations

from pathlib import Path

import pytest

from synapse_channel.core import event_query
from synapse_channel.core.event_query import (
    EventQuery,
    _selective_read_args,
    parse_query,
    run_query,
)
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.state import TaskClaim


def _claim(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "task_id": "T1",
        "owner": "alpha",
        "note": "n",
        "claimed_at": 10.0,
        "lease_expires_at": 999.0,
        "status": "claimed",
        "data_ref": "",
        "worktree": "repo",
        "paths": ("src/auth.py",),
        "epoch": 1,
        "checkpoint": "",
    }
    base.update(overrides)
    return TaskClaim(**base).as_dict()  # type: ignore[arg-type]


def _seed(path: Path) -> None:
    store = EventStore(path)
    # seq 1 ts 10: T1 claim
    store.append(
        EventKind.CLAIM, _claim(task_id="T1", paths=("src/auth.py",)), ts=10.0, durable=True
    )
    # seq 2 ts 20: T2 claim overlapping src
    store.append(
        EventKind.CLAIM,
        _claim(task_id="T2", owner="beta", paths=("src",), epoch=2),
        ts=20.0,
        durable=True,
    )
    # seq 3 ts 30: T1 update
    store.append(
        EventKind.TASK_UPDATE,
        _claim(task_id="T1", status="active", paths=("src/auth.py",)),
        ts=30.0,
        durable=True,
    )
    # seq 4 ts 40: channel chat
    store.append(
        EventKind.CHAT,
        {"sender": "alpha", "payload": "hi", "channel": "secret"},
        ts=40.0,
        durable=True,
    )
    # seq 5 ts 50: non-channel chat (noise)
    store.append(EventKind.CHAT, {"sender": "beta", "payload": "open"}, ts=50.0, durable=True)
    # seq 6 ts 60: T1 release (AFTER typical cutoffs)
    store.append(EventKind.RELEASE, {"task_id": "T1"}, ts=60.0, durable=True)
    # seq 7 ts 70: late T1 claim that must be excluded by 'at seq 3' windows
    store.append(EventKind.CLAIM, _claim(task_id="T1", owner="late"), ts=70.0, durable=True)
    store.close()


def _assert_equivalent(db: Path, query: str) -> None:
    """A selective read must match a full scan for the same query."""
    store = EventStore(db)
    try:
        all_events = tuple(store.read_all())
    finally:
        store.close()
    full = event_query.execute_query(all_events, parse_query(query))
    selective = run_query(db, query)
    assert event_query.result_to_json(selective) == event_query.result_to_json(full)


@pytest.mark.parametrize(
    "query",
    [
        "task T1 timeline",
        "task T1 at seq 3",
        "task T1 at time 35",
        "path src/auth.py between 0 35",
        "conflicts at seq 2",
        "conflicts at time 25",
        "channel secret between seq 1 4",
        "channel secret between time 0 45",
    ],
)
def test_selective_read_matches_full_scan(tmp_path: Path, query: str) -> None:
    db = tmp_path / "hub.db"
    _seed(db)
    _assert_equivalent(db, query)


def test_selective_read_actually_excludes_late_events(tmp_path: Path) -> None:
    # 'task T1 at seq 3' must not see the seq-6 release or seq-7 late claim;
    # the selective read loads only seq <= 3.
    db = tmp_path / "hub.db"
    _seed(db)
    state = run_query(db, "task T1 at seq 3").state
    assert state is not None
    assert state["status"] == "active"  # the seq-3 update, not the seq-7 'late' claim
    assert state["owner"] == "alpha"


# ---------- read_window ----------


def test_read_window_bounds_kinds_and_limit(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    _seed(db)
    store = EventStore(db)
    try:
        assert [e.seq for e in store.read_window(min_seq=3, max_seq=5)] == [3, 4, 5]
        assert [e.seq for e in store.read_window(since_ts=40.0, until_ts=60.0)] == [4, 5, 6]
        assert [e.kind for e in store.read_window(kinds=(EventKind.CHAT,))] == [
            EventKind.CHAT,
            EventKind.CHAT,
        ]
        assert store.read_window(kinds=()) == []
        assert [e.seq for e in store.read_window(limit=2)] == [1, 2]
        assert len(store.read_window()) == 7  # no bounds == read_all
    finally:
        store.close()


def test_selective_read_args_rejects_unknown_kind() -> None:
    with pytest.raises(ValueError, match="unsupported event query kind"):
        _selective_read_args(EventQuery(kind="bogus"))


# ---------- output limit ----------


def test_run_query_limit_caps_records_to_most_recent(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    _seed(db)
    full = run_query(db, "task T1 timeline")
    assert len(full.records) == 4  # claim, update, release, late claim
    limited = run_query(db, "task T1 timeline", limit=2)
    assert len(limited.records) == 2
    assert limited.records == full.records[-2:]


def test_run_query_limit_zero_empties_records(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    _seed(db)
    assert run_query(db, "task T1 timeline", limit=0).records == ()


def test_run_query_limit_caps_conflicts(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    _seed(db)
    full = run_query(db, "conflicts at seq 2")
    assert full.conflicts is not None and len(full.conflicts) >= 1
    capped = run_query(db, "conflicts at seq 2", limit=0)
    assert capped.conflicts == []


def test_run_query_missing_store_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="missing event store"):
        run_query(tmp_path / "nope.db", "task T1 timeline")
