# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real SQLite operation and outbox recovery regressions

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import cast

import pytest

from synapse_channel.core.persistence import EventStore, OperationCommitResult


def _commit(
    store: EventStore,
    *,
    key: str = "operation",
    digest: str = "a" * 64,
    events: tuple[tuple[str, dict[str, object]], ...] = (("claim", {"task_id": "task"}),),
) -> OperationCommitResult:
    return store.commit_operation(
        operation_key=key,
        request_digest=digest,
        response={"type": "claim_granted", "task_id": "task"},
        events=events,
        intent={"family": "claim"},
    )


def _rows(path: Path, query: str) -> list[tuple[object, ...]]:
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as reader:
        return reader.execute(query).fetchall()


@pytest.mark.parametrize(
    ("key", "digest", "events", "reason"),
    [
        ("", "a" * 64, (("claim", {}),), "key must be non-empty"),
        ("operation", "a" * 63, (("claim", {}),), "lowercase SHA-256"),
        ("operation", "A" * 64, (("claim", {}),), "lowercase SHA-256"),
        ("operation", "a" * 64, (), "at least one mutation event"),
    ],
)
def test_invalid_operation_preserves_prior_history_and_allows_retry(
    tmp_path: Path,
    key: str,
    digest: str,
    events: tuple[tuple[str, dict[str, object]], ...],
    reason: str,
) -> None:
    path = tmp_path / "invalid-operation.db"
    with EventStore(path, aef_outbox_kinds={"claim"}) as store:
        prior = store.append("chat", {"retained": True})
        with pytest.raises(ValueError, match=reason):
            _commit(store, key=key, digest=digest, events=events)
        assert [event.seq for event in store.read_all()] == [prior]
        assert store.read_operations() == ()
        assert store.pending_operation_intents() == ()
        assert store.pending_aef_events() == ()
        assert _rows(path, "SELECT COUNT(*) FROM operations") == [(0,)]
        winner = _commit(store)
        assert winner.outcome == "inserted"
        assert [event.seq for event in store.pending_aef_events()] == [
            winner.operation.first_event_seq
        ]
    with EventStore(path) as reopened:
        assert _commit(reopened).outcome == "replayed"
        assert reopened.count() == 3
        assert reopened.get_operation("operation") == winner.operation


@pytest.mark.parametrize("surface", ["response", "intent"])
@pytest.mark.parametrize("encoded", ["[]", "null", "broken-json"])
def test_damaged_operation_rows_refuse_without_rewriting_then_recover(
    tmp_path: Path, surface: str, encoded: str
) -> None:
    path = tmp_path / "damaged-operation.db"
    with EventStore(path) as store:
        winner = _commit(store)
    table, column = (
        ("operations", "response_json")
        if surface == "response"
        else ("operation_outbox", "intent_json")
    )
    with sqlite3.connect(path) as writer:
        original = writer.execute(f"SELECT {column} FROM {table}").fetchone()[0]
        writer.execute(f"UPDATE {table} SET {column} = ?", (encoded,))
    with EventStore(path) as store:
        if surface == "response":
            with pytest.raises(ValueError):
                store.get_operation("operation")
            with pytest.raises(ValueError):
                store.read_operations()
            with pytest.raises(ValueError):
                _commit(store)
        else:
            with pytest.raises(ValueError):
                store.pending_operation_intents()
        assert store.count() == 2
        assert _rows(path, f"SELECT {column} FROM {table}") == [(encoded,)]
        with sqlite3.connect(path) as writer:
            writer.execute(f"UPDATE {table} SET {column} = ?", (original,))
        assert _commit(store).outcome == "replayed"
        assert store.get_operation("operation") == winner.operation
        assert store.pending_operation_intents() == (("operation", {"family": "claim"}),)
        store.mark_operation_intent_delivered("operation", "local:receipt")
    with EventStore(path) as reopened:
        assert reopened.pending_operation_intents() == ()
        assert reopened.get_operation("operation") == winner.operation
        assert reopened.count() == 2


@pytest.mark.parametrize(
    ("key", "receipt", "error"),
    [
        ("", "local:receipt", ValueError),
        ("operation", "", ValueError),
        ("missing", "local:receipt", KeyError),
        ("operation", "local:different", KeyError),
    ],
)
def test_outbox_binding_refusal_preserves_settled_receipt_and_other_pending_intent(
    tmp_path: Path, key: str, receipt: str, error: type[Exception]
) -> None:
    path = tmp_path / "outbox-binding.db"
    with EventStore(path) as store:
        winner = _commit(store)
        _commit(store, key="other")
        store.mark_operation_intent_delivered("operation", "local:receipt")
        store.mark_operation_intent_delivered("operation", "local:receipt")
        with pytest.raises(error):
            store.mark_operation_intent_delivered(key, receipt)
        assert store.pending_operation_intents() == (("other", {"family": "claim"}),)
        assert _rows(path, "SELECT receipt_id FROM operation_outbox ORDER BY rowid") == [
            ("local:receipt",),
            (None,),
        ]
        assert store.get_operation("operation") == winner.operation
        store.mark_operation_intent_delivered("other", "local:other")
    with EventStore(path) as reopened:
        assert reopened.pending_operation_outbox_count() == 0
        assert _commit(reopened).outcome == "replayed"
        assert reopened.count() == 4


