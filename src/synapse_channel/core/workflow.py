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

A dependency may be **conditional**: instead of waiting for a step to merely
*finish*, a dependent can wait for a specific terminal outcome — ``done`` or
``cancelled``. That lets a workflow branch on result (run one step on success,
another on failure) rather than only gating on completion. The condition is carried
through compilation but enforced by the driver, not the board: the board still sees
plain dependency edges (so it gates on terminal-ness), while the driver decides
whether the recorded outcome actually satisfies each conditional edge.

A step may also declare evidence requirements with ``requires``. Those requirements
are metadata for the workflow driver: a step becomes ready only when its dependencies
are satisfied and an evidence snapshot proves every declared predicate. The board
still receives an ordinary task; the evidence gate is evaluated before the driver
advises an owner.

A step may also **fan out** over a list of items: a step with a ``for_each`` list
compiles to one parallel task per item, and any dependency on that step expands to a
dependency on *every* expanded task — a map (the parallel tasks) and a join (a
downstream step that waits for all of them). The expansion is purely an
authoring-time rewrite into ordinary blackboard tasks and edges; the board and the
driver see only the expanded graph.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

TASK_ID_SEPARATOR = "/"
"""Separator joining the workflow name and a step id into a board task id."""

FANOUT_SEPARATOR = "#"
"""Separator joining a fan-out step id and one of its items into a concrete step id."""

FANOUT_MAX_WIDTH = 64
"""Most tasks a single fan-out step may expand to — a bound on accidental blow-up."""

ANY_TERMINAL = ""
"""Dependency condition meaning any terminal status satisfies the edge."""

TERMINAL_CONDITIONS = frozenset({"done", "cancelled"})
"""The terminal statuses a conditional dependency may require."""

EVIDENCE_REQUIREMENTS = frozenset(
    {"claim", "receipt", "tests", "policy", "approval", "sandbox_run", "mailbox", "dead_letters"}
)
"""Evidence predicate names a workflow step may require before assignment."""


class WorkflowError(ValueError):
    """Raised when a workflow is malformed: bad fields, dangling deps, or a cycle."""


@dataclass(frozen=True)
class StepDependency:
    """A dependency edge from one step to another, optionally conditioned on outcome.

    Attributes
    ----------
    step : str
        The step id this edge waits on.
    on : str
        The terminal status that satisfies the edge: ``"done"`` or ``"cancelled"``,
        or :data:`ANY_TERMINAL` (``""``) when any terminal status will do.
    """

    step: str
    on: str = ANY_TERMINAL


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
    depends_on : tuple[StepDependency, ...]
        The edges that must be satisfied before this step is ready, each optionally
        conditioned on the dependency's terminal outcome.
    for_each : tuple[str, ...]
        Fan-out items. When non-empty, the step expands at compile time into one
        parallel task per item; a dependency on this step then expands to a join over
        all of them. Empty for an ordinary single-task step.
    requires : tuple[tuple[str, str], ...]
        Evidence predicates the driver must see for this step before it is ready.
        Each pair is ``(predicate, expected_value)`` and is checked against an
        evidence snapshot keyed by compiled task id.
    """

    step_id: str
    title: str
    task_class: str = ""
    description: str = ""
    depends_on: tuple[StepDependency, ...] = ()
    for_each: tuple[str, ...] = ()
    requires: tuple[tuple[str, str], ...] = ()


def _expand_step(step: WorkflowStep) -> tuple[tuple[str, str], ...]:
    """Return the ``(concrete_step_id, item)`` pairs a step expands to.

    A fan-out step (non-empty ``for_each``) expands to one pair per item, with the
    concrete id ``"<step>#<item>"``; an ordinary step expands to a single pair with
    its own id and an empty item.
    """
    if step.for_each:
        return tuple((f"{step.step_id}{FANOUT_SEPARATOR}{item}", item) for item in step.for_each)
    return ((step.step_id, ""),)


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
    conditions : tuple[tuple[str, str], ...]
        ``(namespaced_dep_id, required_terminal_status)`` pairs for the conditional
        edges only. An edge absent here is unconditional — any terminal status of
        the dependency satisfies it. Carried as driver metadata; the board never
        sees the condition, only the plain ``depends_on`` edge.
    evidence_requirements : tuple[tuple[str, str], ...]
        ``(predicate, expected_value)`` pairs the driver must find for this task in
        an evidence snapshot before routing it. Carried as driver metadata; the
        board receives only the task declaration.
    """

    task_id: str
    title: str
    description: str
    depends_on: tuple[str, ...]
    task_class: str
    conditions: tuple[tuple[str, str], ...] = ()
    evidence_requirements: tuple[tuple[str, str], ...] = ()

    def declaration(self) -> dict[str, Any]:
        """Return the board-declaration kwargs (``task_class`` is driver metadata)."""
        return {
            "task_id": self.task_id,
            "title": self.title,
            "description": self.description,
            "depends_on": list(self.depends_on),
        }

    def required_status(self, dep_id: str) -> str:
        """Return the terminal status ``dep_id`` must reach, or ``""`` if unconditional."""
        for dependency, status in self.conditions:
            if dependency == dep_id:
                return status
        return ANY_TERMINAL

    def required_evidence(self, predicate: str) -> str:
        """Return the expected value for ``predicate``, or ``""`` when it is not required."""
        for name, value in self.evidence_requirements:
            if name == predicate:
                return value
        return ""


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


