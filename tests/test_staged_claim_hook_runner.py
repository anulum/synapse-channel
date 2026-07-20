# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — staged claim hook interpreter and CLI integration tests

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "tools" / "run_staged_claim_hook.py"


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)


def test_hook_runner_uses_the_installed_interpreter_for_an_empty_index(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    environment = os.environ.copy()
    environment["SYNAPSE_STAGED_CLAIM_PYTHON"] = sys.executable

    result = subprocess.run(
        [sys.executable, str(RUNNER)],
        cwd=repo,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "staged claim coverage: no staged paths"
    assert result.stderr == ""


def test_hook_runner_refuses_a_missing_interpreter(tmp_path: Path) -> None:
    environment = os.environ.copy()
    missing = tmp_path / "missing-python"
    environment["SYNAPSE_STAGED_CLAIM_PYTHON"] = str(missing)

    result = subprocess.run(
        [sys.executable, str(RUNNER)],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert str(missing) in result.stderr
