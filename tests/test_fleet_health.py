# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — content-minimized local fleet health regressions

from __future__ import annotations

from pathlib import Path

import pytest

from synapse_channel.core.fleet_health import (
    POLICY_VERSION,
    build_local_fleet_health,
    local_fleet_health_to_json,
    run_local_fleet_health,
)
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore, StoredEvent
from synapse_channel.core.state import TaskClaim


def _claim(
    task_id: str,
    owner: str,
    *,
    expiry: object,
    path: str = "src/shared.py",
    status: str = "claimed",
) -> dict[str, object]:
    payload = TaskClaim(
        task_id=task_id,
        owner=owner,
        note="private work detail",
        claimed_at=1.0,
        lease_expires_at=200.0,
        status=status,
        data_ref="",
        worktree="/repo",
        paths=(path,),
        epoch=1,
        checkpoint="",
    ).as_dict()
    payload["lease_expires_at"] = expiry
    return payload


def _event(seq: int, kind: str, payload: dict[str, object]) -> StoredEvent:
    return StoredEvent(seq=seq, ts=float(seq), kind=kind, payload=payload)


def test_local_fleet_health_is_count_only_and_classifies_red() -> None:
    events = [
        _event(1, EventKind.CLAIM, _claim("A", "alpha", expiry=50.0)),
        _event(2, EventKind.CLAIM, _claim("B", "beta", expiry=200.0)),
        _event(
            3,
            EventKind.DELIVERY_RECEIPT_IMMEDIATE,
            {"dead_lettered": True, "target": "private/seat", "message": "secret"},
        ),
        _event(4, EventKind.DELIVERY_RECEIPT_DEFERRED, {"message_seq": 3}),
        _event(5, EventKind.DEAD_LETTER_ESCALATION, {"target": "private/seat"}),
    ]

    report = build_local_fleet_health(events, generated_at=100.0)
    document = local_fleet_health_to_json(report)

    assert report.level == "red"
    assert report.contention_pairs == 1
    assert report.expired_claims == 1
    assert report.dead_lettered_messages == 1
    assert report.recovered_messages == 1
    assert report.dead_letter_escalations == 1
    assert document["policy_version"] == POLICY_VERSION
    assert document["first_retained_seq"] == 1
    assert document["generated_from_seq"] == 5
    assert document["retained_events"] == 5
    assert document["telemetry"] == "none"
    rendered = repr(document)
    assert "private/seat" not in rendered
    assert "secret" not in rendered
    assert "alpha" not in rendered
    assert "src/shared.py" not in rendered


def test_local_fleet_health_folds_release_completion_and_malformed_expiry() -> None:
    events = [
        _event(1, EventKind.CLAIM, _claim("released", "a", expiry=1.0, path="a.py")),
        _event(2, EventKind.RELEASE, {"task_id": "released"}),
        _event(3, EventKind.CLAIM, _claim("done", "a", expiry=1.0, status="done")),
        _event(4, EventKind.CLAIM, _claim("bool", "a", expiry=True)),
        _event(5, EventKind.CLAIM, _claim("text", "a", expiry="soon")),
        _event(6, EventKind.CLAIM, _claim("nan", "a", expiry=float("nan"))),
        _event(7, EventKind.CLAIM, {"owner": "a", "lease_expires_at": 1.0}),
        _event(8, EventKind.CHAT, {"task_id": "ignored", "owner": "a"}),
        _event(9, EventKind.CLAIM, {**_claim("ownerless", "a", expiry=1.0), "owner": ""}),
    ]

    report = build_local_fleet_health(events, generated_at=10.0)

    assert report.level == "green"
    assert report.expired_claims == 0
    assert report.contention_pairs == 0
    assert report.first_retained_seq == 1
    assert report.generated_from_seq == 9


def test_local_fleet_health_marks_contention_and_dead_letters_amber() -> None:
    contention = [
        _event(1, EventKind.CLAIM, _claim("A", "alpha", expiry=200.0)),
        _event(2, EventKind.CLAIM, _claim("B", "beta", expiry=200.0)),
    ]
    assert build_local_fleet_health(contention, generated_at=100.0).level == "amber"
    dead_letter = [_event(1, EventKind.DELIVERY_RECEIPT_IMMEDIATE, {"dead_lettered": True})]
    assert build_local_fleet_health(dead_letter, generated_at=100.0).level == "amber"


def test_local_fleet_health_empty_and_invalid_timestamp() -> None:
    report = build_local_fleet_health([], generated_at=0.0)
    assert report.level == "green"
    assert report.first_retained_seq == 0
    assert report.generated_from_seq == 0
    with pytest.raises(ValueError, match="generated_at must be finite"):
        build_local_fleet_health([], generated_at=float("inf"))


def test_run_local_fleet_health_reads_the_real_store(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    store = EventStore(db)
    store.append(EventKind.CLAIM, _claim("A", "alpha", expiry=5.0), ts=1.0)
    store.append(EventKind.CHAT, {"text": "retained clock edge"}, ts=10.0)
    store.close()

    report = run_local_fleet_health(db)

    assert report.retained_events == 2
    assert report.expired_claims == 1
    assert run_local_fleet_health(db, generated_at=1.0).expired_claims == 0
    with pytest.raises(ValueError, match="missing event store"):
        run_local_fleet_health(tmp_path / "missing.db")
