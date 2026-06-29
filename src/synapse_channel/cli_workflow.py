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
