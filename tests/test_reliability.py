# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — reliability memory regressions

from __future__ import annotations

from pathlib import Path
from typing import cast

from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.reliability import (
    build_reliability_report,
    reliability_to_json,
    render_human,
    run_reliability_report,
)
from synapse_channel.core.state import TaskClaim


def _claim(
    *,
    task_id: str = "TASK-1",
    owner: str = "alpha",
    note: str = "work",
    claimed_at: float = 10.0,
    lease_expires_at: float = 90.0,
    status: str = "claimed",
    data_ref: str = "",
    worktree: str = "repo",
    paths: tuple[str, ...] = ("src/auth.py",),
    epoch: int = 1,
    checkpoint: str = "",
) -> TaskClaim:
    return TaskClaim(
        task_id=task_id,
        owner=owner,
        note=note,
        claimed_at=claimed_at,
        lease_expires_at=lease_expires_at,
        status=status,
        data_ref=data_ref,
        worktree=worktree,
        paths=paths,
        epoch=epoch,
        checkpoint=checkpoint,
    )


def _seed_reliability_store(path: Path) -> None:
    store = EventStore(path)
    store.append(
        EventKind.CLAIM,
        _claim(
            task_id="STALE",
            owner="alpha",
            paths=("stale/task.py",),
            lease_expires_at=20.0,
        ).as_dict(),
        ts=1.0,
        durable=True,
    )
    store.append(
        EventKind.LEDGER_PROGRESS,
        {
            "task_id": "STALE",
            "author": "alpha",
            "kind": "assessment",
            "text": "release receipt: known_failures=ruff failed; epistemic_status=degraded",
            "posted_at": 2.0,
        },
        ts=2.0,
    )
    store.append(
        EventKind.CLAIM,
        _claim(
            task_id="OVERLAP-A",
            owner="alpha",
            paths=("src/api.py",),
            lease_expires_at=200.0,
        ).as_dict(),
        ts=3.0,
        durable=True,
    )
    store.append(
        EventKind.CLAIM,
        _claim(
            task_id="OVERLAP-B",
            owner="beta",
            paths=("src",),
            epoch=2,
            lease_expires_at=200.0,
        ).as_dict(),
        ts=4.0,
        durable=True,
    )
    store.append(
        EventKind.HANDOFF,
        _claim(
            task_id="HANDOFF-BROKEN",
            owner="gamma",
            paths=("docs/broken.md",),
            epoch=3,
            lease_expires_at=30.0,
        ).as_dict(),
        ts=5.0,
        durable=True,
    )
    store.append(
        EventKind.HANDOFF,
        _claim(
            task_id="HANDOFF-OK",
            owner="delta",
            paths=("docs/ok.md",),
            epoch=4,
            lease_expires_at=30.0,
        ).as_dict(),
        ts=6.0,
        durable=True,
    )
    store.append(
        EventKind.TASK_UPDATE,
        _claim(
            task_id="HANDOFF-OK",
            owner="delta",
            status="in_progress",
            paths=("docs/ok.md",),
            epoch=5,
            lease_expires_at=120.0,
        ).as_dict(),
        ts=7.0,
        durable=True,
    )
    store.append(
        EventKind.CLAIM,
        _claim(
            task_id="RELEASED",
            owner="epsilon",
            paths=("released/task.py",),
            lease_expires_at=8.0,
        ).as_dict(),
        ts=8.0,
        durable=True,
    )
    store.append(EventKind.RELEASE, {"task_id": "RELEASED"}, ts=9.0, durable=True)
    store.close()


