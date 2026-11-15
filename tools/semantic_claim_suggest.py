#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — intent-driven semantic claim suggestion wrapper
"""Suggest file-scope claim paths from a free-text intent."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = REPO_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from synapse_channel.git import semantic_claim_suggest as _impl
from synapse_channel.git.semantic_claim_suggest import (
    CliArgs,
    SuggestedPath,
    parse_args,
    render_draft_claim,
    render_human,
    render_json,
    suggest_paths,
)

# Callers import these names from the checkout wrapper.
__all__ = (
    "CliArgs",
    "DEFAULT_LIMIT",
    "REPO_ROOT",
    "SRC_ROOT",
    "SuggestedPath",
    "main",
    "parse_args",
    "render_draft_claim",
    "render_human",
    "render_json",
    "suggest_paths",
)

_impl.REPO_ROOT = REPO_ROOT

DEFAULT_LIMIT = _impl.DEFAULT_LIMIT


def main(argv: list[str] | None = None) -> int:
    """Run the packaged semantic claim suggester from the checkout tool path."""
    _impl.REPO_ROOT = REPO_ROOT
    return _impl.main(argv, suggest=suggest_paths)


if __name__ == "__main__":
    sys.exit(main())
