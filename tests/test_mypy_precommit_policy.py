# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — policy contract for the whole-tree mypy hook
"""Keep local and remote mypy hook execution whole-tree and environment-aligned."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / ".pre-commit-config.yaml"
WORKFLOW = ROOT / ".github" / "workflows" / "pre-commit.yml"


def test_mypy_hook_cannot_narrow_to_staged_filenames() -> None:
    text = CONFIG.read_text(encoding="utf-8")
    block = text.split("- id: mypy-whole-tree", 1)[1].split("\n      - id:", 1)[0]

    assert "entry: python tools/run_mypy_hook.py" in block
    assert "language: system" in block
    assert "stages: [pre-commit]" in block
    assert "pass_filenames: false" in block
    assert "pyproject\\.toml" in block
    for surface in ("src", "tests", "benchmarks", "tools", "examples"):
        assert surface in block


def test_precommit_ci_installs_the_whole_tree_type_environment() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "--require-hashes -r .github/requirements/requirements-dev.txt" in text
    assert "python -m pip install -e . --no-deps" in text
    assert "python -m pre_commit run --all-files --show-diff-on-failure" in text
    assert "requirements-tools.txt" not in text
