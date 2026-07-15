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
import time
from dataclasses import replace

import pytest

from e2e.opencode_editors import jetbrains_client
from e2e.opencode_editors.jetbrains_timing import DEFAULT_JETBRAINS_TIMING
from e2e.opencode_editors.process_group import PROCESS_GROUP_CLEANUP_TIMEOUT_SECONDS


def test_default_timing_budget_covers_every_driver_phase_and_cleanup() -> None:
    budget = DEFAULT_JETBRAINS_TIMING

    assert budget.phase_seconds == 600.0
    assert budget.screenshot_seconds == 15.0
    assert budget.cleanup_seconds == PROCESS_GROUP_CLEANUP_TIMEOUT_SECONDS == 20.0
    assert budget.driver_budget_seconds == 635.0
    assert budget.parent_timeout_seconds == 755
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


def test_gui_commands_cannot_overrun_their_absolute_phase_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(time, "monotonic", lambda: 100.0)

    assert jetbrains_client._command_timeout(None) == 10.0
    assert jetbrains_client._command_timeout(120.0) == 10.0
    assert jetbrains_client._command_timeout(103.5) == 3.5
    with pytest.raises(RuntimeError, match="phase deadline expired"):
        jetbrains_client._command_timeout(100.0)


def test_poll_sleep_cannot_cross_the_absolute_phase_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(time, "sleep", sleeps.append)

    jetbrains_client._bounded_poll_sleep(100.0)
    jetbrains_client._bounded_poll_sleep(100.1)
    jetbrains_client._bounded_poll_sleep(101.0)

    assert sleeps == pytest.approx([0.1, 0.25])


@pytest.mark.parametrize("value", [0.0, -1.0, math.inf, math.nan])
def test_timing_budget_refuses_invalid_durations(value: float) -> None:
    with pytest.raises(ValueError, match="durations must be finite and positive"):
        replace(DEFAULT_JETBRAINS_TIMING, startup_seconds=value)
