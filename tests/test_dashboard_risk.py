# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — operator risk view regressions

from __future__ import annotations

from typing import Any

from synapse_channel.dashboard import DashboardSnapshot
from synapse_channel.dashboard_fleet import (
    FleetA2A,
    FleetAgents,
    FleetClaims,
    FleetTasks,
    FleetVisibility,
)
from synapse_channel.dashboard_risk import (
    AMBER,
    BLOCKED_TASK,
    BRANCH_CONFLICT,
    GREEN,
    RED,
    STALE_CLAIM,
    build_risk_view,
)
from synapse_channel.dashboard_risk_guidance import (
    MAX_GUIDANCE_TASKS,
    build_risk_guidance,
)
from synapse_channel.dashboard_task_graph import TaskDependencyGraph


def _fleet(
    *,
    stale_claims: list[dict[str, Any]] | None = None,
    branch_conflicts: list[dict[str, Any]] | None = None,
    blocked: list[dict[str, Any]] | None = None,
    ready: list[str] | None = None,
) -> FleetVisibility:
    stale = stale_claims or []
    return FleetVisibility(
        agents=FleetAgents(live=[], waiters=[], missing_waiters=[]),
        claims=FleetClaims(active=0, stale=len(stale), active_claims=[], stale_claims=stale),
        tasks=FleetTasks(ready=ready or [], blocked=blocked or []),
        task_graph=TaskDependencyGraph(nodes=[], edges=[], blocked=[], ready=ready or []),
        receipts=[],
        branch_conflicts=branch_conflicts or [],
        a2a=FleetA2A(source="none", total=0, push_configs=0, states={}),
        generated_at=0.0,
    )


def test_quiet_fleet_is_green_with_no_signals() -> None:
    view = build_risk_view(_fleet(ready=["T2", "T1"]))
    assert view.level == GREEN
    assert view.signals == []
    assert view.safe_next_work == ["T2", "T1"]
    assert view.counts() == {RED: 0, AMBER: 0, "safe_next_work": 2}


def test_stale_claim_is_a_red_signal() -> None:
    view = build_risk_view(
        _fleet(stale_claims=[{"task_id": "T1", "owner": "alpha", "paths": ["src/a.py"]}])
    )
    assert view.level == RED
    signal = view.signals[0]
    assert signal.category == STALE_CLAIM
    assert signal.subject == "T1"
    assert "alpha" in signal.detail and "src/a.py" in signal.detail


def test_stale_claim_falls_back_when_fields_are_empty() -> None:
    view = build_risk_view(_fleet(stale_claims=[{}]))
    signal = view.signals[0]
    assert signal.subject == "(unnamed)"
    assert "an agent" in signal.detail and "its claimed scope" in signal.detail


def test_branch_conflict_is_a_red_signal() -> None:
    view = build_risk_view(
        _fleet(
            branch_conflicts=[
                {"owner_a": "alpha", "owner_b": "beta", "description": "both touch src/x.py"}
            ]
        )
    )
    assert view.level == RED
    signal = view.signals[0]
    assert signal.category == BRANCH_CONFLICT
    assert signal.subject == "alpha vs beta"
    assert signal.detail == "both touch src/x.py"


def test_branch_conflict_falls_back_when_fields_are_empty() -> None:
    signal = build_risk_view(_fleet(branch_conflicts=[{}])).signals[0]
    assert signal.subject == "agent A vs agent B"
    assert "may collide" in signal.detail


def test_blocked_task_is_an_amber_signal() -> None:
    view = build_risk_view(_fleet(blocked=[{"task_id": "T3", "blocked_by": ["T1", "T2"]}]))
    assert view.level == AMBER
    signal = view.signals[0]
    assert signal.category == BLOCKED_TASK
    assert signal.subject == "T3"
    assert "T1, T2" in signal.detail


def test_blocked_task_falls_back_when_fields_are_empty() -> None:
    signal = build_risk_view(_fleet(blocked=[{}])).signals[0]
    assert signal.subject == "(unnamed)"
    assert "unmet dependencies" in signal.detail


