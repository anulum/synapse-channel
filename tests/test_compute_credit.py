# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — compute-credit approval and eligibility tests
"""Exercise owner ledger and durable approval evidence through public projections."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from synapse_channel.core.approvals import (
    APPROVAL_NOTE_KIND,
    format_approval_note,
    run_approval_report,
)
from synapse_channel.core.compute_credit import (
    ComputeCreditError,
    approval_subject,
    suggest_compute_work,
    validate_compute_task,
)
from synapse_channel.core.entitlement_store import append_event, read_events
from synapse_channel.core.journal import EventKind
from synapse_channel.core.ledger import Blackboard
from synapse_channel.core.persistence import EventStore

AS_OF = datetime(2026, 9, 23, 12, tzinfo=timezone.utc)


def _event(kind: str, event_id: str, **fields: object) -> dict[str, object]:
    return {
        "event_id": event_id,
        "kind": kind,
        "recorded_at": "2026-09-23T10:00:00Z",
        "source": "operator:owner",
        "confidence": "operator",
        **fields,
    }


def _ledger(path: Path) -> None:
    for event in (
        _event("account", "a1", account_id="a", label="Private GPU", status="active"),
        _event(
            "pool",
            "p1",
            pool_id="p",
            account_id="a",
            unit="gpu_seconds",
            resource_kind="gpu_time",
            capabilities=["cuda"],
            data_classes=["internal"],
            eligible_projects=["SYNAPSE-CHANNEL"],
            idle_cost={"amount_per_hour": "0.4", "currency": "USD"},
        ),
        _event(
            "window",
            "w1",
            window_id="w",
            pool_id="p",
            starts_at="2026-09-22T00:00:00Z",
            ends_at="2026-09-24T00:00:00Z",
            grant="100",
            unit="gpu_seconds",
            price_revision="r1",
        ),
        _event(
            "balance",
            "b1",
            window_id="w",
            window_event_id="w1",
            source_event_id="balance-1",
            remaining="80",
            observed_at="2026-09-23T11:00:00Z",
        ),
        _event(
            "usage",
            "u1",
            window_id="w",
            window_event_id="w1",
            source_event_id="job-1",
            amount="10",
            observed_at="2026-09-23T11:30:00Z",
        ),
    ):
        assert append_event(path, event)


def _task() -> dict[str, object]:
    return {
        "task_id": "TASK-1",
        "project": "SYNAPSE-CHANNEL",
        "resource_kind": "gpu_time",
        "capability": "cuda",
        "data_class": "internal",
        "unit": "gpu_seconds",
        "required_amount": "30",
        "estimated_total_cost": "2",
        "max_total_cost": "3",
        "cost_currency": "USD",
        "price_revision": "r1",
        "priority": 1,
        "board_version": 1,
        "authorisation_expires_at": "2026-09-24T00:00:00Z",
    }


def _board(*, project: str = "SYNAPSE-CHANNEL") -> Blackboard:
    board = Blackboard()
    accepted, _ = board.post_task(
        task_id="TASK-1",
        title="Run useful compute work",
        author="operator",
        project=project,
        now=AS_OF.timestamp() - 3600,
    )
    assert accepted
    return board


def _approve(path: Path, task: dict[str, object], *, author: str = "CEO/claude") -> None:
    subject = approval_subject(task)
    store = EventStore(path)
    for index, (state, actor) in enumerate(
        (("requested", "SYNAPSE-CHANNEL/codex"), ("approved", author))
    ):
        store.append(
            EventKind.LEDGER_PROGRESS,
            {
                "author": actor,
                "kind": APPROVAL_NOTE_KIND,
                "task_id": subject,
                "text": format_approval_note(subject=subject, state=state),
            },
            ts=AS_OF.timestamp() - 20 + index * 10,
            durable=True,
        )
    store.close()


def test_approved_work_uses_partial_balance_and_keeps_idle_cost_separate(tmp_path: Path) -> None:
    ledger = tmp_path / "private" / "ledger.sqlite3"
    hub = tmp_path / "hub.db"
    _ledger(ledger)
    task = _task()
    _approve(hub, task)
    report = suggest_compute_work(
        read_events(ledger),
        [task],
        run_approval_report(hub),
        _board(),
        reviewer="CEO/claude",
        as_of=AS_OF,
    )
    assert report["authority"] == "advisory_only"
    assert report["no_job_launched"] is True
    options = report["suggestions"][0]["options"]
    assert options[0]["remaining"] == "70"
    assert options[0]["unit"] == "gpu_seconds"
    assert options[0]["idle_cost"] == {"amount_per_hour": "0.4", "currency": "USD"}
    assert report["excluded"] == []


def test_restrictions_and_stale_facts_fail_closed(tmp_path: Path) -> None:
    ledger = tmp_path / "private" / "ledger.sqlite3"
    hub = tmp_path / "hub.db"
    _ledger(ledger)
    task = _task()
    _approve(hub, task)
    approvals = run_approval_report(hub)
    events = read_events(ledger)
    stale_account_events = [
        {**item, "recorded_at": "2026-09-22T10:00:00Z"} if item["kind"] == "account" else item
        for item in events
    ]
    stale = suggest_compute_work(
        stale_account_events,
        [task],
        approvals,
        _board(),
        reviewer="CEO/claude",
        as_of=AS_OF,
        max_evidence_age_seconds=1800,
    )
    assert stale["excluded"] == [{"task_id": "TASK-1", "reason": "no_current_eligible_pool"}]
    fresh_account_stale_balance = [
        {**item, "recorded_at": "2026-09-23T11:45:00Z"} if item["kind"] == "account" else item
        for item in events
    ]
    assert (
        suggest_compute_work(
            fresh_account_stale_balance,
            [task],
            approvals,
            _board(),
            reviewer="CEO/claude",
            as_of=AS_OF,
            max_evidence_age_seconds=1800,
        )["suggestions"]
        == []
    )
    wrong_reviewer = suggest_compute_work(
        events,
        [task],
        approvals,
        _board(),
        reviewer="SYNAPSE-CHANNEL/codex",
        as_of=AS_OF,
    )
    assert wrong_reviewer["suggestions"] == []
    for changed in (
        {**task, "project": "OTHER"},
        {**task, "data_class": "restricted"},
        {**task, "capability": "rocm"},
        {**task, "required_amount": "90"},
        {**task, "price_revision": "r2"},
    ):
        _approve(hub, changed)
        assert suggest_compute_work(
            events,
            [changed],
            run_approval_report(hub),
            _board(project=str(changed["project"])),
            reviewer="CEO/claude",
            as_of=AS_OF,
        )["excluded"] == [{"task_id": "TASK-1", "reason": "no_current_eligible_pool"}]
    tampered = {**task, "max_total_cost": "4"}
    assert suggest_compute_work(
        events,
        [tampered],
        run_approval_report(hub),
        _board(),
        reviewer="CEO/claude",
        as_of=AS_OF,
    )["excluded"] == [{"task_id": "TASK-1", "reason": "missing_current_exact_approval"}]
    assert append_event(
        ledger,
        {
            **events[0],
            "event_id": "a2",
            "recorded_at": "2026-09-23T11:00:00Z",
            "status": "suspended",
            "supersedes": "a1",
        },
    )
    suspended = suggest_compute_work(
        read_events(ledger),
        [task],
        approvals,
        _board(),
        reviewer="CEO/claude",
        as_of=AS_OF,
    )
    assert suspended["suggestions"] == []


def test_expired_grant_and_authorisation_are_separate(tmp_path: Path) -> None:
    ledger = tmp_path / "private" / "ledger.sqlite3"
    hub = tmp_path / "hub.db"
    _ledger(ledger)
    task = {**_task(), "authorisation_expires_at": "2026-09-25T00:00:00Z"}
    _approve(hub, task)
    after_grant = datetime(2026, 9, 24, 1, tzinfo=timezone.utc)
    result = suggest_compute_work(
        read_events(ledger),
        [task],
        run_approval_report(hub),
        _board(),
        reviewer="CEO/claude",
        as_of=after_grant,
    )
    assert result["excluded"] == [{"task_id": "TASK-1", "reason": "no_current_eligible_pool"}]
    expired_task = _task()
    _approve(hub, expired_task)
    expired = suggest_compute_work(
        read_events(ledger),
        [expired_task],
        run_approval_report(hub),
        _board(),
        reviewer="CEO/claude",
        as_of=after_grant,
    )
    assert expired["excluded"] == [
        {"task_id": "TASK-1", "reason": "missing_current_exact_approval"}
    ]


def test_latest_rejection_withdraws_approved_work(tmp_path: Path) -> None:
    ledger = tmp_path / "private" / "ledger.sqlite3"
    hub = tmp_path / "hub.db"
    _ledger(ledger)
    task = _task()
    _approve(hub, task)
    store = EventStore(hub)
    subject = approval_subject(task)
    store.append(
        EventKind.LEDGER_PROGRESS,
        {
            "author": "CEO/claude",
            "kind": APPROVAL_NOTE_KIND,
            "task_id": subject,
            "text": format_approval_note(subject=subject, state="rejected"),
        },
        durable=True,
    )
    store.close()
    result = suggest_compute_work(
        read_events(ledger),
        [task],
        run_approval_report(hub),
        _board(),
        reviewer="CEO/claude",
        as_of=AS_OF,
    )
    assert result["suggestions"] == []
    assert result["excluded"] == [{"task_id": "TASK-1", "reason": "missing_current_exact_approval"}]


def test_board_absence_version_change_and_dependency_block_suggestions(tmp_path: Path) -> None:
    ledger = tmp_path / "private" / "ledger.sqlite3"
    hub = tmp_path / "hub.db"
    _ledger(ledger)
    task = _task()
    _approve(hub, task)
    events = read_events(ledger)
    approvals = run_approval_report(hub)
    missing = suggest_compute_work(
        events,
        [task],
        approvals,
        Blackboard(),
        reviewer="CEO/claude",
        as_of=AS_OF,
    )
    assert missing["excluded"] == [{"task_id": "TASK-1", "reason": "no_matching_ready_board_task"}]
    board = _board()
    accepted, _ = board.post_task(
        task_id="TASK-1",
        title="Changed scope",
        author="operator",
        project="SYNAPSE-CHANNEL",
        now=AS_OF.timestamp(),
    )
    assert accepted
    stale_version = suggest_compute_work(
        events,
        [task],
        approvals,
        board,
        reviewer="CEO/claude",
        as_of=AS_OF,
    )
    assert stale_version["excluded"][0]["reason"] == "no_matching_ready_board_task"
    blocked = Blackboard()
    accepted, _ = blocked.post_task(
        task_id="TASK-1",
        title="Waiting for prerequisite",
        author="operator",
        project="SYNAPSE-CHANNEL",
        depends_on=("MISSING",),
        now=AS_OF.timestamp(),
    )
    assert accepted
    waiting = suggest_compute_work(
        events,
        [task],
        approvals,
        blocked,
        reviewer="CEO/claude",
        as_of=AS_OF,
    )
    assert waiting["suggestions"] == []


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"unit": "ci_minutes"}, "incompatible"),
        ({"required_amount": "0"}, "positive"),
        ({"estimated_total_cost": "4"}, "exceeds"),
        ({"priority": True}, "priority"),
        ({"task_id": "bad id"}, "task_id"),
        ({"cost_currency": "BTC"}, "cost_currency"),
        ({"board_version": 0}, "board_version"),
        ({"unexpected": "x"}, "unexpected"),
    ],
)
def test_invalid_or_over_budget_work_is_refused(change: dict[str, object], reason: str) -> None:
    with pytest.raises(ComputeCreditError, match=reason):
        validate_compute_task({**_task(), **change})


def test_approval_subject_changes_with_cost_and_data_contract() -> None:
    task = _task()
    subject = approval_subject(task)
    assert subject.startswith("compute-credit:")
    assert approval_subject({**task, "estimated_total_cost": "2.5"}) != subject
    assert approval_subject({**task, "data_class": "restricted"}) != subject


def test_evaluation_input_refusals_are_explicit(tmp_path: Path) -> None:
    ledger = tmp_path / "private" / "ledger.sqlite3"
    hub = tmp_path / "hub.db"
    _ledger(ledger)
    task = _task()
    _approve(hub, task)
    events = read_events(ledger)
    approvals = run_approval_report(hub)
    board = _board()
    with pytest.raises(ComputeCreditError, match="UTC offset"):
        suggest_compute_work(
            events,
            [task],
            approvals,
            board,
            reviewer="CEO/claude",
            as_of=datetime(2026, 9, 23, 12),
        )
    with pytest.raises(ComputeCreditError, match="reviewer"):
        suggest_compute_work(
            events,
            [task],
            approvals,
            board,
            reviewer="bad reviewer",
            as_of=AS_OF,
        )
    with pytest.raises(ComputeCreditError, match="max evidence age"):
        suggest_compute_work(
            events,
            [task],
            approvals,
            board,
            reviewer="CEO/claude",
            as_of=AS_OF,
            max_evidence_age_seconds=0,
        )
    with pytest.raises(ComputeCreditError, match="duplicate task id"):
        suggest_compute_work(
            events,
            [task, task],
            approvals,
            board,
            reviewer="CEO/claude",
            as_of=AS_OF,
        )
