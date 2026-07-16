# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — semantic OpenCode editor workflow release gate
"""Validate the required OpenCode editor job with duplicate-safe YAML semantics."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from pathlib import Path
from typing import Any, cast


class EditorWorkflowError(ValueError):
    """The editor workflow does not preserve the required release gate."""


def _object(value: object, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise EditorWorkflowError(f"{where} must be an object")
    return value


def _load_editor_workflow(text: str, path: Path) -> Mapping[str, Any]:
    import yaml

    unique_key_loader = cast(Any, type("_UniqueKeyLoader", (yaml.SafeLoader,), {}))

    def construct_unique_mapping(
        loader: Any,
        node: Any,
        deep: bool = False,
    ) -> dict[object, object]:
        loader.flatten_mapping(node)
        mapping: dict[object, object] = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node, deep=deep)
            try:
                duplicate = key in mapping
            except TypeError as exc:
                raise EditorWorkflowError(
                    "editor workflow YAML mapping keys must be hashable"
                ) from exc
            if duplicate:
                raise EditorWorkflowError(f"editor workflow YAML duplicates mapping key {key!r}")
            mapping[key] = loader.construct_object(value_node, deep=deep)
        return mapping

    unique_key_loader.add_constructor(
        yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
        construct_unique_mapping,
    )
    loader = unique_key_loader(text)
    try:
        document = cast(object, loader.get_single_data())
    except yaml.YAMLError as exc:
        raise EditorWorkflowError(f"cannot parse editor workflow YAML: {path}") from exc
    finally:
        cast(Any, loader).dispose()
    return _object(document, "editor workflow")


def _contains_mapping_key(value: object, expected: str) -> bool:
    if isinstance(value, Mapping):
        return any(
            key == expected or _contains_mapping_key(child, expected)
            for key, child in value.items()
        )
    if isinstance(value, list):
        return any(_contains_mapping_key(child, expected) for child in value)
    return False


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
        contains ``continue-on-error``, or the matrix widens beyond one exact
        ``include`` list containing the expected lanes.
    """
    workflow = _load_editor_workflow(text, path)
    jobs = _object(workflow.get("jobs"), "editor workflow.jobs")
    editor_job = _object(jobs.get("editor-client"), "editor workflow.jobs.editor-client")
    if _contains_mapping_key(editor_job, "continue-on-error"):
        raise EditorWorkflowError("editor workflow must gate every pinned real-client lane")
    strategy = _object(editor_job.get("strategy"), "editor workflow editor-client strategy")
    matrix = _object(strategy.get("matrix"), "editor workflow editor-client matrix")
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
        row = _object(value, f"editor workflow editor-client matrix.include[{index}]")
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
