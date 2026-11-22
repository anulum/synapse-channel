#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — source-to-test ownership map wrapper
"""Build a deterministic map from source modules to likely owning tests."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = REPO_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from synapse_channel.git import test_ownership_map as _impl
from synapse_channel.git.test_ownership_map import (
    CliArgs,
    ImportedModule,
    OwnershipRecord,
    PendingOwner,
    SourceModule,
    TestOwner,
    build_ownership_map,
    defined_symbols,
    discover_sources,
    discover_tests,
    fallback_source_for_test,
    imported_modules,
    records_to_json,
    render_human,
    required_unowned,
    select_records,
)

PACKAGE_NAME = _impl.PACKAGE_NAME
DEFAULT_SOURCE_ROOT = _impl.DEFAULT_SOURCE_ROOT
DEFAULT_TESTS_ROOT = _impl.DEFAULT_TESTS_ROOT

__all__ = [
    "CliArgs",
    "ImportedModule",
    "OwnershipRecord",
    "PendingOwner",
    "SourceModule",
    "TestOwner",
    "build_ownership_map",
    "defined_symbols",
    "discover_sources",
    "discover_tests",
    "fallback_source_for_test",
    "imported_modules",
    "main",
    "parse_args",
    "records_to_json",
    "render_human",
    "required_unowned",
    "select_records",
]


def parse_args(argv: Sequence[str] | None = None) -> CliArgs:
    """Parse checkout arguments with its default root, leaving the packaged default intact."""
    return _impl.parse_args(argv, default_root=REPO_ROOT)


def main(argv: list[str] | None = None) -> int:
    """Run the packaged ownership mapper from the checkout tool path."""
    return _impl.main(argv, default_root=REPO_ROOT, builder=build_ownership_map)


if __name__ == "__main__":
    sys.exit(main())
