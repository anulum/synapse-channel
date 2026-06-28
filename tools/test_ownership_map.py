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
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = REPO_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from synapse_channel.git import test_ownership_map as _impl  # noqa: E402
from synapse_channel.git.test_ownership_map import (  # noqa: E402,F401
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
    imported_modules,
    parse_args,
    records_to_json,
    render_human,
)

_impl.REPO_ROOT = REPO_ROOT
PACKAGE_NAME = _impl.PACKAGE_NAME
DEFAULT_SOURCE_ROOT = _impl.DEFAULT_SOURCE_ROOT
DEFAULT_TESTS_ROOT = _impl.DEFAULT_TESTS_ROOT
_fallback_source_for_test = _impl._fallback_source_for_test
_merge_owner = _impl._merge_owner
_module_name = _impl._module_name
_normalise_requested_source = _impl._normalise_requested_source
_owner_records = _impl._owner_records
_parse_python = _impl._parse_python
_relative = _impl._relative
_required_unowned = _impl._required_unowned
_select_records = _impl._select_records
_source_by_filename = _impl._source_by_filename
_source_by_module = _impl._source_by_module


def main(argv: list[str] | None = None) -> int:
    """Run the packaged ownership mapper from the checkout tool path."""
    _impl.REPO_ROOT = REPO_ROOT
    _impl.build_ownership_map = build_ownership_map
    return _impl.main(argv)


if __name__ == "__main__":
    sys.exit(main())
