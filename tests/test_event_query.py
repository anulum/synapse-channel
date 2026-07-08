# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — temporal event-log query regressions

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from synapse_channel.core import event_query
from synapse_channel.core.delivery_receipts import immediate_receipt_payload
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore, StoredEvent
from synapse_channel.core.state import TaskClaim


def _claim(**overrides: object) -> TaskClaim:
    base: dict[str, object] = {
        "task_id": "T1",
        "owner": "alpha",
        "note": "initial",
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
    return TaskClaim(**base)  # type: ignore[arg-type]


def _seed_store(path: Path) -> None:
    store = EventStore(path)
    store.append(
        EventKind.CLAIM,
        _claim(task_id="T1", owner="alpha", paths=("src/auth.py",), epoch=1).as_dict(),
        ts=10.0,
        durable=True,
    )
    store.append(
        EventKind.CLAIM,
        _claim(
            task_id="T2",
            owner="beta",
            note="overlap",
            paths=("src",),
            epoch=2,
            claimed_at=20.0,
        ).as_dict(),
        ts=20.0,
        durable=True,
    )
    store.append(
        EventKind.TASK_UPDATE,
        _claim(
            task_id="T1",
            owner="alpha",
            status="in_progress",
            data_ref="mem://1",
            paths=("src/auth.py",),
            epoch=3,
            claimed_at=30.0,
        ).as_dict(),
        ts=30.0,
        durable=True,
    )
    store.append(EventKind.RELEASE, {"task_id": "T2"}, ts=40.0, durable=True)
    store.append(
        EventKind.TASK_UPDATE,
        _claim(
            task_id="T1",
            owner="alpha",
            status="completed",
            data_ref="mem://done",
            paths=("src/auth.py",),
            epoch=4,
            claimed_at=50.0,
        ).as_dict(),
        ts=50.0,
        durable=True,
    )
    store.close()


def test_task_timeline_query_returns_task_events_in_order(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    _seed_store(db)

    result = event_query.run_query(db, "task T1 timeline")

    assert result.kind == "task_timeline"
    assert [record.event.kind for record in result.records] == [
        "claim",
        "task_update",
        "task_update",
    ]
    assert [record.task_id for record in result.records] == ["T1", "T1", "T1"]


def test_task_state_at_sequence_reconstructs_owner_status_and_paths(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    _seed_store(db)

    result = event_query.run_query(db, "task T1 at seq 3")

    assert result.kind == "task_state"
    assert result.state is not None
    assert result.state == {
        "task_id": "T1",
        "owner": "alpha",
        "status": "in_progress",
        "data_ref": "mem://1",
        "paths": ["src/auth.py"],
        "worktree": "repo",
        "event_seq": 3,
        "event_ts": pytest.approx(30.0),
    }


def test_path_between_query_finds_overlapping_claim_scopes(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    _seed_store(db)

    result = event_query.run_query(db, "path src/auth.py between 0 35")

    assert result.kind == "path_touched"
    assert [(record.task_id, record.owner) for record in result.records] == [
        ("T1", "alpha"),
        ("T2", "beta"),
        ("T1", "alpha"),
    ]


def test_conflicts_at_sequence_uses_temporal_live_claims(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    _seed_store(db)

    before_release = event_query.run_query(db, "conflicts at seq 3")
    after_release = event_query.run_query(db, "conflicts at seq 5")

    assert before_release.kind == "conflicts"
    assert before_release.conflicts == [
        {
            "left_task": "T1",
            "left_owner": "alpha",
            "right_task": "T2",
            "right_owner": "beta",
            "worktree": "repo",
            "paths": ["src/auth.py", "src"],
        }
    ]
    assert after_release.conflicts == []


def test_cypher_like_aliases_parse_to_existing_query_shapes() -> None:
    assert event_query.parse_query(
        'MATCH (task:TASK {id:"T1"}) RETURN timeline'
    ) == event_query.EventQuery(
        kind="task_timeline",
        task_id="T1",
        raw='MATCH (task:TASK {id:"T1"}) RETURN timeline',
    )
    assert event_query.parse_query(
        'MATCH (task:TASK {id:"T1"}) AT seq 3 RETURN state'
    ) == event_query.EventQuery(
        kind="task_state",
        task_id="T1",
        cutoff_kind="seq",
        cutoff=3.0,
        raw='MATCH (task:TASK {id:"T1"}) AT seq 3 RETURN state',
    )
    assert event_query.parse_query(
        'MATCH (path:PATH {value:"src/auth.py"}) BETWEEN 0 35 RETURN events'
    ) == event_query.EventQuery(
        kind="path_touched",
        path="src/auth.py",
        lower=0.0,
        upper=35.0,
        raw='MATCH (path:PATH {value:"src/auth.py"}) BETWEEN 0 35 RETURN events',
    )
    assert event_query.parse_query(
        "MATCH (conflicts) AT seq 3 RETURN pairs"
    ) == event_query.EventQuery(
        kind="conflicts",
        cutoff_kind="seq",
        cutoff=3.0,
        raw="MATCH (conflicts) AT seq 3 RETURN pairs",
    )


def test_datalog_like_aliases_parse_to_existing_query_shapes() -> None:
    assert event_query.parse_query('timeline("T1").') == event_query.EventQuery(
        kind="task_timeline",
        task_id="T1",
        raw='timeline("T1").',
    )
    assert event_query.parse_query('state("T1", seq, 3).') == event_query.EventQuery(
        kind="task_state",
        task_id="T1",
        cutoff_kind="seq",
        cutoff=3.0,
        raw='state("T1", seq, 3).',
    )
    assert event_query.parse_query('touches("src/auth.py", 0, 35).') == event_query.EventQuery(
        kind="path_touched",
        path="src/auth.py",
        lower=0.0,
        upper=35.0,
        raw='touches("src/auth.py", 0, 35).',
    )
    assert event_query.parse_query("conflicts(seq, 3).") == event_query.EventQuery(
        kind="conflicts",
        cutoff_kind="seq",
        cutoff=3.0,
        raw="conflicts(seq, 3).",
    )
    assert event_query.parse_query("timeline(T1)") == event_query.EventQuery(
        kind="task_timeline",
        task_id="T1",
        raw="timeline(T1)",
    )


def test_alias_queries_execute_with_existing_temporal_semantics(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    _seed_store(db)

    timeline = event_query.run_query(db, 'MATCH (task:TASK {id:"T1"}) RETURN timeline')
    state = event_query.run_query(db, 'state("T1", seq, 3).')
    touched = event_query.run_query(db, 'touches("src/auth.py", 0, 35).')
    conflicts = event_query.run_query(db, "MATCH (conflicts) AT seq 3 RETURN pairs")

    assert [record.event.kind for record in timeline.records] == [
        "claim",
        "task_update",
        "task_update",
    ]
    assert state.state is not None
    assert state.state["status"] == "in_progress"
    assert [record.task_id for record in touched.records] == ["T1", "T2", "T1"]
    assert conflicts.conflicts is not None
    assert conflicts.conflicts[0]["left_task"] == "T1"


def test_task_timeline_alias_human_renderer_uses_task_label(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    _seed_store(db)

    result = event_query.run_query(db, 'MATCH (task:TASK {id:"T1"}) RETURN timeline')

    assert "task T1 timeline: 3 event(s)" in event_query.render_human(result)


def test_task_state_at_timestamp_uses_last_event_at_or_before_time(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    _seed_store(db)
    store = EventStore(db)
    events = store.read_all()
    store.close()
    cutoff = events[2].ts

    result = event_query.run_query(db, f"task T1 at time {cutoff}")

    assert result.state is not None
    assert result.state["status"] == "in_progress"


def test_json_and_human_renderers_are_stable(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    _seed_store(db)
    result = event_query.run_query(db, "task T1 timeline")

    assert event_query.result_to_json(result)["kind"] == "task_timeline"
    human = event_query.render_human(result)
    assert "task T1 timeline: 3 event(s)" in human
    assert "task_update" in human


def test_json_renderer_includes_state_and_conflicts(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    _seed_store(db)

    state = event_query.run_query(db, "task T1 at seq 3")
    conflicts = event_query.run_query(db, "conflicts at seq 3")

    assert event_query.result_to_json(state)["state"] == state.state
    assert event_query.result_to_json(conflicts)["conflicts"] == conflicts.conflicts


def test_channel_between_sequence_query_returns_channel_metadata_only() -> None:
    events = (
        StoredEvent(
            seq=1,
            ts=1.0,
            kind=EventKind.CHAT,
            payload={
                "sender": "alice",
                "target": "all",
                "payload": "public",
                "msg_id": 1,
            },
        ),
        StoredEvent(
            seq=2,
            ts=2.0,
            kind=EventKind.CHAT,
            payload={
                "sender": "alice",
                "target": "all",
                "payload": "private body",
                "channel": "ops",
                "msg_id": 2,
            },
        ),
    )

    result = event_query.execute_query(
        events,
        event_query.parse_query("channel ops between seq 1 3"),
    )

    assert result.kind == "channel_events"
    assert (
        event_query.render_human(result)
        == "channel ops: 1 event(s)\n- seq=2 chat alice channel=ops"
    )
    payload = event_query.result_to_json(result)
    assert payload["records"] == [
        {
            "seq": 2,
            "ts": 2.0,
            "kind": "chat",
            "channel": "ops",
            "sender": "alice",
            "target": "all",
            "msg_id": 2,
            "payload_bytes": 12,
        }
    ]
    assert "private body" not in str(payload)


def test_channel_query_aliases_parse() -> None:
    assert event_query.parse_query('channel("ops", seq, 1, 3).') == event_query.EventQuery(
        kind="channel_events",
        channel_id="ops",
        cutoff_kind="seq",
        lower=1.0,
        upper=3.0,
        raw='channel("ops", seq, 1, 3).',
    )


def test_delivery_receipt_query_filters_by_participant(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    store = EventStore(db)
    store.append(
        EventKind.DELIVERY_RECEIPT_IMMEDIATE,
        immediate_receipt_payload(
            sender="ALICE",
            target="BOB",
            message_id=1,
            message_seq=10,
            delivered=False,
            recipients=(),
        ),
        ts=10.0,
        durable=True,
    )
    store.append(
        EventKind.DELIVERY_RECEIPT_IMMEDIATE,
        immediate_receipt_payload(
            sender="CAROL",
            target="DAVE",
            message_id=2,
            message_seq=11,
            delivered=True,
            recipients=("DAVE",),
        ),
        ts=11.0,
        durable=True,
    )
    store.close()

    result = event_query.run_query(db, "receipts ALICE")

    assert result.kind == "delivery_receipts"
    assert [event.payload["sender"] for event in result.receipt_events] == ["ALICE"]
    payload = event_query.result_to_json(result)
    receipts = cast(list[dict[str, Any]], payload["receipts"])
    assert receipts[0]["phase"] == "immediate"
    assert "delivery receipts ALICE: 1 event(s)" in event_query.render_human(result)


def test_delivery_receipt_query_alias_parses() -> None:
    assert event_query.parse_query('receipts("ALICE").') == event_query.EventQuery(
        kind="delivery_receipts",
        participant="ALICE",
        raw='receipts("ALICE").',
    )


def test_channel_human_renderer_falls_back_for_legacy_labels() -> None:
    assert (
        event_query.render_human(event_query.QueryResult(kind="channel_events", query="legacy ops"))
        == "channel ops: 0 event(s)"
    )
    assert (
        event_query.render_human(event_query.QueryResult(kind="channel_events", query="legacy"))
        == "channel ?: 0 event(s)"
    )


def test_human_renderers_cover_state_path_conflicts_and_fallback(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    _seed_store(db)

    state = event_query.run_query(db, "task T1 at seq 3")
    missing = event_query.run_query(db, "task MISSING at seq 3")
    touched = event_query.run_query(db, "path src/auth.py between 0 35")
    conflicts = event_query.run_query(db, "conflicts at seq 3")
    fallback = event_query.QueryResult(kind="unknown", query="unknown")

    assert "status=in_progress" in event_query.render_human(state)
    assert event_query.render_human(missing) == "task state: not found"
    assert "path touched: 3 event(s)" in event_query.render_human(touched)
    assert "conflicts: 1 pair(s)" in event_query.render_human(conflicts)
    assert (
        event_query.render_human(
            event_query.QueryResult(kind="task_timeline", query="legacy LABEL")
        )
        == "task LABEL timeline: 0 event(s)"
    )
    assert (
        event_query.render_human(event_query.QueryResult(kind="task_timeline", query="legacy"))
        == "task ? timeline: 0 event(s)"
    )
    assert "paths=*" in event_query.render_human(
        event_query.QueryResult(
            kind="conflicts",
            query="conflicts at seq 1",
            conflicts=[
                {
                    "left_task": "A",
                    "left_owner": "alpha",
                    "right_task": "B",
                    "right_owner": "beta",
                    "paths": "*",
                }
            ],
        )
    )
    assert event_query.render_human(fallback) == "unknown: no renderer"


def test_release_clears_task_state_at_cutoff(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    _seed_store(db)

    result = event_query.run_query(db, "task T2 at seq 4")

    assert result.state == {}


def test_task_state_ignores_non_snapshot_task_events() -> None:
    result = event_query.execute_query(
        (StoredEvent(seq=1, ts=1.0, kind=EventKind.CHAT, payload={"task_id": "T1"}),),
        event_query.EventQuery(
            kind="task_state",
            task_id="T1",
            cutoff_kind="seq",
            cutoff=1.0,
            raw="task T1 at seq 1",
        ),
    )

    assert result.state == {}


def test_path_query_treats_empty_snapshot_paths_as_repo_wide() -> None:
    events = (
        StoredEvent(
            seq=1,
            ts=10.0,
            kind=EventKind.CLAIM,
            payload=_claim(task_id="ROOT", owner="alpha", paths=()).as_dict(),
        ),
    )

    result = event_query.execute_query(
        events,
        event_query.EventQuery(
            kind="path_touched",
            path="src/auth.py",
            lower=0.0,
            upper=20.0,
            raw="path src/auth.py between 0 20",
        ),
    )

    assert [record.task_id for record in result.records] == ["ROOT"]


def test_conflict_reconstruction_ignores_non_live_or_non_conflicting_events() -> None:
    events = (
        StoredEvent(seq=1, ts=1.0, kind=EventKind.CHAT, payload={"task_id": "NOISE"}),
        StoredEvent(seq=2, ts=2.0, kind=EventKind.CLAIM, payload={}),
        StoredEvent(
            seq=3,
            ts=3.0,
            kind=EventKind.CLAIM,
            payload=_claim(task_id="SAME1", owner="alpha", paths=("src/a.py",)).as_dict(),
        ),
        StoredEvent(
            seq=4,
            ts=4.0,
            kind=EventKind.CLAIM,
            payload=_claim(task_id="SAME2", owner="alpha", paths=("src/a.py",)).as_dict(),
        ),
        StoredEvent(
            seq=5,
            ts=5.0,
            kind=EventKind.CLAIM,
            payload=_claim(
                task_id="OTHER_TREE",
                owner="beta",
                worktree="other",
                paths=("src/a.py",),
            ).as_dict(),
        ),
        StoredEvent(
            seq=6,
            ts=6.0,
            kind=EventKind.CLAIM,
            payload=_claim(task_id="NO_OVERLAP", owner="beta", paths=("docs",)).as_dict(),
        ),
        StoredEvent(
            seq=7,
            ts=7.0,
            kind=EventKind.CLAIM,
            payload=_claim(task_id="ALL", owner="gamma", paths=()).as_dict(),
        ),
    )

    result = event_query.execute_query(
        events,
        event_query.EventQuery(
            kind="conflicts",
            cutoff_kind="seq",
            cutoff=7.0,
            raw="conflicts at seq 7",
        ),
    )

    assert result.conflicts == [
        {
            "left_task": "ALL",
            "left_owner": "gamma",
            "right_task": "NO_OVERLAP",
            "right_owner": "beta",
            "worktree": "repo",
            "paths": ["docs"],
        },
        {
            "left_task": "ALL",
            "left_owner": "gamma",
            "right_task": "SAME1",
            "right_owner": "alpha",
            "worktree": "repo",
            "paths": ["src/a.py"],
        },
        {
            "left_task": "ALL",
            "left_owner": "gamma",
            "right_task": "SAME2",
            "right_owner": "alpha",
            "worktree": "repo",
            "paths": ["src/a.py"],
        },
    ]


def test_query_parser_rejects_invalid_queries(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    _seed_store(db)

    with pytest.raises(ValueError, match="unsupported event query"):
        event_query.run_query(db, "owners now")
    with pytest.raises(ValueError, match="missing event store"):
        event_query.run_query(tmp_path / "missing.db", "task T1 timeline")
    with pytest.raises(ValueError, match="invalid sequence"):
        event_query.run_query(db, "task T1 at seq not-a-number")
    with pytest.raises(ValueError, match="invalid cutoff kind"):
        event_query.run_query(db, "task T1 at height 1")
    with pytest.raises(ValueError, match="invalid timestamp"):
        event_query.run_query(db, "task T1 at time now")
    with pytest.raises(ValueError, match="invalid lower timestamp"):
        event_query.run_query(db, "path src/auth.py between then 30")
    with pytest.raises(ValueError, match="invalid sequence"):
        event_query.run_query(db, 'state("T1", seq, not-a-number).')
    with pytest.raises(ValueError, match="invalid lower timestamp"):
        event_query.run_query(
            db,
            'MATCH (path:PATH {value:"src/auth.py"}) BETWEEN then 30 RETURN events',
        )


def test_execute_query_rejects_unknown_query_kind() -> None:
    with pytest.raises(ValueError, match="unsupported event query kind"):
        event_query.execute_query((), event_query.EventQuery(kind="unknown", raw="unknown"))
