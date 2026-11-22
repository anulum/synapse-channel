#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — generated-output dependency claim mapper wrapper
"""Map generated repository outputs back to inputs that can stale them."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = REPO_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from synapse_channel.git import generated_dependency_claims as _impl
from synapse_channel.git.generated_dependency_claims import (
    CAPABILITY_DEPENDENCIES,
    CAPABILITY_REQUIRED_DEPENDENCIES,
    DEFAULT_RULES,
    CliArgs,
    DependencyRecord,
    GeneratedRule,
    build_dependency_map,
    check_records,
    normalise_requested_path,
    records_to_json,
    render_claim_args,
    render_human,
    select_records,
)

# Callers import these names from the checkout wrapper.
__all__ = (
    "CAPABILITY_DEPENDENCIES",
    "CAPABILITY_REQUIRED_DEPENDENCIES",
    "CliArgs",
    "DEFAULT_RULES",
    "DependencyRecord",
    "GeneratedRule",
    "REPO_ROOT",
    "SRC_ROOT",
    "build_dependency_map",
    "check_records",
    "main",
    "normalise_requested_path",
    "parse_args",
    "records_to_json",
    "render_claim_args",
    "render_human",
    "select_records",
)


def parse_args(argv: Sequence[str] | None = None) -> CliArgs:
    """Parse checkout arguments with its default root, leaving the packaged default intact."""
    return _impl.parse_args(argv, default_root=REPO_ROOT)


def main(argv: list[str] | None = None) -> int:
    """Run the packaged generated dependency mapper from the checkout tool path."""
    return _impl.main(argv, default_root=REPO_ROOT, builder=build_dependency_map)


if __name__ == "__main__":
    sys.exit(main())
