# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — OpenCode editor governance environment contract
"""Verify deterministic imports for isolated real-editor child processes."""

from __future__ import annotations

import os
from pathlib import Path

from e2e.opencode_editors.governance_contract import source_environment


def test_source_environment_uses_absolute_checkout_roots() -> None:
    """Replace inherited paths with the source and fixture roots in this checkout."""
    environment = {
        "PYTHONPATH": "relative-injection",
        "FORCE_COLOR": "1",
        "RETAINED": "value",
    }

    result = source_environment(environment)

    roots = tuple(Path(entry) for entry in result["PYTHONPATH"].split(os.pathsep))
    repository = Path(__file__).resolve().parents[1]
    assert roots == (repository / "src", repository / "tests")
    assert all(root.is_absolute() for root in roots)
    assert "FORCE_COLOR" not in result
    assert result["RETAINED"] == "value"
    assert result is environment
