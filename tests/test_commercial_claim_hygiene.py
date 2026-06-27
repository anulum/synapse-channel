# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — commercial documentation claim hygiene regressions

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CHECKER = REPO_ROOT / "tools" / "check_commercial_claim_hygiene.py"
COMMERCIAL_DOC = REPO_ROOT / "docs" / "commercial.md"


def _run_checker(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CHECKER), *args],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def test_commercial_claim_hygiene_passes_current_repository() -> None:
    result = _run_checker("--check")

    assert result.returncode == 0, result.stderr + result.stdout
    assert "commercial claim hygiene passed" in result.stdout


def test_commercial_claim_hygiene_detects_missing_boundary_text(tmp_path: Path) -> None:
    drifted = tmp_path / "commercial.md"
    drifted.write_text(
        COMMERCIAL_DOC.read_text(encoding="utf-8").replace(
            "There is **no feature difference between the\n"
            "open-source and the commercial build** — the package on PyPI is the full product; a\n"
            "commercial licence changes the terms, not the code.\n",
            "",
        ),
        encoding="utf-8",
    )

    result = _run_checker("--check", "--path", str(drifted))

    assert result.returncode == 1
    assert "missing-boundary" in result.stderr
    assert "no feature difference" in result.stderr


def test_commercial_claim_hygiene_detects_feature_split_claim(tmp_path: Path) -> None:
    drifted = tmp_path / "commercial.md"
    drifted.write_text(
        COMMERCIAL_DOC.read_text(encoding="utf-8")
        + "\nCommercial-only features include hosted dashboards and policy controls.\n",
        encoding="utf-8",
    )

    result = _run_checker("--check", "--path", str(drifted))

    assert result.returncode == 1
    assert "feature-split-claim" in result.stderr
