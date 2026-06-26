# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — enforce focused A2A module coverage ratchets
"""Validate module-specific coverage for the A2A bridge surface."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

TARGETS = {
    "src/synapse_channel/a2a_server.py": 100.0,
    "src/synapse_channel/cli_a2a.py": 100.0,
    "src/synapse_channel/a2a_events.py": 100.0,
    "src/synapse_channel/a2a_store.py": 100.0,
}


def evaluate_report(
    report: Mapping[str, Any],
    *,
    targets: Mapping[str, float] = TARGETS,
) -> list[str]:
    """Return module coverage failures from a coverage.py JSON report."""
    raw_files = report.get("files", {})
    files = raw_files if isinstance(raw_files, Mapping) else {}
    failures: list[str] = []
    for module_path, required in targets.items():
        raw_entry = files.get(module_path)
        entry = raw_entry if isinstance(raw_entry, Mapping) else {}
        raw_summary = entry.get("summary", {})
        summary = raw_summary if isinstance(raw_summary, Mapping) else {}
        percent = float(summary.get("percent_covered", 0.0))
        if percent < required:
            failures.append(f"{module_path}: {percent:.2f}% < required {required:.2f}%")
    return failures


def _load_report(path: Path) -> Mapping[str, Any]:
    """Load a coverage.py JSON report."""
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, Mapping):
        raise ValueError(f"coverage report must be a JSON object: {path}")
    return data


def main(argv: list[str] | None = None) -> int:
    """Run the A2A module coverage ratchet check."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "coverage_json",
        type=Path,
        help="Path to a coverage.py JSON report produced by `coverage json`.",
    )
    args = parser.parse_args(argv)
    failures = evaluate_report(_load_report(args.coverage_json))
    if failures:
        print("A2A module coverage ratchet failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
