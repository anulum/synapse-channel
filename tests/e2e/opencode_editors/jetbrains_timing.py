# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — bounded JetBrains editor acceptance timing
"""Derive one fail-closed parent timeout from every bounded IDEA phase."""

from __future__ import annotations

import math
from dataclasses import dataclass

from e2e.opencode_editors.process_group import PROCESS_GROUP_CLEANUP_TIMEOUT_SECONDS


@dataclass(frozen=True, slots=True)
class JetBrainsTimingBudget:
    """Bounded timing inputs for one real JetBrains ACP acceptance journey.

    Parameters
    ----------
    startup_seconds:
        Maximum first-run, project, and plugin-discovery phase duration.
    chat_ready_seconds:
        Maximum AI Chat readiness phase duration.
    agent_selection_seconds:
        Maximum exact ACP agent selection phase duration.
    acp_handshake_seconds:
        Maximum initialize, session creation, and plugin-readiness duration.
    acp_prompt_seconds:
        Maximum prompt submission and response duration.
    command_timeout_seconds:
        Maximum duration of one X11 command. Each command also receives its
        phase's remaining absolute deadline, so it cannot extend that phase.
    screenshot_seconds:
        Maximum final evidence screenshot duration.
    cleanup_seconds:
        Maximum graceful plus forced process-group cleanup duration.
    parent_supervision_seconds:
        Margin between the driver's derived budget and its parent timeout.
    """

    startup_seconds: float
    chat_ready_seconds: float
    agent_selection_seconds: float
    acp_handshake_seconds: float
    acp_prompt_seconds: float
    command_timeout_seconds: float
    screenshot_seconds: float
    cleanup_seconds: float
    parent_supervision_seconds: float

    def __post_init__(self) -> None:
        """Reject non-finite, non-positive, or incomplete timing budgets."""
        durations = (
            self.startup_seconds,
            self.chat_ready_seconds,
            self.agent_selection_seconds,
            self.acp_handshake_seconds,
            self.acp_prompt_seconds,
            self.command_timeout_seconds,
            self.screenshot_seconds,
            self.cleanup_seconds,
            self.parent_supervision_seconds,
        )
        if any(not math.isfinite(value) or value <= 0 for value in durations):
            raise ValueError("JetBrains timing durations must be finite and positive")

    @property
    def phase_seconds(self) -> float:
        """Return the sum of all explicitly bounded editor phases."""
        return (
            self.startup_seconds
            + self.chat_ready_seconds
            + self.agent_selection_seconds
            + self.acp_handshake_seconds
            + self.acp_prompt_seconds
        )

    @property
    def driver_budget_seconds(self) -> float:
        """Return the complete driver budget including evidence and cleanup."""
        return self.phase_seconds + self.screenshot_seconds + self.cleanup_seconds

    @property
    def parent_timeout_seconds(self) -> int:
        """Return the parent timeout with a separate supervision margin."""
        return math.ceil(self.driver_budget_seconds + self.parent_supervision_seconds)


DEFAULT_JETBRAINS_TIMING = JetBrainsTimingBudget(
    startup_seconds=300.0,
    chat_ready_seconds=90.0,
    agent_selection_seconds=90.0,
    acp_handshake_seconds=180.0,
    acp_prompt_seconds=90.0,
    command_timeout_seconds=10.0,
    screenshot_seconds=15.0,
    cleanup_seconds=PROCESS_GROUP_CLEANUP_TIMEOUT_SECONDS,
    parent_supervision_seconds=120.0,
)
