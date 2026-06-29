# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — declarative workflow compilation regressions

from __future__ import annotations

import pytest

from synapse_channel.core.workflow import (
    Workflow,
    WorkflowError,
    WorkflowStep,
    compile_to_tasks,
    parse_workflow,
    validate_workflow,
)


def _wf(*steps: dict[str, object], name: str = "build") -> dict[str, object]:
    return {"name": name, "steps": list(steps)}


# ---------- parse_workflow structure ----------


def test_parse_minimal_workflow() -> None:
    wf = parse_workflow(_wf({"step_id": "a", "title": "A"}))
    assert wf.name == "build"
    assert wf.steps[0] == WorkflowStep(step_id="a", title="A")


def test_parse_accepts_id_alias_and_defaults_title_to_id() -> None:
    wf = parse_workflow(_wf({"id": "compile"}))
    assert wf.steps[0].step_id == "compile"
    assert wf.steps[0].title == "compile"


def test_parse_dedupes_and_strips_depends_on() -> None:
    wf = parse_workflow(
        _wf(
            {"id": "a", "title": "A"},
            {"id": "b", "title": "B", "depends_on": [" a ", "a", ""]},
        )
    )
    assert wf.steps[1].depends_on == ("a",)


@pytest.mark.parametrize(
    "data, match",
    [
        ("not a mapping", "must be a mapping"),
        ({"name": "  ", "steps": [{"id": "a"}]}, "workflow name"),
        ({"name": "w"}, "non-empty 'steps'"),
        ({"name": "w", "steps": []}, "non-empty 'steps'"),
        ({"name": "w", "steps": ["x"]}, "step 0 must be a mapping"),
        ({"name": "w", "steps": [{"title": "no id"}]}, "step 0 id"),
        ({"name": "w", "steps": [{"id": "a", "depends_on": "a"}]}, "depends_on must be a list"),
    ],
)
def test_parse_rejects_malformed(data: object, match: str) -> None:
    with pytest.raises(WorkflowError, match=match):
        parse_workflow(data)


# ---------- validation ----------


def test_validate_rejects_duplicate_ids() -> None:
    with pytest.raises(WorkflowError, match="duplicate step id 'a'"):
        validate_workflow(Workflow("w", (WorkflowStep("a", "A"), WorkflowStep("a", "A2"))))


def test_validate_rejects_self_dependency() -> None:
    with pytest.raises(WorkflowError, match="depends on itself"):
        validate_workflow(Workflow("w", (WorkflowStep("a", "A", depends_on=("a",)),)))


def test_validate_rejects_dangling_dependency() -> None:
    with pytest.raises(WorkflowError, match="unknown step 'missing'"):
        validate_workflow(Workflow("w", (WorkflowStep("a", "A", depends_on=("missing",)),)))


def test_validate_rejects_a_cycle() -> None:
    with pytest.raises(WorkflowError, match="dependency cycle"):
        parse_workflow(
            _wf(
                {"id": "a", "depends_on": ["b"]},
                {"id": "b", "depends_on": ["a"]},
            )
        )


def test_validate_accepts_a_diamond() -> None:
    # a -> b, a -> c, b -> d, c -> d : node d is reached twice (already-explored path)
    wf = parse_workflow(
        _wf(
            {"id": "a"},
            {"id": "b", "depends_on": ["a"]},
            {"id": "c", "depends_on": ["a"]},
            {"id": "d", "depends_on": ["b", "c"]},
        )
    )
    assert {s.step_id for s in wf.steps} == {"a", "b", "c", "d"}


# ---------- compilation ----------


def test_compile_namespaces_ids_and_remaps_dependencies() -> None:
    wf = parse_workflow(
        _wf(
            {"id": "build", "title": "Build", "task_class": "ci"},
            {"id": "test", "title": "Test", "depends_on": ["build"]},
            name="release",
        )
    )
    tasks = compile_to_tasks(wf)
    build, test = tasks
    assert build.task_id == "release/build"
    assert build.task_class == "ci"
    assert test.task_id == "release/test"
    assert test.depends_on == ("release/build",)


def test_compile_orders_dependencies_before_dependents() -> None:
    wf = parse_workflow(
        _wf(
            {"id": "d", "depends_on": ["b", "c"]},
            {"id": "b", "depends_on": ["a"]},
            {"id": "c", "depends_on": ["a"]},
            {"id": "a"},
        )
    )
    order = [task.task_id.split("/")[1] for task in compile_to_tasks(wf)]
    assert order.index("a") < order.index("b") < order.index("d")
    assert order.index("a") < order.index("c") < order.index("d")


def test_compiled_task_declaration_excludes_task_class() -> None:
    wf = parse_workflow(_wf({"id": "a", "title": "A", "task_class": "ci", "description": "x"}))
    decl = compile_to_tasks(wf)[0].declaration()
    assert decl == {
        "task_id": "build/a",
        "title": "A",
        "description": "x",
        "depends_on": [],
    }
    assert "task_class" not in decl
