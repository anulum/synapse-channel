# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — semantic OpenCode editor workflow contract tests
"""Exercise the isolated duplicate-safe editor workflow release gate."""

from __future__ import annotations

from pathlib import Path

import pytest
from tools.opencode_editor_workflow_contract import (
    EditorWorkflowError,
    assert_editor_workflow_contract,
)

_LANES = frozenset({"neovim", "emacs", "zed", "jetbrains"})


def _workflow(matrix: str | None = None, *, job_extra: str = "") -> str:
    matrix_body = (
        matrix
        or """include:
          - client: neovim
            timeout: 20
          - client: emacs
            timeout: 20
          - client: zed
            timeout: 30
          - client: jetbrains
            timeout: 45"""
    )
    return f"""name: editor-e2e
jobs:
  editor-client:
{job_extra}    strategy:
      matrix:
        {matrix_body}
    steps:
      - uses: actions/checkout@pinned
"""


def _assert(text: str) -> None:
    assert_editor_workflow_contract(text, Path("editor.yml"), expected_lanes=_LANES)


def test_exact_include_only_matrix_is_accepted() -> None:
    _assert(_workflow())


def test_editor_workflow_executes_the_semantic_contract_directly() -> None:
    workflow = Path(__file__).resolve().parents[1] / ".github/workflows/opencode-editor-e2e.yml"
    text = workflow.read_text(encoding="utf-8")

    assert "python -m tools.opencode_compatibility_contract --check" in text
    assert "pytest tests/test_opencode_editor_workflow_contract.py" in text


def test_continue_on_error_is_rejected_at_any_nested_depth() -> None:
    text = _workflow().replace(
        "      - uses: actions/checkout@pinned",
        "      - continue-on-error: true\n        uses: actions/checkout@pinned",
    )
    with pytest.raises(EditorWorkflowError, match="must gate every pinned real-client lane"):
        _assert(text)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("[]\n", "editor workflow must be an object"),
        ("jobs: []\n", "editor workflow.jobs must be an object"),
        (
            "jobs:\n  editor-client:\n    strategy: {}\n    strategy:\n      matrix: {}\n",
            "duplicates mapping key 'strategy'",
        ),
        (
            "jobs:\n  editor-client:\n    ? [unhashable]\n    : true\n"
            "    strategy:\n      matrix: {}\n",
            "mapping keys must be hashable",
        ),
        ("jobs: [\n", "cannot parse editor workflow YAML"),
        (
            _workflow("client: [vscode]\n        include: []"),
            "matrix fields differ",
        ),
        (_workflow("include: neovim"), "matrix.include must be an array"),
        (_workflow("include:\n          - neovim"), r"include\[0\] must be an object"),
        (
            _workflow("include:\n          - client: [neovim]"),
            r"include\[0\].client must be a string",
        ),
        (
            _workflow(
                """include:
          - client: neovim
          - client: emacs
          - client: zed
          - client: vscode"""
            ),
            "matrix lanes differ",
        ),
    ],
)
def test_invalid_workflow_shapes_fail_closed(text: str, message: str) -> None:
    with pytest.raises(EditorWorkflowError, match=message):
        _assert(text)
