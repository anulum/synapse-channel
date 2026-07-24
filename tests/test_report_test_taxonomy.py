# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for tools/report_test_taxonomy.py
"""Behaviour of the advisory test-taxonomy inventory tool."""

from __future__ import annotations

from pathlib import Path

from tools.report_test_taxonomy import inventory_test_taxonomy, main


def test_inventory_counts_design_docs_and_docs_contract(tmp_path: Path) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_feature_design_docs.py").write_text(
        "import pytest\npytestmark = pytest.mark.docs_contract\n\ndef test_a():\n    assert True\n",
        encoding="utf-8",
    )
    (tests / "test_cli_e2e_journey.py").write_text(
        "def test_b():\n    assert True\n",
        encoding="utf-8",
    )
    (tests / "test_real.py").write_text(
        "import pytest\npytestmark = pytest.mark.real_hub\n\ndef test_c():\n    assert True\n",
        encoding="utf-8",
    )
    counts = inventory_test_taxonomy(tests)
    assert counts["test_modules"] == 3
    assert counts["docs_contract_modules"] == 1
    assert counts["real_hub_modules"] == 1
    assert counts["design_docs_named"] == 1
    assert counts["e2e_named"] == 1


def test_main_prints_inventory_for_repo_tests() -> None:
    assert main(["--tests-root", "tests"]) == 0


def test_main_missing_root_exits_2(tmp_path: Path) -> None:
    assert main(["--tests-root", str(tmp_path / "nope")]) == 2
