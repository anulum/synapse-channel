# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — declarative workflow that compiles to blackboard tasks
"""A declarative workflow that compiles to the existing blackboard DAG.

The blackboard already executes a task graph: a task with unmet ``depends_on``
edges is blocked, and it becomes ready when every dependency reaches a terminal
status. This module adds the authoring layer on top of that substrate. A
:class:`Workflow` is a plain, dependency-free artifact — a name and a list of
:class:`WorkflowStep` records — that :func:`compile_to_tasks` turns into ordinary
blackboard task declarations. There is no new runtime and no new dependency: the
board's existing ready/blocked derivation runs the compiled workflow.

Validation is strict and happens before anything is posted: step ids must be
unique and non-empty, every ``depends_on`` must reference a declared step, no step
may depend on itself, and the dependency graph must be acyclic — a workflow with a
cycle can never make progress, so it is rejected at authoring time rather than
deadlocking the board.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

TASK_ID_SEPARATOR = "/"
"""Separator joining the workflow name and a step id into a board task id."""


class WorkflowError(ValueError):
    """Raised when a workflow is malformed: bad fields, dangling deps, or a cycle."""


@dataclass(frozen=True)
class WorkflowStep:
    """One step of a workflow, compiled to a single blackboard task.

    Attributes
    ----------
    step_id : str
        Workflow-unique identifier for the step.
    title : str
        Short human-readable name of the work.
    task_class : str
        Routing hint: the capability class a driver routes the task to. Advisory
        and carried through compilation; the blackboard itself does not store it.
    description : str
        Optional longer description or acceptance notes.
    depends_on : tuple[str, ...]
        Step ids that must finish before this step is ready.
    """

    step_id: str
    title: str
    task_class: str = ""
    description: str = ""
    depends_on: tuple[str, ...] = ()


@dataclass(frozen=True)
class Workflow:
    """A named, validated, acyclic set of workflow steps.

    Attributes
    ----------
    name : str
        Workflow name; namespaces every compiled task id.
    steps : tuple[WorkflowStep, ...]
        The steps, in author order.
    """

    name: str
    steps: tuple[WorkflowStep, ...]


@dataclass(frozen=True)
class CompiledTask:
    """A blackboard task declaration compiled from a workflow step.

    Attributes
    ----------
    task_id : str
        Namespaced board task id (``"<workflow>/<step>"``).
    title : str
        Short human-readable name.
    description : str
        Optional longer description.
    depends_on : tuple[str, ...]
        Namespaced board task ids this task waits on.
    task_class : str
        Routing hint carried from the step for a driver to route on.
    """

    task_id: str
    title: str
    description: str
    depends_on: tuple[str, ...]
    task_class: str

    def declaration(self) -> dict[str, Any]:
        """Return the board-declaration kwargs (``task_class`` is driver metadata)."""
        return {
            "task_id": self.task_id,
            "title": self.title,
            "description": self.description,
            "depends_on": list(self.depends_on),
        }


def _require_text(value: object, label: str) -> str:
    """Return a stripped non-empty string, or raise :class:`WorkflowError`.

    ``None`` (a missing key) counts as empty, so a missing id or name is rejected
    rather than silently becoming the literal string ``"None"``.
    """
    text = "" if value is None else str(value).strip()
    if not text:
        msg = f"{label} must be a non-empty string"
        raise WorkflowError(msg)
    return text


def _parse_step(raw: object, index: int) -> WorkflowStep:
    """Build one :class:`WorkflowStep` from a raw mapping."""
    if not isinstance(raw, dict):
        msg = f"step {index} must be a mapping"
        raise WorkflowError(msg)
    step_id = _require_text(raw.get("step_id") or raw.get("id"), f"step {index} id")
    title = _require_text(raw.get("title") or step_id, f"step {step_id!r} title")
    depends_raw = raw.get("depends_on", ())
    if not isinstance(depends_raw, (list, tuple)):
        msg = f"step {step_id!r} depends_on must be a list"
        raise WorkflowError(msg)
    depends_on = tuple(dict.fromkeys(str(dep).strip() for dep in depends_raw if str(dep).strip()))
    return WorkflowStep(
        step_id=step_id,
        title=title,
        task_class=str(raw.get("task_class", "")).strip(),
        description=str(raw.get("description", "")).strip(),
        depends_on=depends_on,
    )


def parse_workflow(data: object) -> Workflow:
    """Parse and validate a workflow artifact into a :class:`Workflow`.

    Parameters
    ----------
    data : object
        A mapping with a ``name`` and a non-empty ``steps`` list of step mappings.

    Returns
    -------
    Workflow
        The validated, acyclic workflow.

    Raises
    ------
    WorkflowError
        If the artifact is not a mapping, the name is empty, steps are missing or
        malformed, an id is duplicated, a dependency dangles, a step depends on
        itself, or the dependency graph has a cycle.
    """
    if not isinstance(data, dict):
        msg = "workflow must be a mapping"
        raise WorkflowError(msg)
    name = _require_text(data.get("name"), "workflow name")
    raw_steps = data.get("steps")
    if not isinstance(raw_steps, (list, tuple)) or not raw_steps:
        msg = "workflow must declare a non-empty 'steps' list"
        raise WorkflowError(msg)
    steps = tuple(_parse_step(raw, index) for index, raw in enumerate(raw_steps))
    workflow = Workflow(name=name, steps=steps)
    validate_workflow(workflow)
    return workflow


def validate_workflow(workflow: Workflow) -> None:
    """Validate id uniqueness, dependency resolution, and acyclicity.

    Parameters
    ----------
    workflow : Workflow
        The workflow to check.

    Raises
    ------
    WorkflowError
        On a duplicate id, a self-dependency, a dangling dependency, or a cycle.
    """
    ids: set[str] = set()
    for step in workflow.steps:
        if step.step_id in ids:
            msg = f"duplicate step id {step.step_id!r}"
            raise WorkflowError(msg)
        ids.add(step.step_id)
    for step in workflow.steps:
        for dep in step.depends_on:
            if dep == step.step_id:
                msg = f"step {step.step_id!r} depends on itself"
                raise WorkflowError(msg)
            if dep not in ids:
                msg = f"step {step.step_id!r} depends on unknown step {dep!r}"
                raise WorkflowError(msg)
    _reject_cycle(workflow)


def _reject_cycle(workflow: Workflow) -> None:
    """Raise :class:`WorkflowError` naming a step on a dependency cycle, if any."""
    edges = {step.step_id: step.depends_on for step in workflow.steps}
    # 0 = unvisited, 1 = on the current DFS path, 2 = fully explored
    state: dict[str, int] = dict.fromkeys(edges, 0)

    def visit(node: str) -> None:
        state[node] = 1
        for dep in edges[node]:
            if state[dep] == 1:
                msg = f"workflow has a dependency cycle through step {node!r}"
                raise WorkflowError(msg)
            if state[dep] == 0:
                visit(dep)
        state[node] = 2

    for step_id in edges:
        if state[step_id] == 0:
            visit(step_id)


def compile_to_tasks(workflow: Workflow) -> tuple[CompiledTask, ...]:
    """Compile a validated workflow into ordered blackboard task declarations.

    Each step becomes one task whose id is namespaced by the workflow name
    (``"<workflow>/<step>"``), with its ``depends_on`` remapped to the namespaced
    ids. Tasks are returned in dependency order (every task appears after the tasks
    it depends on), so posting them in order keeps the board consistent even before
    its own ready/blocked derivation runs.

    Parameters
    ----------
    workflow : Workflow
        A workflow already validated by :func:`parse_workflow` or
        :func:`validate_workflow`.

    Returns
    -------
    tuple[CompiledTask, ...]
        The compiled tasks in dependency order.
    """
    validate_workflow(workflow)

    def task_id(step_id: str) -> str:
        return f"{workflow.name}{TASK_ID_SEPARATOR}{step_id}"

    by_id = {step.step_id: step for step in workflow.steps}
    ordered: list[str] = []
    placed: set[str] = set()

    def place(step_id: str) -> None:
        if step_id in placed:
            return
        for dep in by_id[step_id].depends_on:
            place(dep)
        placed.add(step_id)
        ordered.append(step_id)

    for step in workflow.steps:
        place(step.step_id)

    return tuple(
        CompiledTask(
            task_id=task_id(step_id),
            title=by_id[step_id].title,
            description=by_id[step_id].description,
            depends_on=tuple(task_id(dep) for dep in by_id[step_id].depends_on),
            task_class=by_id[step_id].task_class,
        )
        for step_id in ordered
    )