def test_sqlite_trigger_refusal_rolls_back_outbox_binding_then_same_store_recovers(
    tmp_path: Path,
) -> None:
    path = tmp_path / "outbox-trigger.db"
    with EventStore(path) as store:
        winner = _commit(store)
        with sqlite3.connect(path) as writer:
            writer.execute(
                "CREATE TRIGGER reject_binding BEFORE UPDATE ON operation_outbox "
                "BEGIN SELECT RAISE(ABORT, 'binding unavailable'); END"
            )
        with pytest.raises(sqlite3.IntegrityError, match="binding unavailable"):
            store.mark_operation_intent_delivered("operation", "local:receipt")
        assert store.pending_operation_intents() == (("operation", {"family": "claim"}),)
        assert _rows(path, "SELECT receipt_id FROM operation_outbox") == [(None,)]
        with sqlite3.connect(path) as writer:
            writer.execute("DROP TRIGGER reject_binding")
        store.mark_operation_intent_delivered("operation", "local:receipt")
    with EventStore(path) as reopened:
        assert reopened.pending_operation_outbox_count() == 0
        assert reopened.get_operation("operation") == winner.operation
        assert reopened.count() == 2


@pytest.mark.parametrize(("sequence", "receipt"), [(0, "receipt"), (True, "receipt"), (1, "")])
def test_invalid_aef_binding_keeps_committed_source_pending_across_reopen(
    tmp_path: Path, sequence: int, receipt: str
) -> None:
    path = tmp_path / "aef-binding.db"
    with EventStore(path, aef_outbox_kinds={"claim"}) as store:
        winner = _commit(store)
        assert store.append_batch((), durable=True) == ()
        with pytest.raises(ValueError, match="AEF outbox"):
            store.mark_aef_delivered(sequence, receipt)
        assert store.aef_delivery(winner.operation.first_event_seq) is None
        assert [event.seq for event in store.pending_aef_events()] == [
            winner.operation.first_event_seq
        ]
        assert store.count() == 2
    with EventStore(path) as reopened:
        assert _commit(reopened).outcome == "replayed"
        reopened.mark_aef_delivered(winner.operation.first_event_seq, "aef:receipt")
        assert reopened.pending_aef_events() == ()
        assert reopened.aef_delivery(winner.operation.first_event_seq) == "aef:receipt"


@pytest.mark.parametrize("committed", [False, True])
def test_real_sqlite_cleanup_refusal_preserves_atomic_commit_truth(
    tmp_path: Path, committed: bool, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "cleanup-refusal.db"
    with EventStore(path) as store:
        # The SQLite authorizer injects an engine refusal on the real connection;
        # writes, rollback, commit and replay still use the public store API.
        connection = cast(sqlite3.Connection, store._conn)

        def authorize(
            action: int,
            first: str | None,
            second: str | None,
            database: str | None,
            origin: str | None,
        ) -> int:
            if action == sqlite3.SQLITE_PRAGMA and first == "synchronous" and second == "NORMAL":
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        def fail(stage: str) -> None:
            if stage == ("after_commit" if committed else "before_commit"):
                connection.set_authorizer(authorize)
                if not committed:
                    raise OSError("precommit interrupted")

        def commit() -> OperationCommitResult:
            return store.commit_operation(
                operation_key="operation",
                request_digest="a" * 64,
                response={"type": "claim_granted", "task_id": "task"},
                events=(("claim", {"task_id": "task"}),),
                intent={"family": "claim"},
                stage_hook=fail,
            )

        try:
            if committed:
                assert commit().outcome == "inserted"
                assert "Could not restore SQLite synchronous=NORMAL" in caplog.text
            else:
                with pytest.raises(sqlite3.DatabaseError, match="not authorized") as caught:
                    commit()
                assert isinstance(caught.value.__context__, OSError)
        finally:
            # Python 3.10 requires a callable; None disables only from 3.11.
            connection.set_authorizer(
                lambda _action, _first, _second, _database, _origin: sqlite3.SQLITE_OK
            )
        expected = 1 if committed else 0
        assert _rows(path, "SELECT COUNT(*) FROM operations") == [(expected,)]
        assert store.pending_operation_outbox_count() == expected
        assert store.count() == expected * 2
        retry = _commit(store)
        assert retry.outcome == ("replayed" if committed else "inserted")
        assert store.count() == 2
    with EventStore(path) as reopened:
        assert reopened.get_operation("operation") == retry.operation
        assert _commit(reopened).outcome == "replayed"
        assert reopened.count() == 2
