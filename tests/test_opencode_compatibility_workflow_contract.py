# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — semantic OpenCode compatibility-matrix contract tests
"""Exercise the duplicate-safe OpenCode cross-platform smoke-matrix release gate."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from tools.opencode_compatibility_workflow_contract import (
        CompatibilityWorkflowError,
        assert_compatibility_workflow_contract,
    )
else:
    # tools/ is not an installed package surface; mirror the contract test import
    # so focused CI can collect without PYTHONPATH=.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from tools.opencode_compatibility_workflow_contract import (
        CompatibilityWorkflowError,
        assert_compatibility_workflow_contract,
    )

_EXPECTED = {
    "linux-x64": "ubuntu-24.04",
    "linux-arm64": "ubuntu-24.04-arm",
    "darwin-arm64": "macos-15",
    "darwin-x64": "macos-15-intel",
    "windows-x64": "windows-2025",
}


def _rows(pairs: list[tuple[str, str]]) -> str:
    return "\n".join(
        f"          - platform: {platform}\n            runner: {runner}"
        for platform, runner in pairs
    )


def _workflow(
    *,
    include: str | None = None,
    fail_fast: str = "      fail-fast: false\n",
    job_extra: str = "",
    matrix_extra: str = "",
) -> str:
    include_body = include if include is not None else _rows(list(_EXPECTED.items()))
    return (
        "name: opencode-compatibility\n"
        "jobs:\n"
        "  cross-platform-acp:\n"
        f"{job_extra}"
        "    strategy:\n"
        f"{fail_fast}"
        "      matrix:\n"
        f"{matrix_extra}"
        "        include:\n"
        f"{include_body}\n"
        "    steps:\n"
        "      - uses: actions/checkout@pinned\n"
    )


def _assert(text: str) -> None:
    assert_compatibility_workflow_contract(text, Path("compat.yml"), expected_platforms=_EXPECTED)


def test_exact_platform_matrix_is_accepted() -> None:
    _assert(_workflow())


def test_real_repository_compatibility_workflow_is_accepted() -> None:
    """The shipped workflow file must satisfy the semantic gate for the pinned set."""
    workflow = Path(__file__).resolve().parents[1] / ".github/workflows/opencode-compatibility.yml"
    assert_compatibility_workflow_contract(
        workflow.read_text(encoding="utf-8"),
        workflow,
        expected_platforms=_EXPECTED,
    )


def test_continue_on_error_below_the_gate_is_rejected() -> None:
    with pytest.raises(CompatibilityWorkflowError, match="must gate every pinned platform lane"):
        _assert(_workflow(job_extra="    continue-on-error: true\n"))


def test_continue_on_error_nested_in_a_step_is_rejected() -> None:
    include = _rows(list(_EXPECTED.items()))
    text = _workflow(include=include).replace(
        "      - uses: actions/checkout@pinned\n",
        "      - uses: actions/checkout@pinned\n        continue-on-error: true\n",
    )
    with pytest.raises(CompatibilityWorkflowError, match="must gate every pinned platform lane"):
        _assert(text)


@pytest.mark.parametrize("fail_fast", ["      fail-fast: true\n", ""])
def test_fail_fast_must_be_false(fail_fast: str) -> None:
    with pytest.raises(CompatibilityWorkflowError, match="fail-fast: false"):
        _assert(_workflow(fail_fast=fail_fast))


def test_duplicate_strategy_mapping_key_is_rejected() -> None:
    text = (
        "name: opencode-compatibility\n"
        "jobs:\n"
        "  cross-platform-acp:\n"
        "    strategy:\n"
        "      fail-fast: false\n"
        "    strategy:\n"
        "      matrix:\n"
        "        include: []\n"
    )
    with pytest.raises(CompatibilityWorkflowError, match="duplicates mapping key 'strategy'"):
        _assert(text)


def test_widened_matrix_axis_is_rejected() -> None:
    with pytest.raises(CompatibilityWorkflowError, match="matrix fields differ"):
        _assert(_workflow(matrix_extra="        python: ['3.12']\n"))


def test_non_array_include_is_rejected() -> None:
    text = (
        "name: opencode-compatibility\n"
        "jobs:\n"
        "  cross-platform-acp:\n"
        "    strategy:\n"
        "      fail-fast: false\n"
        "      matrix:\n"
        "        include: nope\n"
    )
    with pytest.raises(CompatibilityWorkflowError, match="matrix.include must be an array"):
        _assert(text)


def test_non_object_include_row_is_rejected() -> None:
    with pytest.raises(CompatibilityWorkflowError, match=r"include\[0\] must be an object"):
        _assert(_workflow(include="          - windows-x64"))


def test_non_string_platform_is_rejected() -> None:
    include = "          - platform: [linux-x64]\n            runner: ubuntu-24.04"
    with pytest.raises(CompatibilityWorkflowError, match=r"include\[0\].platform must be a string"):
        _assert(_workflow(include=include))


def test_non_string_runner_is_rejected() -> None:
    include = "          - platform: linux-x64\n            runner: [ubuntu-24.04]"
    with pytest.raises(CompatibilityWorkflowError, match=r"include\[0\].runner must be a string"):
        _assert(_workflow(include=include))


def test_duplicate_platform_row_is_rejected() -> None:
    pairs = list(_EXPECTED.items())
    pairs.append(("linux-x64", "ubuntu-24.04"))
    with pytest.raises(CompatibilityWorkflowError, match="duplicates platform 'linux-x64'"):
        _assert(_workflow(include=_rows(pairs)))


def test_wrong_runner_is_rejected() -> None:
    pairs = [(p, "ubuntu-24.04" if p == "windows-x64" else r) for p, r in _EXPECTED.items()]
    with pytest.raises(CompatibilityWorkflowError, match="platforms differ"):
        _assert(_workflow(include=_rows(pairs)))


def test_missing_platform_is_rejected() -> None:
    pairs = [pair for pair in _EXPECTED.items() if pair[0] != "windows-x64"]
    with pytest.raises(CompatibilityWorkflowError, match="platforms differ"):
        _assert(_workflow(include=_rows(pairs)))


def test_extra_platform_is_rejected() -> None:
    pairs = [*_EXPECTED.items(), ("freebsd-x64", "freebsd-14")]
    with pytest.raises(CompatibilityWorkflowError, match="platforms differ"):
        _assert(_workflow(include=_rows(pairs)))


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("[]\n", "compatibility workflow must be an object"),
        ("jobs: []\n", "compatibility workflow.jobs must be an object"),
        ("jobs:\n  other: {}\n", r"jobs.cross-platform-acp must be an object"),
        (
            "jobs:\n  cross-platform-acp:\n    steps: []\n",
            "cross-platform-acp strategy must be an object",
        ),
        (
            "jobs:\n  cross-platform-acp:\n    strategy:\n      fail-fast: false\n",
            "cross-platform-acp matrix must be an object",
        ),
        ("jobs: [\n", "cannot parse compatibility workflow YAML"),
    ],
)
def test_structural_shapes_fail_closed(text: str, message: str) -> None:
    with pytest.raises(CompatibilityWorkflowError, match=message):
        _assert(text)
