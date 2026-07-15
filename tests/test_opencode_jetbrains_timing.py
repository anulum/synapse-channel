# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — JetBrains editor acceptance timing contract tests
"""Lock every bounded IDEA phase into one fail-closed parent timeout."""

from __future__ import annotations

import math
from dataclasses import replace

import pytest

from e2e.opencode_editors import jetbrains_client
from e2e.opencode_editors.jetbrains_timing import DEFAULT_JETBRAINS_TIMING
from e2e.opencode_editors.process_group import PROCESS_GROUP_CLEANUP_TIMEOUT_SECONDS


def test_default_timing_budget_covers_every_driver_phase_and_cleanup() -> None:
    budget = DEFAULT_JETBRAINS_TIMING

    assert budget.phase_seconds == 600.0
    assert budget.prompt_submission_seconds == 90.0
    assert budget.phase_overhang_seconds == 180.0
    assert budget.screenshot_seconds == 15.0
    assert budget.cleanup_seconds == PROCESS_GROUP_CLEANUP_TIMEOUT_SECONDS == 20.0
    assert budget.driver_budget_seconds == 905.0
    assert budget.parent_timeout_seconds == 1025
    assert budget.parent_timeout_seconds - budget.driver_budget_seconds == 120.0


def test_driver_consumes_the_shared_timing_budget() -> None:
    budget = DEFAULT_JETBRAINS_TIMING

    assert jetbrains_client._STARTUP_TIMEOUT_SECONDS == budget.startup_seconds
    assert jetbrains_client._CHAT_READY_TIMEOUT_SECONDS == budget.chat_ready_seconds
    assert jetbrains_client._AGENT_SELECTION_TIMEOUT_SECONDS == budget.agent_selection_seconds
    assert jetbrains_client._ACP_HANDSHAKE_TIMEOUT_SECONDS == budget.acp_handshake_seconds
    assert jetbrains_client._ACP_PROMPT_TIMEOUT_SECONDS == budget.acp_prompt_seconds
    assert jetbrains_client._GUI_COMMAND_TIMEOUT_SECONDS == budget.command_timeout_seconds
    assert jetbrains_client._SCREENSHOT_TIMEOUT_SECONDS == budget.screenshot_seconds


@pytest.mark.parametrize("value", [0.0, -1.0, math.inf, math.nan])
def test_timing_budget_refuses_invalid_durations(value: float) -> None:
    with pytest.raises(ValueError, match="durations must be finite and positive"):
        replace(DEFAULT_JETBRAINS_TIMING, startup_seconds=value)


@pytest.mark.parametrize(
    "field",
    ["prompt_submission_commands", "phase_overhang_commands"],
)
def test_timing_budget_refuses_invalid_command_counts(field: str) -> None:
    values = {
        "prompt_submission_commands": DEFAULT_JETBRAINS_TIMING.prompt_submission_commands,
        "phase_overhang_commands": DEFAULT_JETBRAINS_TIMING.phase_overhang_commands,
    }
    values[field] = 0

    with pytest.raises(ValueError, match="command counts must be positive"):
        replace(DEFAULT_JETBRAINS_TIMING, **values)
