# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — workflow driver planning regressions

from __future__ import annotations

from synapse_channel.core.workflow import CompiledTask
from synapse_channel.core.workflow_driver import (
    Assignment,
    derive_state,
    plan_assignments,
)


def _task(
    task_id: str,
    *,
    depends_on: tuple[str, ...] = (),
    task_class: str = "",
    conditions: tuple[tuple[str, str], ...] = (),
    evidence_requirements: tuple[tuple[str, str], ...] = (),
) -> CompiledTask:
    return CompiledTask(
        task_id=task_id,
        title=task_id,
        description="",
        depends_on=depends_on,
        task_class=task_class,
        conditions=conditions,
        evidence_requirements=evidence_requirements,
    )


_CHAIN = (
    _task("w/a", task_class="ci"),
    _task("w/b", depends_on=("w/a",), task_class="ci"),
    _task("w/c", depends_on=("w/b",)),
)


# ---------- derive_state ----------


def test_derive_state_buckets_by_phase() -> None:
    state = derive_state(_CHAIN, {"w/a": "done", "w/b": "in_progress"})
    assert state.done == ("w/a",)
    assert state.in_flight == ("w/b",)
    assert state.blocked == ("w/c",)  # w/c waits on w/b which is not terminal
    assert state.ready == ()


def test_derive_state_readiness_recomputed_from_dependencies() -> None:
    # nothing reported: only the dependency-free task is ready
    state = derive_state(_CHAIN, {})
    assert state.ready == ("w/a",)
    assert state.blocked == ("w/b", "w/c")


def test_derive_state_cancelled_counts_as_done_and_unblocks() -> None:
    state = derive_state(_CHAIN, {"w/a": "cancelled"})
    assert state.done == ("w/a",)
    assert state.ready == ("w/b",)


def test_complete_is_true_only_when_all_terminal() -> None:
    assert derive_state(_CHAIN, {"w/a": "done", "w/b": "done", "w/c": "done"}).complete is True
    assert derive_state(_CHAIN, {"w/a": "done"}).complete is False


# ---------- conditional edges ----------

# deploy runs only if test is done; rollback only if test is cancelled.
_BRANCH = (
    _task("w/test"),
    _task("w/deploy", depends_on=("w/test",), conditions=(("w/test", "done"),)),
    _task("w/rollback", depends_on=("w/test",), conditions=(("w/test", "cancelled"),)),
)


def test_conditional_edge_ready_on_matching_outcome() -> None:
    state = derive_state(_BRANCH, {"w/test": "done"})
    assert state.ready == ("w/deploy",)  # the success branch is ready
    assert state.skipped == ("w/rollback",)  # the failure branch can never fire


def test_conditional_edge_takes_the_failure_branch_when_cancelled() -> None:
    state = derive_state(_BRANCH, {"w/test": "cancelled"})
    assert state.ready == ("w/rollback",)
    assert state.skipped == ("w/deploy",)


def test_conditional_edge_is_blocked_while_the_dependency_is_pending() -> None:
    state = derive_state(_BRANCH, {"w/test": "in_progress"})
    assert state.ready == ()
    assert state.skipped == ()
    assert state.blocked == ("w/deploy", "w/rollback")


def test_skipped_tasks_keep_the_workflow_incomplete_until_retired() -> None:
    # test done -> rollback is skipped (not yet terminal on the board) -> not complete
    state = derive_state(_BRANCH, {"w/test": "done", "w/deploy": "done"})
    assert state.skipped == ("w/rollback",)
    assert state.complete is False
    # once the skipped branch is cancelled on the board, the workflow completes
    done = derive_state(_BRANCH, {"w/test": "done", "w/deploy": "done", "w/rollback": "cancelled"})
    assert done.complete is True


def test_state_to_dict_round_trips() -> None:
    payload = derive_state(_CHAIN, {"w/a": "done"}).to_dict()
    assert payload == {
        "done": ["w/a"],
        "in_flight": [],
        "ready": ["w/b"],
        "blocked": ["w/c"],
        "evidence_blocked": [],
        "skipped": [],
    }


def test_evidence_requirement_blocks_ready_task_until_it_matches() -> None:
    task = _task("w/release", evidence_requirements=(("policy", "pass"),))

    missing = derive_state((task,), {})
    mismatch = derive_state((task,), {}, evidence={"w/release": {"policy": "fail"}})
    satisfied = derive_state((task,), {}, evidence={"w/release": {"policy": "pass"}})

    assert missing.evidence_blocked == ("w/release",)
    assert mismatch.evidence_blocked == ("w/release",)
    assert satisfied.ready == ("w/release",)


def test_evidence_requirement_waits_until_dependencies_are_satisfied_first() -> None:
    task = _task(
        "w/release",
        depends_on=("w/test",),
        evidence_requirements=(("receipt", "verified"),),
    )
    state = derive_state((task,), {"w/test": "open"}, evidence={"w/release": {"receipt": "bad"}})

    assert state.blocked == ("w/release",)
    assert state.evidence_blocked == ()


# ---------- plan_assignments ----------


def test_plan_routes_ready_tasks_to_capable_agents() -> None:
    plan = plan_assignments(
        _CHAIN,
        {"w/a": "done"},
        {"alpha": frozenset({"ci"}), "beta": frozenset({"docs"})},
        max_in_flight=4,
    )
    # only w/b is ready; alpha advertises ci, beta does not
    assert plan == (Assignment(task_id="w/b", agent="alpha", task_class="ci"),)


def test_plan_respects_the_in_flight_budget() -> None:
    tasks = (_task("w/a"), _task("w/b"), _task("w/c"))  # three independent ready tasks
    agents: dict[str, frozenset[str]] = {"a1": frozenset(), "a2": frozenset(), "a3": frozenset()}
    plan = plan_assignments(tasks, {}, agents, max_in_flight=2)
    assert len(plan) == 2  # budget caps it at two


def test_plan_subtracts_current_in_flight_from_budget() -> None:
    tasks = (_task("w/a"), _task("w/b"))
    plan = plan_assignments(tasks, {"w/a": "in_progress"}, {"a1": frozenset()}, max_in_flight=1)
    assert plan == ()  # the one in-flight slot is already used


def test_plan_assigns_each_agent_at_most_one_task() -> None:
    tasks = (_task("w/a"), _task("w/b"))
    plan = plan_assignments(tasks, {}, {"solo": frozenset()}, max_in_flight=5)
    assert len(plan) == 1  # the single agent can only take one


def test_plan_skips_a_task_with_no_capable_agent() -> None:
    tasks = (_task("w/a", task_class="gpu"), _task("w/b"))
    plan = plan_assignments(tasks, {}, {"cpu": frozenset({"cpu"})}, max_in_flight=5)
    # w/a needs gpu (no agent), w/b is unclassified so cpu takes it
    assert plan == (Assignment(task_id="w/b", agent="cpu", task_class=""),)


def test_plan_does_not_assign_evidence_blocked_tasks() -> None:
    tasks = (_task("w/release", evidence_requirements=(("approval", "owner"),)),)

    blocked = plan_assignments(tasks, {}, {"a1": frozenset()}, max_in_flight=1)
    ready = plan_assignments(
        tasks,
        {},
        {"a1": frozenset()},
        max_in_flight=1,
        evidence={"w/release": {"approval": "owner"}},
    )

    assert blocked == ()
    assert ready == (Assignment("w/release", "a1", ""),)


def test_assignment_to_dict() -> None:
    assert Assignment("w/a", "alpha", "ci").to_dict() == {
        "task_id": "w/a",
        "agent": "alpha",
        "task_class": "ci",
    }