def _parse_dependency(raw: object, step_id: str) -> StepDependency | None:
    """Build one :class:`StepDependency` from a raw edge, or ``None`` if it is blank.

    An edge is either a bare step id (``"build"`` — unconditional) or a mapping
    (``{"step": "build", "on": "done"}``) carrying a terminal-outcome condition. The
    ``on`` value, when present, must be a terminal status in
    :data:`TERMINAL_CONDITIONS`.
    """
    if isinstance(raw, dict):
        step = str(raw.get("step") or raw.get("id") or "").strip()
        on = str(raw.get("on", ANY_TERMINAL)).strip()
    else:
        step = str(raw).strip()
        on = ANY_TERMINAL
    if not step:
        return None
    if on and on not in TERMINAL_CONDITIONS:
        msg = (
            f"step {step_id!r} dependency on {step!r} has invalid condition {on!r}; "
            f"expected one of {sorted(TERMINAL_CONDITIONS)}"
        )
        raise WorkflowError(msg)
    return StepDependency(step=step, on=on)


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
    seen: dict[str, StepDependency] = {}
    for edge in depends_raw:
        dependency = _parse_dependency(edge, step_id)
        if dependency is not None:
            seen.setdefault(dependency.step, dependency)
    for_each: tuple[str, ...] = ()
    if "for_each" in raw:
        fan_raw = raw["for_each"]
        if not isinstance(fan_raw, (list, tuple)):
            msg = f"step {step_id!r} for_each must be a list"
            raise WorkflowError(msg)
        for_each = tuple(dict.fromkeys(str(item).strip() for item in fan_raw if str(item).strip()))
        if not for_each:
            msg = f"step {step_id!r} for_each must list at least one non-empty item"
            raise WorkflowError(msg)
    requires = _parse_requires(raw.get("requires", {}), step_id)
    return WorkflowStep(
        step_id=step_id,
        title=title,
        task_class=str(raw.get("task_class", "")).strip(),
        description=str(raw.get("description", "")).strip(),
        depends_on=tuple(seen.values()),
        for_each=for_each,
        requires=requires,
    )


