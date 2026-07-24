#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — cheap real-path / docs-contract test inventory for CI honesty
"""Report pytest taxonomy counts without executing tests.

Prints how many test modules carry ``docs_contract`` / ``real_hub`` markers and
how many file names suggest design-doc guards versus e2e journeys. Intended as
an advisory honesty metric so audits do not rely on ad-hoc greps alone.

Exit code is always ``0`` on successful inventory (never a suite gate).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path


def inventory_test_taxonomy(tests_root: Path) -> dict[str, int]:
    """Return taxonomy counts for modules under ``tests_root``."""
    modules = sorted(tests_root.rglob("test_*.py"))
    docs_contract_modules = 0
    real_hub_modules = 0
    design_docs_named = 0
    e2e_named = 0
    for path in modules:
        text = path.read_text(encoding="utf-8", errors="replace")
        if "pytest.mark.docs_contract" in text or "mark.docs_contract" in text:
            docs_contract_modules += 1
        if "pytest.mark.real_hub" in text or "mark.real_hub" in text:
            real_hub_modules += 1
        name = path.name
        if name.endswith("_design_docs.py") or "_design_docs_" in name:
            design_docs_named += 1
        if "_e2e_" in name or name.endswith("_e2e.py") or name.startswith("test_e2e_"):
            e2e_named += 1
    return {
        "test_modules": len(modules),
        "docs_contract_modules": docs_contract_modules,
        "real_hub_modules": real_hub_modules,
        "design_docs_named": design_docs_named,
        "e2e_named": e2e_named,
    }


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry: print taxonomy counts for the repository tests tree."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tests-root",
        type=Path,
        default=Path("tests"),
        help="Tests directory to scan (default: ./tests).",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    root = args.tests_root
    if not root.is_dir():
        print(f"tests root not found: {root}", file=sys.stderr)
        return 2
    counts = inventory_test_taxonomy(root)
    print("test taxonomy inventory (file scan; not executed):")
    for key in (
        "test_modules",
        "docs_contract_modules",
        "real_hub_modules",
        "design_docs_named",
        "e2e_named",
    ):
        print(f"  {key}: {counts[key]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