def test_red_outranks_amber_and_signals_are_ordered_worst_first() -> None:
    view = build_risk_view(
        _fleet(
            stale_claims=[{"task_id": "T9", "owner": "z"}],
            blocked=[{"task_id": "T1", "blocked_by": ["T0"]}],
            ready=["R1"],
        )
    )
    assert view.level == RED
    assert [signal.level for signal in view.signals] == [RED, AMBER]
    assert view.counts() == {RED: 1, AMBER: 1, "safe_next_work": 1}


def test_to_dict_round_trips_the_view() -> None:
    fleet = _fleet(blocked=[{"task_id": "T1", "blocked_by": ["T0"]}], ready=["R1"])
    payload = build_risk_view(fleet).to_dict()
    assert payload["level"] == AMBER
    assert payload["safe_next_work"] == ["R1"]
    assert payload["counts"]["amber"] == 1
    assert payload["signals"][0]["category"] == BLOCKED_TASK


def test_snapshot_to_dict_carries_the_risk_view() -> None:
    # A lease that expired in the deep past is always stale, so the integrated
    # snapshot must surface a red risk verdict and the ready queue as safe work.
    snapshot = DashboardSnapshot(
        online_agents=[],
        state={
            "active_claims": [
                {"task_id": "STALE", "owner": "alpha", "lease_expires_at": 1.0, "paths": ["a.py"]}
            ]
        },
        board={
            "tasks": [{"task_id": "R", "status": "open", "depends_on": []}],
            "ready": ["R"],
        },
        manifest=[],
    )
    risk = snapshot.to_dict()["risk"]
    assert risk["level"] == RED
    assert risk["safe_next_work"] == ["R"]
    assert risk["signals"][0]["category"] == STALE_CLAIM
    assert risk["guidance"]["tasks"][0]["task_id"] == "R"
    assert risk["guidance"]["tasks"][0]["route_fallback"] == (
        "no agent capability cards are available"
    )


def test_risk_guidance_ranks_routes_resources_and_links_postmortems() -> None:
    guidance = build_risk_guidance(
        board={
            "tasks": [
                {
                    "task_id": "GPU render/1",
                    "title": "render gpu frames",
                    "description": "use the available accelerator",
                }
            ]
        },
        manifest=[
            {
                "agent": "render-seat",
                "task_classes": ["render"],
                "skills": ["gpu"],
                "description": "GPU rendering",
                "contracts": [{"name": "render"}],
            }
        ],
        state={
            "resources": [
                {
                    "agent": "render-seat",
                    "kind": "gpu",
                    "name": "A100",
                    "capacity": 2,
                }
            ]
        },
        safe_task_ids=["GPU render/1"],
    ).to_dict()

    task = guidance["tasks"][0]
    assert task["route_candidates"][0]["agent"] == "render-seat"
    assert task["route_candidates"][0]["score"] > 0
    bid = task["resource_bids"][0]
    assert (bid["resource_kind"], bid["resource_name"], bid["agent"]) == (
        "gpu",
        "A100",
        "render-seat",
    )
    assert bid["capacity"] == 2
    assert bid["trust"] == "advisory-only"
    assert task["postmortem_href"] == "/postmortem.json?task=GPU+render%2F1"
    assert "do not claim tasks" in guidance["trust_boundary"]


def test_risk_guidance_is_bounded_deduplicated_and_fail_visible() -> None:
    task_ids = [f"T{index}" for index in range(MAX_GUIDANCE_TASKS + 2)]
    guidance = build_risk_guidance(
        board={"tasks": []},
        manifest=[],
        state={"resources": {"malformed": True}},
        safe_task_ids=[*task_ids, task_ids[0], ""],
    ).to_dict()

    assert guidance["task_count"] == MAX_GUIDANCE_TASKS
    assert guidance["omitted_tasks"] == 2
    assert [task["task_id"] for task in guidance["tasks"]] == task_ids[:MAX_GUIDANCE_TASKS]
    assert guidance["tasks"][0]["route_fallback"] == (
        "ready task is absent from the board snapshot"
    )
    assert guidance["tasks"][0]["resource_bids"] == []
