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
    FANOUT_MAX_WIDTH,
    StepDependency,
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
    assert wf.steps[1].depends_on == (StepDependency("a"),)


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
        validate_workflow(
            Workflow("w", (WorkflowStep("a", "A", depends_on=(StepDependency("a"),)),))
        )


def test_validate_rejects_dangling_dependency() -> None:
    with pytest.raises(WorkflowError, match="unknown step 'missing'"):
        validate_workflow(
            Workflow("w", (WorkflowStep("a", "A", depends_on=(StepDependency("missing"),)),))
        )


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


# ---------- conditional dependencies ----------


def test_parse_accepts_a_conditional_dependency_object() -> None:
    wf = parse_workflow(
        _wf(
            {"id": "test"},
            {"id": "deploy", "depends_on": [{"step": "test", "on": "done"}]},
        )
    )
    assert wf.steps[1].depends_on == (StepDependency("test", "done"),)


def test_parse_dependency_object_uses_id_alias() -> None:
    wf = parse_workflow(
        _wf(
            {"id": "test"},
            {"id": "deploy", "depends_on": [{"id": "test", "on": "cancelled"}]},
        )
    )
    assert wf.steps[1].depends_on == (StepDependency("test", "cancelled"),)


def test_parse_drops_a_dependency_object_without_a_step() -> None:
    wf = parse_workflow(
        _wf(
            {"id": "a"},
            {"id": "b", "depends_on": [{"on": "done"}, "a"]},
        )
    )
    assert wf.steps[1].depends_on == (StepDependency("a"),)


def test_parse_rejects_an_invalid_dependency_condition() -> None:
    with pytest.raises(WorkflowError, match="invalid condition 'maybe'"):
        parse_workflow(
            _wf(
                {"id": "a"},
                {"id": "b", "depends_on": [{"step": "a", "on": "maybe"}]},
            )
        )


def test_compile_carries_conditions_and_required_status() -> None:
    wf = parse_workflow(
        _wf(
            {"id": "test"},
            {"id": "deploy", "depends_on": [{"step": "test", "on": "done"}]},
            {"id": "rollback", "depends_on": [{"step": "test", "on": "cancelled"}, "test"]},
            name="release",
        )
    )
    _test, deploy, rollback = compile_to_tasks(wf)
    assert deploy.depends_on == ("release/test",)
    assert deploy.conditions == (("release/test", "done"),)
    assert deploy.required_status("release/test") == "done"
    # a dep id absent from the conditions is unconditional even when others are set
    assert deploy.required_status("release/other") == ""
    # an unconditional duplicate edge to the same step is deduped to the first (conditional)
    assert rollback.conditions == (("release/test", "cancelled"),)
    assert rollback.required_status("release/test") == "cancelled"


def test_required_status_is_empty_for_an_unconditional_edge() -> None:
    wf = parse_workflow(_wf({"id": "a"}, {"id": "b", "depends_on": ["a"]}))
    _a, b = compile_to_tasks(wf)
    assert b.conditions == ()
    assert b.required_status("release/a") == ""


# ---------- fan-out / map-join ----------


def test_parse_reads_and_dedupes_for_each() -> None:
    wf = parse_workflow(_wf({"id": "shard", "for_each": [" a ", "a", "b", ""]}))
    assert wf.steps[0].for_each == ("a", "b")


def test_parse_rejects_a_non_list_for_each() -> None:
    with pytest.raises(WorkflowError, match="for_each must be a list"):
        parse_workflow(_wf({"id": "shard", "for_each": "a"}))


def test_parse_rejects_an_empty_for_each() -> None:
    with pytest.raises(WorkflowError, match="at least one non-empty item"):
        parse_workflow(_wf({"id": "shard", "for_each": ["", "  "]}))


def test_compile_fans_a_step_out_into_one_task_per_item() -> None:
    wf = parse_workflow(
        _wf({"id": "shard", "title": "Shard", "for_each": ["us", "eu"]}, name="ingest")
    )
    tasks = compile_to_tasks(wf)
    assert [task.task_id for task in tasks] == ["ingest/shard#us", "ingest/shard#eu"]
    assert [task.title for task in tasks] == ["Shard [us]", "Shard [eu]"]


def test_compile_joins_a_dependent_over_every_fanned_task() -> None:
    wf = parse_workflow(
        _wf(
            {"id": "shard", "for_each": ["us", "eu"]},
            {"id": "merge", "title": "Merge", "depends_on": ["shard"]},
            name="ingest",
        )
    )
    merge = compile_to_tasks(wf)[-1]
    assert merge.task_id == "ingest/merge"
    assert merge.depends_on == ("ingest/shard#us", "ingest/shard#eu")


def test_compile_carries_a_condition_onto_every_fanned_join_edge() -> None:
    wf = parse_workflow(
        _wf(
            {"id": "shard", "for_each": ["us", "eu"]},
            {"id": "merge", "depends_on": [{"step": "shard", "on": "done"}]},
            name="ingest",
        )
    )
    merge = compile_to_tasks(wf)[-1]
    assert merge.conditions == (("ingest/shard#us", "done"), ("ingest/shard#eu", "done"))


def test_validate_rejects_a_fan_out_wider_than_the_limit() -> None:
    items = [f"i{n}" for n in range(FANOUT_MAX_WIDTH + 1)]
    with pytest.raises(WorkflowError, match="the limit is"):
        parse_workflow(_wf({"id": "shard", "for_each": items}))


def test_validate_rejects_a_fan_out_that_collides_with_a_step_id() -> None:
    with pytest.raises(WorkflowError, match="duplicate task id 'a#x'"):
        parse_workflow(_wf({"id": "a", "for_each": ["x"]}, {"id": "a#x"}))
