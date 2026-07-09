# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — workflow driver live-loop regressions

from __future__ import annotations

from collections.abc import Mapping, Sequence

from synapse_channel.core.ledger import TERMINAL_LEDGER_STATUSES
from synapse_channel.core.workflow import CompiledTask
from synapse_channel.core.workflow_run import BoardSnapshot, run_workflow

_BUILD = CompiledTask(
    task_id="w/build", title="Build", description="", depends_on=(), task_class="ci"
)
_TEST = CompiledTask(
    task_id="w/test", title="Test", description="", depends_on=("w/build",), task_class=""
)
_AGENTS: Mapping[str, frozenset[str]] = {"a1": frozenset({"ci"}), "a2": frozenset()}


class _FakeGateway:
    """An in-memory board: assigned tasks optionally complete on the next reading."""

    def __init__(
        self,
        task_ids: Sequence[str],
        *,
        complete_assigned: bool = True,
        preset_owner: Mapping[str, str] | None = None,
        evidence_by_poll: Sequence[Mapping[str, Mapping[str, str]]] = (),
    ) -> None:
        self.status: dict[str, str] = {tid: "open" for tid in task_ids}
        self.owner: dict[str, str] = dict(preset_owner or {})
        self._complete_assigned = complete_assigned
        self._evidence_by_poll = tuple(evidence_by_poll)
        self._polls = 0
        self.posted: list[str] = []
        self.assigned: list[tuple[str, str]] = []
        self.cancelled: list[str] = []

    async def post_tasks(self, tasks: Sequence[CompiledTask]) -> None:
        self.posted = [task.task_id for task in tasks]
        for task in tasks:
            self.status.setdefault(task.task_id, "open")

    async def read_board(self) -> BoardSnapshot:
        evidence = (
            self._evidence_by_poll[min(self._polls, len(self._evidence_by_poll) - 1)]
            if self._evidence_by_poll
            else {}
        )
        self._polls += 1
        if self._complete_assigned:
            for task_id, owner in self.owner.items():
                if owner and self.status.get(task_id) not in TERMINAL_LEDGER_STATUSES:
                    self.status[task_id] = "done"
        return BoardSnapshot(
            status=dict(self.status), suggested_owner=dict(self.owner), evidence=evidence
        )

    async def assign(self, task_id: str, agent: str) -> None:
        self.owner[task_id] = agent
        self.assigned.append((task_id, agent))

    async def cancel(self, task_id: str) -> None:
        self.status[task_id] = "cancelled"
        self.cancelled.append(task_id)


class _Clock:
    """A virtual clock that only advances when the loop sleeps."""

    def __init__(self) -> None:
        self.now = 0.0

    def time(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds


async def test_run_drives_a_two_step_workflow_to_completion() -> None:
    gateway = _FakeGateway(["w/build", "w/test"])
    clock = _Clock()
    result = await run_workflow(
        [_BUILD, _TEST],
        _AGENTS,
        gateway,
        max_in_flight=4,
        deadline=100.0,
        clock=clock.time,
        sleep=clock.sleep,
        poll_interval=1.0,
    )
    assert result.complete is True
    assert result.timed_out is False
    assert gateway.posted == ["w/build", "w/test"]
    assert ("w/build", "a1") in result.assignments
    assert any(task_id == "w/test" for task_id, _ in result.assignments)
    assert result.state.done == ("w/build", "w/test")
    assert result.polls == 3


async def test_run_completes_immediately_for_an_empty_workflow() -> None:
    gateway = _FakeGateway([])
    clock = _Clock()
    result = await run_workflow(
        [],
        _AGENTS,
        gateway,
        max_in_flight=4,
        deadline=100.0,
        clock=clock.time,
        sleep=clock.sleep,
        poll_interval=1.0,
    )
    assert result.complete is True
    assert result.polls == 1
    assert result.assignments == ()
    assert result.to_dict() == {
        "complete": True,
        "timed_out": False,
        "polls": 1,
        "assignments": [],
        "cancellations": [],
        "state": {
            "done": [],
            "in_flight": [],
            "ready": [],
            "blocked": [],
            "evidence_blocked": [],
            "skipped": [],
        },
    }


async def test_run_does_not_reassign_a_task_that_already_advises_the_owner() -> None:
    # build's planned owner (a1) is already advised, so the loop must skip re-assigning
    # it; with completion disabled the workflow then runs out the deadline.
    gateway = _FakeGateway(
        ["w/build", "w/test"], complete_assigned=False, preset_owner={"w/build": "a1"}
    )
    clock = _Clock()
    result = await run_workflow(
        [_BUILD, _TEST],
        _AGENTS,
        gateway,
        max_in_flight=4,
        deadline=2.0,
        clock=clock.time,
        sleep=clock.sleep,
        poll_interval=1.0,
    )
    assert result.complete is False
    assert result.timed_out is True
    assert gateway.assigned == []  # build skipped (already owned), test still blocked
    assert "w/build" in result.state.ready


async def test_run_waits_for_evidence_before_assigning() -> None:
    release = CompiledTask(
        task_id="w/release",
        title="Release",
        description="",
        depends_on=(),
        task_class="",
        evidence_requirements=(("policy", "pass"),),
    )
    gateway = _FakeGateway(
        ["w/release"],
        evidence_by_poll=({}, {"w/release": {"policy": "pass"}}),
    )
    clock = _Clock()

    result = await run_workflow(
        [release],
        {"a1": frozenset[str]()},
        gateway,
        max_in_flight=1,
        deadline=100.0,
        clock=clock.time,
        sleep=clock.sleep,
        poll_interval=1.0,
    )

    assert result.complete is True
    assert result.assignments == (("w/release", "a1"),)
    assert result.polls == 3


_GATE = CompiledTask(task_id="w/gate", title="Gate", description="", depends_on=(), task_class="")
_DEPLOY = CompiledTask(
    task_id="w/deploy",
    title="Deploy",
    description="",
    depends_on=("w/gate",),
    task_class="",
    conditions=(("w/gate", "done"),),
)
_ROLLBACK = CompiledTask(
    task_id="w/rollback",
    title="Rollback",
    description="",
    depends_on=("w/gate",),
    task_class="",
    conditions=(("w/gate", "cancelled"),),
)


async def test_run_retires_the_branch_not_taken() -> None:
    # gate succeeds -> deploy runs, rollback can never fire and is cancelled on the board
    gateway = _FakeGateway(["w/gate", "w/deploy", "w/rollback"])
    clock = _Clock()
    result = await run_workflow(
        [_GATE, _DEPLOY, _ROLLBACK],
        {"a1": frozenset[str]()},
        gateway,
        max_in_flight=4,
        deadline=100.0,
        clock=clock.time,
        sleep=clock.sleep,
        poll_interval=1.0,
    )
    assert result.complete is True
    assert result.cancellations == ("w/rollback",)
    assert gateway.cancelled == ["w/rollback"]
    assert ("w/deploy", "a1") in result.assignments
    assert "w/rollback" not in [task_id for task_id, _ in result.assignments]


async def test_run_times_out_when_no_agent_can_take_the_ready_work() -> None:
    gateway = _FakeGateway(["w/build", "w/test"])
    clock = _Clock()
    result = await run_workflow(
        [_BUILD, _TEST],
        {},  # no agents -> nothing is ever assigned
        gateway,
        max_in_flight=4,
        deadline=3.0,
        clock=clock.time,
        sleep=clock.sleep,
        poll_interval=1.0,
    )
    assert result.complete is False
    assert result.timed_out is True
    assert result.assignments == ()
    assert result.polls >= 1
