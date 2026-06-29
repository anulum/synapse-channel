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
authoring tools over :mod:`synapse_channel.core.workflow`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from synapse_channel.core.workflow import WorkflowError, compile_to_tasks, parse_workflow
from synapse_channel.core.workflow_driver import derive_state, plan_assignments


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


def _cmd_validate(args: argparse.Namespace) -> int:
    """Validate a workflow file and report the outcome."""
    try:
        workflow = parse_workflow(_load_workflow_file(args.file))
    except WorkflowError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"workflow '{workflow.name}' is valid: {len(workflow.steps)} steps")
    return 0


def _cmd_compile(args: argparse.Namespace) -> int:
    """Compile a workflow file into blackboard task declarations."""
    try:
        tasks = compile_to_tasks(parse_workflow(_load_workflow_file(args.file)))
    except WorkflowError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.json:
        payload = [{**task.declaration(), "task_class": task.task_class} for task in tasks]
        print(json.dumps(payload, indent=2))
        return 0
    print(f"{len(tasks)} blackboard tasks:")
    for task in tasks:
        deps = ", ".join(task.depends_on) if task.depends_on else "(none)"
        task_class = f" [{task.task_class}]" if task.task_class else ""
        print(f"  {task.task_id}{task_class} <- {deps}")
    return 0


def _cmd_plan(args: argparse.Namespace) -> int:
    """Plan the next agent assignments for a workflow against a board snapshot."""
    try:
        tasks = compile_to_tasks(parse_workflow(_load_workflow_file(args.file)))
        status = _load_status(args.status)
        agents = _load_agents(args.agents)
    except WorkflowError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    state = derive_state(tasks, status)
    plan = plan_assignments(tasks, status, agents, max_in_flight=args.max_in_flight)
    if args.json:
        print(json.dumps({"state": state.to_dict(), "plan": [a.to_dict() for a in plan]}, indent=2))
        return 0
    print(
        f"state: {len(state.done)} done, {len(state.in_flight)} in flight, "
        f"{len(state.ready)} ready, {len(state.blocked)} blocked"
        + (" (complete)" if state.complete else "")
    )
    if not plan:
        print("no assignments")
        return 0
    print("assignments:")
    for assignment in plan:
        task_class = f" [{assignment.task_class}]" if assignment.task_class else ""
        print(f"  {assignment.task_id}{task_class} -> {assignment.agent}")
    return 0


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
        "--max-in-flight", type=int, default=4, help="Most tasks allowed in progress at once."
    )
    plan.add_argument("--json", action="store_true", help="Emit machine-readable state and plan.")
    plan.set_defaults(func=_cmd_plan)
