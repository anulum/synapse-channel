# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — commercial documentation claim hygiene regressions

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CHECKER = REPO_ROOT / "tools" / "check_commercial_claim_hygiene.py"
COMMERCIAL_DOC = REPO_ROOT / "docs" / "commercial.md"
_SPEC = importlib.util.spec_from_file_location("check_commercial_claim_hygiene", CHECKER)
assert _SPEC is not None and _SPEC.loader is not None
checker = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = checker
_SPEC.loader.exec_module(checker)


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


def test_commercial_claim_hygiene_requires_evaluation_flow(tmp_path: Path) -> None:
    drifted = tmp_path / "commercial.md"
    text = COMMERCIAL_DOC.read_text(encoding="utf-8")
    if "## Evaluation path" in text:
        start = text.index("## Evaluation path")
        end = text.index("## Claim hygiene")
        text = text[:start] + text[end:]
    drifted.write_text(text, encoding="utf-8")

    result = _run_checker("--check", "--path", str(drifted))

    assert result.returncode == 1
    assert "missing-evaluation-flow" in result.stderr


def test_scan_path_reports_missing_evaluation_flow(tmp_path: Path) -> None:
    drifted = tmp_path / "commercial.md"
    text = COMMERCIAL_DOC.read_text(encoding="utf-8")
    start = text.index("## Evaluation path")
    end = text.index("## Claim hygiene")
    drifted.write_text(text[:start] + text[end:], encoding="utf-8")

    findings = checker.scan_path(drifted)

    assert any(finding.category == "missing-evaluation-flow" for finding in findings)


def test_scan_paths_combines_findings(tmp_path: Path) -> None:
    missing = tmp_path / "commercial.md"
    split = tmp_path / "split.md"
    missing.write_text("commercial licence terms not the code\n", encoding="utf-8")
    split.write_text(
        "commercial licence terms not the code\nCommercial-only features ship later.\n",
        encoding="utf-8",
    )

    findings = checker.scan_paths((missing, split))

    assert {finding.category for finding in findings} >= {
        "missing-boundary",
        "missing-evaluation-flow",
        "feature-split-claim",
    }


def test_scan_path_ignores_unrelated_document(tmp_path: Path) -> None:
    unrelated = tmp_path / "notes.md"
    unrelated.write_text("plain project notes without licensing claims\n", encoding="utf-8")

    assert checker.scan_path(unrelated) == ()


def test_parse_args_uses_defaults_and_custom_paths(tmp_path: Path) -> None:
    custom = tmp_path / "doc.md"

    defaults = checker.parse_args(["--check"])
    custom_args = checker.parse_args(["--check", "--path", str(custom)])

    assert defaults.check is True
    assert defaults.paths == checker.DEFAULT_PATHS
    assert custom_args.paths == (custom,)


def test_main_returns_failure_for_findings(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    drifted = tmp_path / "commercial.md"
    drifted.write_text("commercial licence terms not the code\n", encoding="utf-8")

    code = checker.main(["--check", "--path", str(drifted)])

    captured = capsys.readouterr()
    assert code == 1
    assert "missing-boundary" in captured.err


def test_main_returns_success_for_clean_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clean = tmp_path / "notes.md"
    clean.write_text("plain project notes without licensing claims\n", encoding="utf-8")

    code = checker.main(["--check", "--path", str(clean)])

    captured = capsys.readouterr()
    assert code == 0
    assert "commercial claim hygiene passed" in captured.out
