# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — semantic OpenCode compatibility smoke-matrix release gate
"""Validate the OpenCode cross-platform smoke matrix with duplicate-safe semantics.

The compatibility workflow's ``cross-platform-acp`` job runs the real OpenCode ACP
process on every pinned platform runner. A substring audit of the workflow text
cannot tell a live matrix row from a comment, cannot see a duplicated platform
row, and cannot notice a ``continue-on-error`` that quietly downgrades a required
lane to advisory. This gate parses the workflow and requires the matrix to
enumerate exactly the pinned ``platform -> runner`` pairs, once each, with no
advisory escape hatch below the release gate.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from tools.opencode_workflow_yaml import (
    contains_mapping_key,
    load_workflow_yaml,
    require_object,
)

_LABEL = "compatibility workflow"
_JOB = "cross-platform-acp"


class CompatibilityWorkflowError(ValueError):
    """The compatibility workflow does not preserve the required smoke-matrix gate."""


def assert_compatibility_workflow_contract(
    text: str,
    path: Path,
    *,
    expected_platforms: Mapping[str, str],
) -> None:
    """Require one non-advisory smoke job enumerating exactly the pinned platforms.

    Parameters
    ----------
    text : str
        Complete GitHub Actions workflow YAML.
    path : pathlib.Path
        Source path used in parse diagnostics.
    expected_platforms : collections.abc.Mapping of str to str
        Exact ``platform -> runner`` pairs the matrix must enumerate once each.

    Raises
    ------
    CompatibilityWorkflowError
        If the YAML is malformed, mappings contain duplicate keys, the smoke job
        contains ``continue-on-error``, the matrix widens beyond one exact
        ``include`` list, a platform row repeats, or the enumerated
        ``platform -> runner`` pairs differ from ``expected_platforms``.
    """
    workflow = load_workflow_yaml(text, path, error_cls=CompatibilityWorkflowError, label=_LABEL)
    jobs = require_object(
        workflow.get("jobs"),
        "compatibility workflow.jobs",
        error_cls=CompatibilityWorkflowError,
    )
    job = require_object(
        jobs.get(_JOB),
        f"compatibility workflow.jobs.{_JOB}",
        error_cls=CompatibilityWorkflowError,
    )
    if contains_mapping_key(job, "continue-on-error"):
        raise CompatibilityWorkflowError(
            "compatibility workflow must gate every pinned platform lane"
        )
    strategy = require_object(
        job.get("strategy"),
        f"compatibility workflow {_JOB} strategy",
        error_cls=CompatibilityWorkflowError,
    )
    if strategy.get("fail-fast") is not False:
        raise CompatibilityWorkflowError(
            "compatibility workflow must set fail-fast: false so no platform lane is masked"
        )
    matrix = require_object(
        strategy.get("matrix"),
        f"compatibility workflow {_JOB} matrix",
        error_cls=CompatibilityWorkflowError,
    )
    matrix_fields = {str(key) for key in matrix}
    if matrix_fields != {"include"}:
        raise CompatibilityWorkflowError(
            "compatibility workflow matrix fields differ: "
            f"expected=['include'], actual={sorted(matrix_fields)}"
        )
    include = matrix.get("include")
    if not isinstance(include, list):
        raise CompatibilityWorkflowError(
            f"compatibility workflow {_JOB} matrix.include must be an array"
        )
    platforms: dict[str, str] = {}
    for index, value in enumerate(include):
        row = require_object(
            value,
            f"compatibility workflow {_JOB} matrix.include[{index}]",
            error_cls=CompatibilityWorkflowError,
        )
        platform = row.get("platform")
        runner = row.get("runner")
        if not isinstance(platform, str):
            raise CompatibilityWorkflowError(
                f"compatibility workflow {_JOB} matrix.include[{index}].platform must be a string"
            )
        if not isinstance(runner, str):
            raise CompatibilityWorkflowError(
                f"compatibility workflow {_JOB} matrix.include[{index}].runner must be a string"
            )
        if platform in platforms:
            raise CompatibilityWorkflowError(
                f"compatibility workflow matrix duplicates platform {platform!r}"
            )
        platforms[platform] = runner
    expected = dict(expected_platforms)
    if platforms != expected:
        raise CompatibilityWorkflowError(
            "compatibility workflow matrix platforms differ: "
            f"expected={sorted(expected.items())}, actual={sorted(platforms.items())}"
        )
