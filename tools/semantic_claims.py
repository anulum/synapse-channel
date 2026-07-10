#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — semantic claim selector resolver wrapper
"""Resolve semantic claim selectors into canonical path claim scopes."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = REPO_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from synapse_channel.git import semantic_claims as _impl  # noqa: E402
from synapse_channel.git.semantic_claims import (  # noqa: E402,F401
    CliArgs,
    ParsedSelector,
    SemanticClaimRecord,
    companion_claim_paths,
    parse_args,
    parse_selector,
    records_to_json,
    render_claim_args,
    render_human,
    resolve_selectors,
)

_impl.REPO_ROOT = REPO_ROOT

SEMANTIC_SELECTOR_KINDS = _impl.SEMANTIC_SELECTOR_KINDS


def main(argv: list[str] | None = None) -> int:
    """Run the packaged semantic claim resolver from the checkout tool path."""
    _impl.REPO_ROOT = REPO_ROOT
    return _impl.main(argv, resolver=resolve_selectors)


if __name__ == "__main__":
    sys.exit(main())