def test_reliability_report_tracks_signals_without_scores(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    _seed_reliability_store(db)
    store = EventStore(db)
    try:
        report = build_reliability_report(store.read_all(), as_of=100.0)
    finally:
        store.close()

    assert report.generated_from_seq == 9
    assert report.as_of == 100.0
    assert [finding.kind for finding in report.findings] == [
        "stale_claim",
        "declared_failed_check",
        "conflict_pair",
        "conflict_pair",
        "broken_handoff_candidate",
        "stale_claim",
    ]
    alpha = report.summary_by_owner["alpha"]
    beta = report.summary_by_owner["beta"]
    gamma = report.summary_by_owner["gamma"]
    assert "delta" not in report.summary_by_owner
    assert alpha.stale_claims == 1
    assert alpha.declared_failed_checks == 1
    assert alpha.conflict_pairs == 1
    assert beta.conflict_pairs == 1
    assert gamma.stale_claims == 1
    assert gamma.broken_handoffs == 1


def test_reliability_json_and_human_renderers_are_stable(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    _seed_reliability_store(db)

    report = run_reliability_report(db, as_of=100.0)
    payload = reliability_to_json(report)
    text = render_human(report)

    assert payload["generated_from_seq"] == 9
    assert payload["as_of"] == 100.0
    summaries = cast(list[dict[str, object]], payload["owners"])
    findings = cast(list[dict[str, object]], payload["findings"])
    assert summaries[0]["owner"] == "alpha"
    assert summaries[0]["declared_failed_checks"] == 1
    assert findings[0]["kind"] == "stale_claim"
    assert "Reliability memory: audit signals, not scores" in text
    assert "declared_failed_check" in text
    assert "broken_handoff_candidate" in text
    assert "score" not in text.lower().replace("not scores", "")


def test_reliability_report_without_signals_is_explicit(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    store = EventStore(db)
    store.append(
        EventKind.CLAIM,
        _claim(task_id="LIVE", owner="alpha", lease_expires_at=200.0).as_dict(),
        ts=1.0,
        durable=True,
    )
    store.close()

    report = run_reliability_report(db, as_of=100.0)

    assert report.findings == ()
    assert (
        render_human(report)
        == "Reliability memory: audit signals, not scores\n\nNo reliability signals found."
    )


def test_reliability_report_handles_edge_event_paths(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    store = EventStore(db)
    store.append(
        EventKind.LEDGER_PROGRESS,
        {
            "task_id": "NOTE",
            "author": "alpha",
            "kind": "note",
            "text": "release receipt: checks passed",
            "posted_at": 1.0,
        },
        ts=1.0,
        durable=True,
    )
    store.append(
        EventKind.HANDOFF,
        _claim(
            task_id="HANDOFF-RELEASED",
            owner="beta",
            paths=("docs/released.md",),
            worktree="handoffs",
            lease_expires_at=2.0,
        ).as_dict(),
        ts=2.0,
        durable=True,
    )
    store.append(EventKind.RELEASE, {"task_id": "HANDOFF-RELEASED"}, ts=3.0, durable=True)
    store.append(
        EventKind.HANDOFF,
        _claim(
            task_id="HANDOFF-FUTURE",
            owner="gamma",
            paths=("docs/future.md",),
            worktree="handoffs",
            lease_expires_at=200.0,
        ).as_dict(),
        ts=4.0,
        durable=True,
    )
    store.append(
        EventKind.HANDOFF,
        _claim(
            task_id="HANDOFF-WRONG-OWNER",
            owner="delta",
            paths=("docs/wrong.md",),
            worktree="handoffs",
            lease_expires_at=5.0,
        ).as_dict(),
        ts=5.0,
        durable=True,
    )
    store.append(
        EventKind.LEDGER_PROGRESS,
        {
            "task_id": "HANDOFF-WRONG-OWNER",
            "author": "epsilon",
            "kind": "note",
            "text": "receiver looked at task context",
            "posted_at": 5.5,
        },
        ts=5.5,
        durable=True,
    )
    store.append(
        EventKind.TASK_UPDATE,
        _claim(
            task_id="HANDOFF-WRONG-OWNER",
            owner="epsilon",
            paths=("docs/wrong.md",),
            worktree="handoffs",
            lease_expires_at=200.0,
        ).as_dict(),
        ts=6.0,
        durable=True,
    )
    store.append(
        EventKind.CLAIM,
        _claim(
            task_id="WHOLE-TREE",
            owner="zeta",
            paths=(),
            lease_expires_at=200.0,
        ).as_dict(),
        ts=7.0,
        durable=True,
    )
    store.append(
        EventKind.CLAIM,
        _claim(
            task_id="SPECIFIC",
            owner="eta",
            paths=("src/specific.py",),
            lease_expires_at=200.0,
        ).as_dict(),
        ts=8.0,
        durable=True,
    )
    events = store.read_all()
    store.close()

    empty_report = build_reliability_report(())
    report = build_reliability_report(events, as_of=100.0)

    assert empty_report.as_of == 0.0
    assert empty_report.generated_from_seq == 0
    assert empty_report.findings == ()
    assert [finding.kind for finding in report.findings] == [
        "broken_handoff_candidate",
        "conflict_pair",
        "conflict_pair",
    ]
    assert report.summary_by_owner["delta"].broken_handoffs == 1
    assert report.summary_by_owner["eta"].conflict_pairs == 1
    assert report.summary_by_owner["zeta"].conflict_pairs == 1


def test_missing_reliability_store_reports_error(tmp_path: Path) -> None:
    missing = tmp_path / "missing.db"

    try:
        run_reliability_report(missing)
    except ValueError as exc:
        assert "missing event store" in str(exc)
    else:
        raise AssertionError("missing reliability store was accepted")