def _parse_requires(raw: object, step_id: str) -> tuple[tuple[str, str], ...]:
    """Parse a step's evidence predicates from its ``requires`` mapping."""
    if raw in (None, ""):
        return ()
    if not isinstance(raw, dict):
        msg = f"step {step_id!r} requires must be a mapping"
        raise WorkflowError(msg)
    requirements: list[tuple[str, str]] = []
    for name, value in raw.items():
        predicate = str(name).strip()
        expected = str(value).strip()
        if predicate not in EVIDENCE_REQUIREMENTS:
            msg = (
                f"step {step_id!r} requires unknown evidence predicate {predicate!r}; "
                f"expected one of {sorted(EVIDENCE_REQUIREMENTS)}"
            )
            raise WorkflowError(msg)
        if not expected:
            msg = f"step {step_id!r} requires {predicate!r} to have a non-empty expected value"
            raise WorkflowError(msg)
        requirements.append((predicate, expected))
    return tuple(sorted(requirements))


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
            if dep.step == step.step_id:
                msg = f"step {step.step_id!r} depends on itself"
                raise WorkflowError(msg)
            if dep.step not in ids:
                msg = f"step {step.step_id!r} depends on unknown step {dep.step!r}"
                raise WorkflowError(msg)
    _validate_fan_out(workflow)
    _reject_cycle(workflow)


def _validate_fan_out(workflow: Workflow) -> None:
    """Bound each fan-out width and reject expansions that collide on a task id."""
    concrete: dict[str, str] = {}
    for step in workflow.steps:
        if len(step.for_each) > FANOUT_MAX_WIDTH:
            msg = (
                f"step {step.step_id!r} fans out to {len(step.for_each)} tasks; "
                f"the limit is {FANOUT_MAX_WIDTH}"
            )
            raise WorkflowError(msg)
        for concrete_id, _item in _expand_step(step):
            if concrete_id in concrete:
                msg = f"fan-out produces a duplicate task id {concrete_id!r}"
                raise WorkflowError(msg)
            concrete[concrete_id] = step.step_id


def _reject_cycle(workflow: Workflow) -> None:
    """Raise :class:`WorkflowError` naming a step on a dependency cycle, if any."""
    edges = {step.step_id: tuple(dep.step for dep in step.depends_on) for step in workflow.steps}
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

    Each step becomes a task whose id is namespaced by the workflow name
    (``"<workflow>/<step>"``), with its ``depends_on`` remapped to the namespaced
    ids. A fan-out step (``for_each``) expands to one task per item
    (``"<workflow>/<step>#<item>"``), and a dependency on any step expands to edges
    to every task that step produced — so a dependency on a fan-out step joins all of
    its parallel tasks. Tasks are returned in dependency order (every task appears
    after the tasks it depends on), so posting them in order keeps the board
    consistent even before its own ready/blocked derivation runs.

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

    def task_id(concrete_step_id: str) -> str:
        return f"{workflow.name}{TASK_ID_SEPARATOR}{concrete_step_id}"

    by_id = {step.step_id: step for step in workflow.steps}
    expansion = {step.step_id: _expand_step(step) for step in workflow.steps}
    ordered: list[str] = []
    placed: set[str] = set()

    def place(step_id: str) -> None:
        if step_id in placed:
            return
        for dep in by_id[step_id].depends_on:
            place(dep.step)
        placed.add(step_id)
        ordered.append(step_id)

    for step in workflow.steps:
        place(step.step_id)

    tasks: list[CompiledTask] = []
    for step_id in ordered:
        step = by_id[step_id]
        dep_ids = [
            (task_id(dep_concrete), dep.on)
            for dep in step.depends_on
            for dep_concrete, _item in expansion[dep.step]
        ]
        for concrete_id, item in expansion[step_id]:
            tasks.append(
                CompiledTask(
                    task_id=task_id(concrete_id),
                    title=f"{step.title} [{item}]" if item else step.title,
                    description=step.description,
                    depends_on=tuple(dep_task for dep_task, _on in dep_ids),
                    task_class=step.task_class,
                    conditions=tuple((dep_task, on) for dep_task, on in dep_ids if on),
                    evidence_requirements=step.requires,
                )
            )
    return tuple(tasks)
