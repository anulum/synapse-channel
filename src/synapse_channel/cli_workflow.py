# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — declarative workflow CLI (validate and compile)
"""``synapse workflow`` — validate and compile a declarative workflow.

A workflow is a plain JSON artifact (a ``name`` and a list of ``steps`` with
``depends_on`` edges). ``validate`` parses and checks it; ``compile`` turns it
into the blackboard task declarations the board would execute, either as a human
summary or machine-readable JSON. Neither touches the hub — they are offline
authoring tools over :mod:`synapse_channel.core.workflow`. ``contention`` joins
them to the durable log: it runs the same offline yield-advice analysis as
``synapse causality contention`` and keeps only the overlapping live-claim pairs
a workflow task is party to.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from synapse_channel.client.agent import SynapseAgent, default_hub_uri
from synapse_channel.connect_failures import closed_after_ready, describe_connect_failure
from synapse_channel.core.causality import DEFAULT_MAX_GRAPH_NODES
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.workflow import (
    CompiledTask,
    WorkflowError,
    compile_to_tasks,
    parse_workflow,
)
from synapse_channel.core.workflow_driver import (
    DEFAULT_STATUS,
    derive_state,
    plan_assignments,
)
from synapse_channel.core.workflow_run import BoardSnapshot, RunResult, run_workflow
from synapse_channel.core.yield_advice import (
    advice_involving,
    advice_to_json,
    render_advice_markdown,
    run_yield_advice,
)
from synapse_channel.terminal_text import terminal_text

AgentFactory = Callable[..., SynapseAgent]
"""Factory for the client agent; injectable so the driver is testable."""

_READY_TIMEOUT = 5.0
"""Seconds to wait for the hub welcome before treating the hub as unreachable."""


def _load_workflow_file(path: str) -> object:
    """Read and JSON-decode a workflow file, raising :class:`WorkflowError`."""
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        msg = f"could not read workflow file: {path}"
        raise WorkflowError(msg) from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        msg = f"workflow file is not valid JSON: {exc}"
        raise WorkflowError(msg) from exc


def _load_status(path: str | None) -> dict[str, str]:
    """Load a ``{task_id: status}`` map, or an empty map when no file is given."""
    if not path:
        return {}
    data = _load_workflow_file(path)
    if not isinstance(data, dict):
        msg = "status file must be a JSON object of task_id -> status"
        raise WorkflowError(msg)
    return {str(key): str(value) for key, value in data.items()}


def _load_agents(path: str | None) -> dict[str, frozenset[str]]:
    """Load an ``{agent: [task_classes]}`` map, or an empty map when no file is given."""
    if not path:
        return {}
    data = _load_workflow_file(path)
    if not isinstance(data, dict):
        msg = "agents file must be a JSON object of agent -> [task_classes]"
        raise WorkflowError(msg)
    agents: dict[str, frozenset[str]] = {}
    for agent, classes in data.items():
        if not isinstance(classes, (list, tuple)):
            msg = f"agent {agent!r} must map to a list of task classes"
            raise WorkflowError(msg)
        agents[str(agent)] = frozenset(str(item) for item in classes)
    return agents


def _load_evidence(path: str | None) -> dict[str, dict[str, str]]:
    """Load a ``{task_id: {predicate: value}}`` evidence snapshot."""
    if not path:
        return {}
    data = _load_workflow_file(path)
    if not isinstance(data, dict):
        msg = "evidence file must be a JSON object of task_id -> predicate map"
        raise WorkflowError(msg)
    evidence: dict[str, dict[str, str]] = {}
    for task_id, predicates in data.items():
        if not isinstance(predicates, dict):
            msg = f"evidence for task {task_id!r} must be a predicate map"
            raise WorkflowError(msg)
        evidence[str(task_id)] = {
            str(name).strip(): str(value).strip()
            for name, value in predicates.items()
            if str(name).strip()
        }
    return evidence


def _cmd_validate(args: argparse.Namespace) -> int:
    """Validate a workflow file and report the outcome."""
    try:
        workflow = parse_workflow(_load_workflow_file(args.file))
    except WorkflowError as exc:
        print(terminal_text(exc), file=sys.stderr)
        return 2
    print(f"workflow '{terminal_text(workflow.name)}' is valid: {len(workflow.steps)} steps")
    return 0


def _cmd_compile(args: argparse.Namespace) -> int:
    """Compile a workflow file into blackboard task declarations."""
    try:
        tasks = compile_to_tasks(parse_workflow(_load_workflow_file(args.file)))
    except WorkflowError as exc:
        print(terminal_text(exc), file=sys.stderr)
        return 2
    if args.json:
        payload = [
            {
                **task.declaration(),
                "task_class": task.task_class,
                "conditions": [{"dep": dep, "on": on} for dep, on in task.conditions],
                "requires": dict(task.evidence_requirements),
            }
            for task in tasks
        ]
        print(json.dumps(payload, indent=2))
        return 0
    print(f"{len(tasks)} blackboard tasks:")
    for task in tasks:
        edges = [
            f"{terminal_text(dep)}:{terminal_text(task.required_status(dep))}"
            if task.required_status(dep)
            else terminal_text(dep)
            for dep in task.depends_on
        ]
        deps = ", ".join(edges) if edges else "(none)"
        task_class = f" [{terminal_text(task.task_class)}]" if task.task_class else ""
        requires = (
            " requires "
            + ", ".join(
                f"{terminal_text(name)}={terminal_text(value)}"
                for name, value in task.evidence_requirements
            )
            if task.evidence_requirements
            else ""
        )
        print(f"  {terminal_text(task.task_id)}{task_class} <- {deps}{requires}")
    return 0


def _cmd_plan(args: argparse.Namespace) -> int:
    """Plan the next agent assignments for a workflow against a board snapshot."""
    try:
        tasks = compile_to_tasks(parse_workflow(_load_workflow_file(args.file)))
        status = _load_status(args.status)
        agents = _load_agents(args.agents)
        evidence = _load_evidence(args.evidence)
    except WorkflowError as exc:
        print(terminal_text(exc), file=sys.stderr)
        return 2
    state = derive_state(tasks, status, evidence=evidence)
    plan = plan_assignments(
        tasks,
        status,
        agents,
        max_in_flight=args.max_in_flight,
        evidence=evidence,
    )
    if args.json:
        print(json.dumps({"state": state.to_dict(), "plan": [a.to_dict() for a in plan]}, indent=2))
        return 0
    print(
        f"state: {len(state.done)} done, {len(state.in_flight)} in flight, "
        f"{len(state.ready)} ready, {len(state.blocked)} blocked, "
        f"{len(state.evidence_blocked)} waiting for evidence"
        + (" (complete)" if state.complete else "")
    )
    if not plan:
        print("no assignments")
        return 0
    print("assignments:")
    for assignment in plan:
        task_class = f" [{terminal_text(assignment.task_class)}]" if assignment.task_class else ""
        print(
            f"  {terminal_text(assignment.task_id)}{task_class} "
            f"-> {terminal_text(assignment.agent)}"
        )
    return 0


def _cmd_contention(args: argparse.Namespace) -> int:
    """Weigh overlapping live claims scoped to one workflow's tasks.

    Compiles the workflow to its task ids, runs the same offline yield-advice
    analysis as ``synapse causality contention``, and keeps only the pairs a
    workflow task is party to — whether it keeps or yields. Exit ``0`` when no
    live claim involving a workflow task overlaps, ``1`` when at least one pair
    does, ``2`` on an invalid workflow, a missing store, or the node ceiling.
    """
    try:
        tasks = compile_to_tasks(parse_workflow(_load_workflow_file(args.file)))
        recommendations = run_yield_advice(
            args.db,
            max_nodes=args.max_nodes,
            key_file=getattr(args, "db_key_file", None),
        )
    except (WorkflowError, ValueError) as exc:
        print(terminal_text(exc), file=sys.stderr)
        return 2
    scoped = advice_involving(recommendations, [task.task_id for task in tasks])
    if args.json:
        print(json.dumps(advice_to_json(scoped), indent=2, sort_keys=True))
        return 1 if scoped else 0
    if scoped:
        print(render_advice_markdown(scoped))
    else:
        print("No live claims involving this workflow's tasks overlap; nothing to weigh.")
    others = len(recommendations) - len(scoped)
    if others:
        print(f"(note: {others} other overlapping pair(s) do not involve this workflow)")
    return 1 if scoped else 0


def _snapshot_from_board(board: Mapping[str, Any]) -> BoardSnapshot:
    """Reduce a hub board snapshot to the status and owner maps the driver routes on."""
    status: dict[str, str] = {}
    suggested_owner: dict[str, str] = {}
    for task in board.get("tasks", []):
        task_id = str(task.get("task_id", "")).strip()
        if not task_id:
            continue
        status[task_id] = str(task.get("status") or DEFAULT_STATUS)
        owner = str(task.get("suggested_owner") or "")
        if owner:
            suggested_owner[task_id] = owner
    return BoardSnapshot(status=status, suggested_owner=suggested_owner)


class _AgentGateway:
    """A :class:`~synapse_channel.core.workflow_run.WorkflowGateway` over a live hub client.

    Wraps one connected :class:`SynapseAgent`: ``post_tasks`` declares each compiled
    task, ``read_board`` requests and awaits a fresh board snapshot, and ``assign``
    advises an owner. Board snapshots arrive on the agent's message callback and are
    appended to a shared ``boards`` buffer that ``read_board`` drains.
    """

    def __init__(
        self,
        agent: SynapseAgent,
        boards: list[Mapping[str, Any]],
        *,
        evidence_path: str | None = None,
        attempts: int = 80,
        poll: float = 0.05,
    ) -> None:
        self._agent = agent
        self._boards = boards
        self._evidence_path = evidence_path
        self._attempts = attempts
        self._poll = poll

    async def post_tasks(self, tasks: Sequence[CompiledTask]) -> None:
        """Declare every compiled task on the board, in dependency order."""
        for task in tasks:
            await self._agent.post_task(
                task.task_id,
                title=task.title,
                description=task.description,
                depends_on=task.depends_on,
            )

    async def read_board(self) -> BoardSnapshot:
        """Request a board snapshot and return the latest reading (empty if none arrives)."""
        self._boards.clear()
        await self._agent.request_board()
        for _ in range(self._attempts):
            if self._boards:
                break
            await asyncio.sleep(self._poll)
        board = self._boards[-1] if self._boards else {}
        snapshot = _snapshot_from_board(board)
        if not self._evidence_path:
            return snapshot
        evidence = _load_evidence(self._evidence_path)
        return BoardSnapshot(
            status=snapshot.status,
            suggested_owner=snapshot.suggested_owner,
            evidence=evidence,
        )

    async def assign(self, task_id: str, agent: str) -> None:
        """Advise ``agent`` as the owner of ``task_id`` on the board."""
        await self._agent.update_ledger_task(task_id, suggested_owner=agent)

    async def cancel(self, task_id: str) -> None:
        """Retire ``task_id`` on the board (a conditional branch that was not taken)."""
        await self._agent.update_ledger_task(task_id, status="cancelled")


def _render_run(result: RunResult, *, json_out: bool) -> None:
    """Print a driver run outcome, as JSON or readable lines."""
    if json_out:
        print(json.dumps(result.to_dict(), indent=2))
        return
    outcome = "complete" if result.complete else "incomplete (deadline reached)"
    print(f"workflow {outcome} after {result.polls} board reads")
    if result.assignments:
        print("assignments made:")
        for task_id, agent in result.assignments:
            print(f"  {terminal_text(task_id)} -> {terminal_text(agent)}")
    else:
        print("no assignments made")
    if result.cancellations:
        print("retired (branch not taken):")
        for task_id in result.cancellations:
            print(f"  {terminal_text(task_id)}")


async def _drive_run(
    args: argparse.Namespace,
    tasks: Sequence[CompiledTask],
    agents: Mapping[str, frozenset[str]],
    *,
    agent_factory: AgentFactory = SynapseAgent,
) -> int:
    """Connect, drive the compiled workflow against the live board, and render the outcome."""
    boards: list[Mapping[str, Any]] = []

    async def collect(data: dict[str, Any]) -> None:
        if data.get("type") == MessageType.BOARD_SNAPSHOT:
            boards.append(data.get("board", {}))

    agent = agent_factory(args.name, collect, uri=args.uri, verbose=False, token=args.token)
    conn_task = asyncio.create_task(agent.connect())
    try:
        if not await agent.wait_until_ready(timeout=_READY_TIMEOUT) or await closed_after_ready(
            agent
        ):
            print(
                terminal_text(
                    describe_connect_failure(
                        args.name,
                        args.uri,
                        close_code=agent.last_close_code,
                        close_reason=agent.last_close_reason,
                    )
                )
            )
            return 1
        gateway = _AgentGateway(agent, boards, evidence_path=args.evidence)
        loop = asyncio.get_event_loop()
        result = await run_workflow(
            tasks,
            agents,
            gateway,
            max_in_flight=args.max_in_flight,
            deadline=loop.time() + args.deadline,
            clock=loop.time,
            sleep=asyncio.sleep,
            poll_interval=args.poll_interval,
        )
        _render_run(result, json_out=args.json)
        return 0
    finally:
        agent.running = False
        conn_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await conn_task


def _cmd_run(args: argparse.Namespace, *, agent_factory: AgentFactory = SynapseAgent) -> int:
    """Run a declarative workflow live against the hub until complete or past deadline."""
    try:
        tasks = compile_to_tasks(parse_workflow(_load_workflow_file(args.file)))
        agents = _load_agents(args.agents)
        _load_evidence(args.evidence)
    except WorkflowError as exc:
        print(terminal_text(exc), file=sys.stderr)
        return 2
    return asyncio.run(_drive_run(args, tasks, agents, agent_factory=agent_factory))


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``workflow`` command group."""
    parser = subparsers.add_parser(
        "workflow",
        help="Validate and compile a declarative workflow to blackboard tasks.",
    )
    group = parser.add_subparsers(dest="workflow_command", required=True)

    validate = group.add_parser("validate", help="Parse and validate a workflow JSON file.")
    validate.add_argument("file", help="Path to the workflow JSON file.")
    validate.set_defaults(func=_cmd_validate)

    compile_parser = group.add_parser(
        "compile",
        help="Compile a workflow into the blackboard tasks the board would execute.",
    )
    compile_parser.add_argument("file", help="Path to the workflow JSON file.")
    compile_parser.add_argument(
        "--json", action="store_true", help="Emit machine-readable task declarations."
    )
    compile_parser.set_defaults(func=_cmd_compile)

    plan = group.add_parser(
        "plan",
        help="Plan the next agent assignments for a workflow against a board snapshot.",
    )
    plan.add_argument("file", help="Path to the workflow JSON file.")
    plan.add_argument("--status", default=None, help="JSON file mapping task_id -> board status.")
    plan.add_argument(
        "--agents", default=None, help="JSON file mapping agent -> [task classes] it handles."
    )
    plan.add_argument(
        "--evidence",
        default=None,
        help="JSON file mapping task_id -> evidence predicates for proof-carrying steps.",
    )
    plan.add_argument(
        "--max-in-flight", type=int, default=4, help="Most tasks allowed in progress at once."
    )
    plan.add_argument("--json", action="store_true", help="Emit machine-readable state and plan.")
    plan.set_defaults(func=_cmd_plan)

    run = group.add_parser(
        "run",
        help="Drive a workflow live against the hub: post tasks, then route ready steps.",
    )
    run.add_argument("file", help="Path to the workflow JSON file.")
    run.add_argument("--uri", default=default_hub_uri(), help="Hub URI to drive against.")
    run.add_argument("--name", default="WORKFLOW", help="Driver's display name on the hub.")
    run.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    run.add_argument(
        "--agents", default=None, help="JSON file mapping agent -> [task classes] it handles."
    )
    run.add_argument(
        "--evidence",
        default=None,
        help="JSON file mapping task_id -> evidence predicates; reread on each board poll.",
    )
    run.add_argument(
        "--max-in-flight", type=int, default=4, help="Most tasks allowed in progress at once."
    )
    run.add_argument(
        "--deadline",
        type=float,
        default=120.0,
        help="Seconds to keep driving before stopping if not yet complete.",
    )
    run.add_argument(
        "--poll-interval",
        type=float,
        default=1.0,
        help="Seconds between board readings.",
    )
    run.add_argument("--json", action="store_true", help="Emit a machine-readable run summary.")
    run.set_defaults(func=_cmd_run)

    contention = group.add_parser(
        "contention",
        help="Weigh overlapping live claims involving this workflow's tasks.",
    )
    contention.add_argument("file", help="Path to the workflow JSON file.")
    contention.add_argument("db", help="Path to the hub event store, e.g. ~/synapse/hub.db.")
    contention.add_argument(
        "--db-key-file",
        default=None,
        help="Owner-only SQLCipher key for an encrypted event store.",
    )
    contention.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    contention.add_argument(
        "--max-nodes",
        type=int,
        default=DEFAULT_MAX_GRAPH_NODES,
        help="Fail-closed ceiling on coordination events folded into the graph "
        "(0 lifts it); exceeding it errors instead of exhausting memory.",
    )
    contention.set_defaults(func=_cmd_contention)
