# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — adaptive lease TTL advice regressions

from __future__ import annotations

from pathlib import Path
from typing import cast

from synapse_channel.core.adaptive_ttl import (
    build_ttl_advice,
    render_human,
    run_ttl_advice,
    ttl_advice_to_json,
)
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.state import TaskClaim


def _claim(
    *,
    task_id: str,
    owner: str,
    claimed_at: float,
    lease_expires_at: float = 10_000.0,
) -> TaskClaim:
    return TaskClaim(
        task_id=task_id,
        owner=owner,
        note="work",
        claimed_at=claimed_at,
        lease_expires_at=lease_expires_at,
        status="claimed",
        data_ref="",
        worktree="repo",
        paths=(f"src/{task_id}.py",),
        epoch=1,
    )


def _seed_ttl_store(path: Path) -> None:
    store = EventStore(path)
    samples = [
        ("TASK-A", "alpha", 0.0, 100.0),
        ("TASK-B", "alpha", 10.0, 210.0),
        ("TASK-C", "beta", 20.0, 420.0),
    ]
    for task_id, owner, start, release in samples:
        store.append(
            EventKind.CLAIM,
            _claim(task_id=task_id, owner=owner, claimed_at=start).as_dict(),
            ts=start,
            durable=True,
        )
        store.append(EventKind.RELEASE, {"task_id": task_id}, ts=release, durable=True)
    store.append(
        EventKind.CLAIM,
        _claim(
            task_id="ACTIVE-STALE",
            owner="gamma",
            claimed_at=50.0,
            lease_expires_at=80.0,
        ).as_dict(),
        ts=50.0,
        durable=True,
    )
    store.append(
        EventKind.CLAIM,
        _claim(
            task_id="ACTIVE-LIVE",
            owner="delta",
            claimed_at=90.0,
            lease_expires_at=900.0,
        ).as_dict(),
        ts=90.0,
        durable=True,
    )
    store.close()


def test_ttl_advice_uses_completed_task_samples_without_changing_defaults(
    tmp_path: Path,
) -> None:
    db = tmp_path / "events.db"
    _seed_ttl_store(db)
    store = EventStore(db)
    try:
        report = build_ttl_advice(
            store.read_all(),
            as_of=300.0,
            current_default_seconds=3600.0,
            min_samples=3,
            min_owner_samples=2,
            safety_multiplier=1.5,
        )
    finally:
        store.close()

    assert report.generated_from_seq == 8
    assert report.sample_count == 3
    assert report.active_claims == 2
    assert report.stale_claims == 1
    assert report.p90_seconds == 400.0
    assert report.recommended_default_seconds == 600.0
    assert report.confidence == "medium"
    assert report.owner_advice[0].owner == "alpha"
    assert report.owner_advice[0].sample_count == 2
    assert report.owner_advice[0].recommended_seconds == 300.0
    assert "manual_ttl_preserved" in report.notes


def test_ttl_advice_falls_back_when_sample_count_is_low(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    store = EventStore(db)
    store.append(
        EventKind.CLAIM,
        _claim(task_id="TASK-A", owner="alpha", claimed_at=0.0).as_dict(),
        ts=0.0,
        durable=True,
    )
    store.append(EventKind.RELEASE, {"task_id": "TASK-A"}, ts=100.0, durable=True)
    store.close()

    report = run_ttl_advice(db, min_samples=3, current_default_seconds=1200.0)

    assert report.sample_count == 1
    assert report.recommended_default_seconds == 1200.0
    assert report.confidence == "low"
    assert report.owner_advice == ()


def test_ttl_advice_handles_empty_and_ignored_event_rows(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    store = EventStore(db)
    store.append(EventKind.RELEASE, {"task_id": "UNKNOWN"}, ts=1.0, durable=True)
    store.append(EventKind.LEDGER_PROGRESS, {"text": "no task id"}, ts=2.0, durable=True)
    store.close()

    report = run_ttl_advice(
        db,
        current_default_seconds=-1.0,
        min_ttl_seconds=-1.0,
        max_ttl_seconds=-1.0,
        safety_multiplier=0.0,
    )

    assert report.as_of == 2.0
    assert report.sample_count == 0
    assert report.p90_seconds == 0.0
    assert report.recommended_default_seconds == 3600.0
    assert report.confidence == "none"
    assert report.notes == (
        "manual_ttl_preserved",
        "read_only_event_log",
        "insufficient_samples_fallback",
    )


def test_ttl_advice_high_confidence_clamps_to_bounds(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    store = EventStore(db)
    for index in range(10):
        task_id = f"TASK-{index}"
        start = float(index * 1000)
        release = start + float((index + 1) * 100)
        store.append(
            EventKind.CLAIM,
            _claim(task_id=task_id, owner="alpha", claimed_at=start).as_dict(),
            ts=start,
            durable=True,
        )
        store.append(EventKind.RELEASE, {"task_id": task_id}, ts=release, durable=True)
    store.close()

    report = run_ttl_advice(
        db,
        min_samples=3,
        max_ttl_seconds=500.0,
        safety_multiplier=2.0,
    )

    assert report.sample_count == 10
    assert report.p90_seconds == 900.0
    assert report.recommended_default_seconds == 500.0
    assert report.confidence == "high"
    assert report.notes == ("manual_ttl_preserved", "read_only_event_log")


def test_ttl_advice_json_and_human_renderers_are_stable(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    _seed_ttl_store(db)

    report = run_ttl_advice(db, as_of=300.0, min_samples=3, min_owner_samples=2)
    payload = ttl_advice_to_json(report)
    text = render_human(report)

    assert payload["generated_from_seq"] == 8
    assert payload["recommended_default_seconds"] == 600.0
    owners = cast(list[dict[str, object]], payload["owner_advice"])
    assert owners[0]["owner"] == "alpha"
    assert "Lease TTL advice: advisory, manual TTL preserved" in text
    assert "recommended_default_seconds=600.000" in text
    assert "active_claims=2 stale_claims=1" in text


def test_missing_ttl_advice_store_reports_error(tmp_path: Path) -> None:
    missing = tmp_path / "missing.db"

    try:
        run_ttl_advice(missing)
    except ValueError as exc:
        assert "missing event store" in str(exc)
    else:
        raise AssertionError("missing TTL advice store was accepted")
