# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for deterministic ready-task dispatch selection

from __future__ import annotations

from typing import Any

from synapse_channel.core.ready_dispatch import DispatchPlan, plan_dispatches

PROJECT = "SYNAPSE-CHANNEL"


def _task(
    task_id: str,
    *,
    project: str = PROJECT,
    suggested_owner: str = "",
    updated_at: float = 1_000.0,
    status: str = "open",
    version: int = 3,
) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "title": task_id,
        "status": status,
        "project": project,
        "suggested_owner": suggested_owner,
        "updated_at": updated_at,
        "version": version,
    }


def _card(
    agent: str,
    *,
    task_classes: list[str] | None = None,
    skills: list[str] | None = None,
    dispatchable: object = None,
    advertised_at: float = 500.0,
) -> dict[str, Any]:
    card: dict[str, Any] = {
        "agent": agent,
        "task_classes": task_classes or [],
        "skills": skills or [],
        "advertised_at": advertised_at,
    }
    if dispatchable is not None:
        card["dispatchable"] = dispatchable
    return card


def _plan(**overrides: Any) -> DispatchPlan:
    base: dict[str, Any] = {
        "tasks": [_task("T-1")],
        "ready_ids": frozenset({"T-1"}),
        "claims": [],
        "cards": [_card(f"{PROJECT}/kimi-3dcd")],
        "online": frozenset({f"{PROJECT}/kimi-3dcd-rx"}),
        "wake_capabilities": {f"{PROJECT}/kimi-3dcd-rx": "pane_bridge"},
        "project": PROJECT,
        "now": 10_000.0,
    }
    base.update(overrides)
    return plan_dispatches(**base)


def test_ready_task_assigned_to_online_project_seat() -> None:
    plan = _plan()
    assert [a.owner for a in plan.assignments] == [f"{PROJECT}/kimi-3dcd"]
    assignment = plan.assignments[0]
    assert assignment.wake_identity == f"{PROJECT}/kimi-3dcd-rx"
    assert plan.trust_boundary


def test_unscoped_task_is_never_dispatched() -> None:
    plan = _plan(tasks=[_task("T-1", project="")])
    assert plan.assignments == ()
    assert "unscoped" in plan.skipped["T-1"][0]


def test_foreign_project_task_is_skipped() -> None:
    plan = _plan(tasks=[_task("T-1", project="OTHER")])
    assert plan.assignments == ()
    assert "OTHER" in plan.skipped["T-1"][0]


def test_non_ready_and_non_open_tasks_are_ignored() -> None:
    plan = _plan(tasks=[_task("T-1"), _task("T-2", status="done")], ready_ids=frozenset({"T-2"}))
    assert plan.assignments == ()


def test_claimed_task_is_skipped() -> None:
    plan = _plan(claims=[{"task_id": "T-1", "owner": f"{PROJECT}/codex-23696"}])
    assert plan.assignments == ()
    assert "claim" in plan.skipped["T-1"][0]


def test_fresh_suggestion_is_not_rewoken() -> None:
    plan = _plan(tasks=[_task("T-1", suggested_owner=f"{PROJECT}/kimi-3dcd", updated_at=9_800.0)])
    assert plan.assignments == ()
    assert "fresh suggestion" in plan.skipped["T-1"][0]


def test_stale_suggestion_reopens_and_rewakes() -> None:
    plan = _plan(tasks=[_task("T-1", suggested_owner="PROJ/offline", updated_at=1_000.0)])
    assert [a.owner for a in plan.assignments] == [f"{PROJECT}/kimi-3dcd"]
    assert "stale suggestion" in plan.assignments[0].reasons[-1]


def test_dispatchable_false_card_is_never_assigned() -> None:
    plan = _plan(cards=[_card(f"{PROJECT}/kimi-3dcd", dispatchable=False)])
    assert plan.assignments == ()
    assert "no dispatchable candidate" in plan.skipped["T-1"][0]


def test_offline_seat_and_missing_wake_capability_are_skipped() -> None:
    plan = _plan(online=frozenset(), wake_capabilities={})
    assert plan.assignments == ()
    plan2 = _plan(wake_capabilities={f"{PROJECT}/kimi-3dcd-rx": "passive"})
    assert plan2.assignments == ()


def test_sidecar_rx_identity_wakes_the_seat() -> None:
    plan = _plan(online=frozenset({f"{PROJECT}/kimi-3dcd-rx"}))
    assert plan.assignments[0].wake_identity == f"{PROJECT}/kimi-3dcd-rx"


