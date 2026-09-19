# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — startup replay verification for delivery intents
"""Corrupt the real SQLite journal and require fail-closed hub-store reopening."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.core.delivery_modes import DeliveryRefusal, parse_delivery_intent
from synapse_channel.core.persistence import EventStore


def _seed(path: Path) -> str:
    """Commit one valid queued request through the public event-store path."""
    intent = parse_delivery_intent(
        {
            "type": "delivery_request",
            "target": "P/receiver",
            "protocol_version": 3,
            "request_id": "req-1",
            "idempotency_key": "idem-1",
            "target_incarnation": "a" * 64,
            "mode": "next_turn",
            "allowed_fallbacks": [],
            "task_id": "T-1",
            "body": "Continue the reviewed work.",
            "deadline": 160.0,
        },
        sender="P/author",
        origin_hub="hub-1",
        now=100.0,
    )
    key = intent.operation_key
    with EventStore(path) as store:
        result = store.delivery.create(
            intent,
            selected_mode="next_turn",
            quality="emulated",
            offer={
                "type": "delivery_offer",
                "operation_key": key,
                "notification_id": f"delivery:{key}:1",
                "target": intent.target,
                "target_incarnation": intent.target_incarnation,
                "body": intent.body,
            },
        )
        assert result.disposition == "inserted"
    return key


def _change_event(path: Path, seq: int, changes: dict[str, Any]) -> None:
    """Simulate a damaged or tampered persisted event outside the hub."""
    with sqlite3.connect(path) as connection:
        row = connection.execute("SELECT payload FROM events WHERE seq = ?", (seq,)).fetchone()
        assert row is not None
        payload = json.loads(row[0])
        payload.update(changes)
        connection.execute(
            "UPDATE events SET payload = ? WHERE seq = ?",
            (json.dumps(payload), seq),
        )


@pytest.mark.parametrize(
    "damage",
    ["wrong_profile", "missing_queued", "wrong_aggregate", "wrong_notification", "duplicate_queue"],
)
def test_reopen_rejects_incompatible_delivery_history(tmp_path: Path, damage: str) -> None:
    """A checksum-looking table is insufficient when the event chain disagrees."""
    path = tmp_path / "hub.db"
    key = _seed(path)
    if damage == "wrong_profile":
        _change_event(path, 1, {"profile": 4})
    else:
        with sqlite3.connect(path) as connection:
            if damage == "missing_queued":
                connection.execute("DELETE FROM events WHERE seq = 2")
            elif damage == "wrong_aggregate":
                connection.execute(
                    "UPDATE delivery_requests SET stage = 'completed' WHERE operation_key = ?",
                    (key,),
                )
            elif damage == "wrong_notification":
                connection.execute(
                    "UPDATE delivery_notifications SET audience = 'P/attacker' "
                    "WHERE operation_key = ?",
                    (key,),
                )
            else:
                queued = connection.execute("SELECT payload FROM events WHERE seq = 2").fetchone()
                assert queued is not None
                connection.execute(
                    "INSERT INTO events (ts, kind, payload) VALUES (1.0, ?, ?)",
                    ("delivery_intent_queued", queued[0]),
                )
    with pytest.raises(DeliveryRefusal) as failure:
        EventStore(path)
    assert failure.value.code == "replay_incompatible"


def test_unmodified_delivery_history_reopens(tmp_path: Path) -> None:
    """The same verifier admits an unchanged request and its queued offer."""
    path = tmp_path / "hub.db"
    key = _seed(path)
    with EventStore(path) as store:
        row = store.delivery.get(key)
        assert row is not None
        assert row.stage == "queued"


@pytest.mark.parametrize(
    ("sequence", "field", "value"),
    [
        (1, "operation_key", "not-a-key"),
        (1, "request.profile", 4),
        (1, "request.sender", ""),
        (1, "request.origin_hub", ""),
        (1, "request.request_id", ""),
        (1, "request.idempotency_key", ""),
        (1, "request.deadline", True),
        (1, "request.deadline", float("nan")),
        (1, "request.mode", "unknown"),
        (1, "request.extra", "unexpected"),
        (1, "digest", "0" * 64),
        (2, "ordinal", 4),
        (2, "stage", "completed"),
        (2, "selected_mode", "interrupt"),
        (2, "quality", "unknown"),
        (2, "notification_id", "wrong"),
    ],
)
def test_reopen_refuses_tampered_request_or_queue_field(
    tmp_path: Path, sequence: int, field: str, value: object
) -> None:
    """Every control field in a committed admission remains replay-verifiable."""
    path = tmp_path / "hub.db"
    _seed(path)
    with sqlite3.connect(path) as connection:
        row = connection.execute("SELECT payload FROM events WHERE seq = ?", (sequence,)).fetchone()
        assert row is not None
        payload = json.loads(row[0])
        if field.startswith("request."):
            payload["request"][field.removeprefix("request.")] = value
        else:
            payload[field] = value
        connection.execute(
            "UPDATE events SET payload = ? WHERE seq = ?",
            (json.dumps(payload), sequence),
        )
    with pytest.raises(DeliveryRefusal) as failure:
        EventStore(path)
    assert failure.value.code == "replay_incompatible"


@pytest.mark.parametrize("raw", ["{invalid", "[]", "null"])
def test_reopen_refuses_non_object_delivery_event(tmp_path: Path, raw: str) -> None:
    """Malformed event JSON cannot be projected into a plausible queue."""
    path = tmp_path / "hub.db"
    _seed(path)
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE events SET payload = ? WHERE seq = 1", (raw,))
    with pytest.raises(DeliveryRefusal) as failure:
        EventStore(path)
    assert failure.value.code == "replay_incompatible"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("ordinal", 9),
        ("prior_stage", "accepted"),
        ("mutation_id", ""),
        ("mutation_digest", "invalid"),
        ("actor", "P/attacker"),
        ("source", "hub"),
        ("stage", "completed"),
        ("evidence", "invalid"),
        ("cancel_requested", True),
        ("boundary_delivered", False),
        ("explicitly_acknowledged", True),
        ("notification_id", "wrong"),
    ],
)
def test_reopen_refuses_tampered_stage_evidence(tmp_path: Path, field: str, value: object) -> None:
    """An event/aggregate mismatch or forged recipient stage blocks startup."""
    path = tmp_path / "hub.db"
    key = _seed(path)
    with EventStore(path) as store:
        store.delivery.advance(
            key,
            stage="boundary_delivered",
            mutation_id="boundary-1",
            mutation_digest="b" * 64,
            actor="P/receiver",
            source="recipient",
            evidence={"boundary": "turn-1"},
        )
    _change_event(path, 3, {field: value})
    with pytest.raises(DeliveryRefusal) as failure:
        EventStore(path)
    assert failure.value.code == "replay_incompatible"


@pytest.mark.parametrize(
    ("table", "statement"),
    [
        ("mutations", "DELETE FROM delivery_mutations"),
        ("notifications", "DELETE FROM delivery_notifications"),
        (
            "frame",
            'UPDATE delivery_notifications SET frame_json = \'{"operation_key":"wrong"}\'',
        ),
        ("malformed_frame", "UPDATE delivery_notifications SET frame_json = '[]'"),
    ],
)
def test_reopen_refuses_damaged_indexes_and_outbox(
    tmp_path: Path, table: str, statement: str
) -> None:
    """The append-only event stream remains authoritative over indexes/outbox."""
    path = tmp_path / "hub.db"
    key = _seed(path)
    if table == "mutations":
        with EventStore(path) as store:
            store.delivery.advance(
                key,
                stage="boundary_delivered",
                mutation_id="boundary-1",
                mutation_digest="b" * 64,
                actor="P/receiver",
                source="recipient",
                evidence={"boundary": "turn-1"},
            )
    with sqlite3.connect(path) as connection:
        connection.execute(statement)
    with pytest.raises(DeliveryRefusal) as failure:
        EventStore(path)
    assert failure.value.code == "replay_incompatible"
