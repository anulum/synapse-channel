# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — semantic OpenCode editor workflow release gate
"""Validate the required OpenCode editor job with duplicate-safe YAML semantics."""

from __future__ import annotations

from collections.abc import Collection
from pathlib import Path

from tools.opencode_workflow_yaml import (
    contains_mapping_key,
    load_workflow_yaml,
    require_object,
)

_LABEL = "editor workflow"


class EditorWorkflowError(ValueError):
    """The editor workflow does not preserve the required release gate."""


def assert_editor_workflow_contract(
    text: str,
    path: Path,
    *,
    expected_lanes: Collection[str],
) -> None:
    """Require one non-advisory editor job with exactly the pinned client lanes.

    Parameters
    ----------
    text : str
        Complete GitHub Actions workflow YAML.
    path : pathlib.Path
        Source path used in parse diagnostics.
    expected_lanes : collections.abc.Collection of str
        Exact editor client names the matrix must enumerate once each.

    Raises
    ------
    EditorWorkflowError
        If YAML is malformed, mappings contain duplicate keys, the gating job
        contains ``continue-on-error``, does not set ``fail-fast: false``, or the
        matrix widens beyond one exact ``include`` list containing the expected
        lanes.
    """
    workflow = load_workflow_yaml(text, path, error_cls=EditorWorkflowError, label=_LABEL)
    jobs = require_object(
        workflow.get("jobs"), "editor workflow.jobs", error_cls=EditorWorkflowError
    )
    editor_job = require_object(
        jobs.get("editor-client"),
        "editor workflow.jobs.editor-client",
        error_cls=EditorWorkflowError,
    )
    if contains_mapping_key(editor_job, "continue-on-error"):
        raise EditorWorkflowError("editor workflow must gate every pinned real-client lane")
    strategy = require_object(
        editor_job.get("strategy"),
        "editor workflow editor-client strategy",
        error_cls=EditorWorkflowError,
    )
    if strategy.get("fail-fast") is not False:
        raise EditorWorkflowError(
            "editor workflow must set fail-fast: false so no pinned real-client lane is masked"
        )
    matrix = require_object(
        strategy.get("matrix"),
        "editor workflow editor-client matrix",
        error_cls=EditorWorkflowError,
    )
    matrix_fields = {str(key) for key in matrix}
    if matrix_fields != {"include"}:
        raise EditorWorkflowError(
            "editor workflow matrix fields differ: "
            f"expected=['include'], actual={sorted(matrix_fields)}"
        )
    include = matrix.get("include")
    if not isinstance(include, list):
        raise EditorWorkflowError("editor workflow editor-client matrix.include must be an array")
    lanes: list[str] = []
    for index, value in enumerate(include):
        row = require_object(
            value,
            f"editor workflow editor-client matrix.include[{index}]",
            error_cls=EditorWorkflowError,
        )
        lane = row.get("client")
        if not isinstance(lane, str):
            raise EditorWorkflowError(
                f"editor workflow editor-client matrix.include[{index}].client must be a string"
            )
        lanes.append(lane)
    expected = set(expected_lanes)
    if len(lanes) != len(expected) or set(lanes) != expected:
        raise EditorWorkflowError(
            "editor workflow matrix lanes differ: "
            f"expected={sorted(expected)}, actual={sorted(lanes)}"
        )
