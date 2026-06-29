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


def _task(task_id: str, *, depends_on: tuple[str, ...] = (), task_class: str = "") -> CompiledTask:
    return CompiledTask(
        task_id=task_id,
        title=task_id,
        description="",
        depends_on=depends_on,
        task_class=task_class,
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


def test_state_to_dict_round_trips() -> None:
    payload = derive_state(_CHAIN, {"w/a": "done"}).to_dict()
    assert payload == {"done": ["w/a"], "in_flight": [], "ready": ["w/b"], "blocked": ["w/c"]}


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


def test_assignment_to_dict() -> None:
    assert Assignment("w/a", "alpha", "ci").to_dict() == {
        "task_id": "w/a",
        "agent": "alpha",
        "task_class": "ci",
    }
