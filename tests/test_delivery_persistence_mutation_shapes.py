# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — delivery ledger: malformed mutations, unknown requests, stored-row edges
"""Refuse malformed mutations before any write; read back legal and damaged stored rows."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Literal, cast

import pytest

from synapse_channel.core.delivery_modes import DeliveryRefusal, parse_delivery_intent
from synapse_channel.core.persistence import EventStore

_RECEIVER = "P/receiver"
# '{"boundary":""}' is 15 bytes, so these encode to exactly 4097 and 4096 bytes.
_OVER_LIMIT = {"boundary": "x" * 4082}
_AT_LIMIT = {"boundary": "x" * 4081}


def _queued(store: EventStore) -> str:
    """Queue one valid request through the public ledger and return its operation key."""
    intent = parse_delivery_intent(
        {
            "type": "delivery_request",
            "target": _RECEIVER,
            "protocol_version": 3,
            "request_id": "req-1",
            "idempotency_key": "idem-1",
            "target_incarnation": "a" * 64,
            "mode": "follow_up",
            "allowed_fallbacks": ["next_turn"],
            "task_id": "T-1",
            "body": "Review this change.",
            "deadline": 160.0,
        },
        sender="P/author",
        origin_hub="hub-1",
        now=100.0,
    )
    store.delivery.create(
        intent,
        selected_mode="follow_up",
        quality="native",
        offer={
            "type": "delivery_offer",
            "operation_key": intent.operation_key,
            "notification_id": f"delivery:{intent.operation_key}:1",
            "target": intent.target,
            "target_incarnation": intent.target_incarnation,
            "request_id": intent.request_id,
            "mode": "follow_up",
            "body": intent.body,
        },
    )
    return intent.operation_key


def _advance(
    store: EventStore,
    operation_key: str,
    *,
    mutation_id: str = "boundary-1",
    mutation_digest: str = "b" * 64,
    source: str = "recipient",
    evidence: dict[str, Any] | None = None,
) -> None:
    store.delivery.advance(
        operation_key,
        stage="boundary_delivered",
        mutation_id=mutation_id,
        mutation_digest=mutation_digest,
        actor=_RECEIVER,
        # A runtime caller can pass any string; the ledger must refuse it itself.
        source=cast(Literal["recipient", "hub"], source),
        evidence={"boundary": "turn-1"} if evidence is None else evidence,
    )


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"mutation_id": ""}, "delivery mutation identity is malformed"),
        ({"mutation_id": "m" * 129}, "delivery mutation identity is malformed"),
        ({"mutation_id": "ok\x1fno"}, "delivery mutation identity is malformed"),
        ({"mutation_id": "ok\x7fno"}, "delivery mutation identity is malformed"),
        ({"mutation_digest": "b" * 63}, "delivery mutation identity is malformed"),
        ({"mutation_digest": "B" * 64}, "delivery mutation identity is malformed"),
        ({"evidence": _OVER_LIMIT}, "delivery evidence exceeds its limit"),
        ({"source": "operator"}, "delivery evidence source is unknown"),
    ],
    ids=[
        "empty-id",
        "long-id",
        "control-id",
        "delete-id",
        "short-digest",
        "upper-digest",
        "evidence-limit",
        "unknown-source",
    ],
)
def test_malformed_mutation_is_refused_without_a_write(
    tmp_path: Path, changes: dict[str, Any], reason: str
) -> None:
    """Shape, size and source errors are refused and leave the aggregate and events alone."""
    with EventStore(tmp_path / "hub.db") as store:
        key = _queued(store)
        before = (store.delivery.get(key), tuple(store.iter_events()))
        with pytest.raises(DeliveryRefusal, match=f"^{reason}$") as caught:
            _advance(store, key, **changes)
        assert caught.value.code == "invalid_shape"
        assert (store.delivery.get(key), tuple(store.iter_events())) == before


def test_mutations_exactly_at_both_limits_are_accepted(tmp_path: Path) -> None:
    """Both limits are inclusive: a 128-byte id with 4096-byte evidence advances."""
    with EventStore(tmp_path / "hub.db") as store:
        key = _queued(store)
        _advance(store, key, mutation_id="m" * 128, evidence=_AT_LIMIT)
        record = store.delivery.get(key)
        assert record is not None
        assert record.stage == "boundary_delivered"


def test_an_unknown_request_is_refused(tmp_path: Path) -> None:
    """A transition for an operation key the ledger never admitted names the request."""
    with EventStore(tmp_path / "hub.db") as store:
        _queued(store)
        events = tuple(store.iter_events())
        with pytest.raises(DeliveryRefusal, match="^delivery request does not exist$") as caught:
            _advance(store, "missing-operation")
        assert caught.value.code == "unknown_request"
        assert tuple(store.iter_events()) == events


def test_stored_rows_project_a_legal_stage_and_refuse_a_malformed_frame(tmp_path: Path) -> None:
    """An 'accepted' row reads back as accepted; a non-object frame is replay-incompatible."""
    path = tmp_path / "hub.db"
    with EventStore(path) as store:
        key = _queued(store)
        notification_id = f"delivery:{key}:1"
        with sqlite3.connect(path) as edited:
            edited.execute(
                "UPDATE delivery_requests SET stage = 'accepted' WHERE operation_key = ?", (key,)
            )
            edited.execute(
                "UPDATE delivery_notifications SET frame_json = '[]' WHERE notification_id = ?",
                (notification_id,),
            )
        record = store.delivery.get(key)
        assert record is not None
        assert record.stage == "accepted"
        with pytest.raises(DeliveryRefusal, match="^stored delivery frame is malformed$") as caught:
            store.delivery.notification(notification_id)
        assert caught.value.code == "replay_incompatible"
