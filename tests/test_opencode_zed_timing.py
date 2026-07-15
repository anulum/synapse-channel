# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — derived Zed acceptance timing contracts
"""Verify every Zed phase contributes to one bounded parent timeout."""

from __future__ import annotations

import math

import pytest

from e2e.opencode_editors.zed_timing import DEFAULT_ZED_TIMING, ZedTimingBudget


def _budget(**overrides: float) -> ZedTimingBudget:
    """Return a small complete timing budget with selected replacements."""
    values = {
        "startup_seconds": 1.0,
        "acp_session_seconds": 2.0,
        "acp_prompt_seconds": 3.0,
        "command_timeout_seconds": 4.0,
        "screenshot_seconds": 5.0,
        "leader_term_seconds": 6.0,
        "leader_kill_seconds": 7.0,
        "group_cleanup_seconds": 8.0,
        "parent_supervision_seconds": 9.1,
    }
    values.update(overrides)
    return ZedTimingBudget(**values)


def test_zed_parent_timeout_is_derived_from_every_bounded_phase() -> None:
    budget = _budget()
    assert budget.phase_seconds == 6.0
    assert budget.driver_budget_seconds == 29.0
    assert budget.parent_timeout_seconds == 47
    assert DEFAULT_ZED_TIMING.phase_seconds == 180.0
    assert DEFAULT_ZED_TIMING.driver_budget_seconds == 225.0
    assert DEFAULT_ZED_TIMING.parent_timeout_seconds == 305


@pytest.mark.parametrize(
    "field",
    [
        "startup_seconds",
        "acp_session_seconds",
        "acp_prompt_seconds",
        "command_timeout_seconds",
        "screenshot_seconds",
        "leader_term_seconds",
        "leader_kill_seconds",
        "group_cleanup_seconds",
        "parent_supervision_seconds",
    ],
)
@pytest.mark.parametrize("value", [0.0, -1.0, math.inf, math.nan])
def test_zed_timing_rejects_unbounded_or_nonpositive_duration(
    field: str,
    value: float,
) -> None:
    with pytest.raises(ValueError, match="finite and positive"):
        _budget(**{field: value})