def test_pane_bridge_outranks_direct() -> None:
    cards = [_card(f"{PROJECT}/direct-1"), _card(f"{PROJECT}/bridge-1")]
    plan = _plan(
        cards=cards,
        online=frozenset({f"{PROJECT}/direct-1", f"{PROJECT}/bridge-1"}),
        wake_capabilities={f"{PROJECT}/direct-1": "direct", f"{PROJECT}/bridge-1": "pane_bridge"},
    )
    assert plan.assignments[0].owner == f"{PROJECT}/bridge-1"


def test_class_hint_match_wins_over_wake_rank() -> None:
    cards = [
        _card(f"{PROJECT}/bridge-1", task_classes=["docs"]),
        _card(f"{PROJECT}/direct-1", task_classes=["audit"]),
    ]
    plan = _plan(
        tasks=[_task("SEC-AUDIT-7")],
        ready_ids=frozenset({"SEC-AUDIT-7"}),
        cards=cards,
        online=frozenset({f"{PROJECT}/direct-1", f"{PROJECT}/bridge-1"}),
        wake_capabilities={f"{PROJECT}/direct-1": "direct", f"{PROJECT}/bridge-1": "pane_bridge"},
    )
    assert plan.assignments[0].owner == f"{PROJECT}/direct-1"
    assert plan.assignments[0].class_score == 1


def test_capacity_limits_assignments_per_pass() -> None:
    tasks = [_task("T-1"), _task("T-2")]
    plan = _plan(tasks=tasks, ready_ids=frozenset({"T-1", "T-2"}), capacity=1)
    assert len(plan.assignments) == 1
    assert plan.assignments[0].task_id == "T-1"
    assert "no dispatchable candidate" in plan.skipped["T-2"][0]


def test_active_claims_reduce_remaining_capacity() -> None:
    plan = _plan(claims=[{"task_id": "OTHER-T", "owner": f"{PROJECT}/kimi-3dcd"}])
    assert plan.assignments == ()


def test_higher_capacity_allows_multiple_assignments() -> None:
    tasks = [_task("T-1"), _task("T-2")]
    plan = _plan(tasks=tasks, ready_ids=frozenset({"T-1", "T-2"}), capacity=2)
    assert [a.task_id for a in plan.assignments] == ["T-1", "T-2"]


def test_idle_rank_prefers_older_card_then_lexicographic() -> None:
    seats = [f"{PROJECT}/seat-a", f"{PROJECT}/seat-b", f"{PROJECT}/seat-c"]
    cards = [
        _card(f"{PROJECT}/seat-b", advertised_at=900.0),
        _card(f"{PROJECT}/seat-a", advertised_at=100.0),
        _card(f"{PROJECT}/seat-c", advertised_at=900.0),
    ]
    base = {
        "online": frozenset(seats),
        "wake_capabilities": {seat: "direct" for seat in seats},
    }
    plan = _plan(cards=cards, **base)
    assert plan.assignments[0].owner == f"{PROJECT}/seat-a"
    tied = _plan(cards=cards[:1] + cards[2:], **base)
    assert tied.assignments[0].owner == f"{PROJECT}/seat-b"


def test_plan_is_deterministic_under_input_shuffling() -> None:
    kwargs: dict[str, Any] = {
        "tasks": [_task("T-3"), _task("T-1"), _task("T-2")],
        "ready_ids": frozenset({"T-1", "T-2", "T-3"}),
        "claims": [],
        "cards": [_card(f"{PROJECT}/seat-b"), _card(f"{PROJECT}/seat-a")],
        "online": frozenset({f"{PROJECT}/seat-a-rx", f"{PROJECT}/seat-b-rx"}),
        "wake_capabilities": {
            f"{PROJECT}/seat-a-rx": "pane_bridge",
            f"{PROJECT}/seat-b-rx": "pane_bridge",
        },
        "project": PROJECT,
        "now": 10_000.0,
        "capacity": 2,
    }
    first = plan_dispatches(**kwargs)
    kwargs["tasks"] = list(reversed(kwargs["tasks"]))
    kwargs["cards"] = list(reversed(kwargs["cards"]))
    second = plan_dispatches(**kwargs)
    assert [(a.task_id, a.owner) for a in first.assignments] == [
        (a.task_id, a.owner) for a in second.assignments
    ]


def test_malformed_rows_are_ignored_not_fatal() -> None:
    plan = _plan(
        tasks=[{"status": "open"}, _task("T-1")],
        cards=[{"agent": ""}, _card(f"{PROJECT}/kimi-3dcd")],
        claims=[{"task_id": "", "owner": ""}],
    )
    assert [a.task_id for a in plan.assignments] == ["T-1"]
