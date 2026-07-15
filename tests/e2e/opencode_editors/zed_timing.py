# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — bounded Zed editor acceptance timing
"""Derive one fail-closed parent timeout from every bounded Zed phase."""

from __future__ import annotations

import math
from dataclasses import dataclass

from e2e.opencode_editors.process_group import PROCESS_GROUP_CLEANUP_TIMEOUT_SECONDS


@dataclass(frozen=True, slots=True)
class ZedTimingBudget:
    """Bounded timing inputs for one real Zed ACP acceptance journey.

    Parameters
    ----------
    startup_seconds:
        Maximum launch, owned-window discovery, trust, and agent-open duration.
    acp_session_seconds:
        Maximum ACP session-creation duration after the agent action.
    acp_prompt_seconds:
        Maximum prompt input, submission, delivery, and response duration.
    command_timeout_seconds:
        Maximum duration of one X11 command. Every command also receives its
        phase's remaining absolute deadline and cannot extend that phase.
    screenshot_seconds:
        Maximum duration of each evidence capture. A failed primary capture
        can be followed by one cleanup capture.
    leader_term_seconds:
        Maximum direct graceful wait for the foreground Zed leader.
    leader_kill_seconds:
        Maximum direct wait after forced leader termination.
    group_cleanup_seconds:
        Time reserved by the parent for the driver, editor, proxy, and helper
        process group to exit.
    parent_supervision_seconds:
        Margin between the complete derived journey and forced supervision.
    """

    startup_seconds: float
    acp_session_seconds: float
    acp_prompt_seconds: float
    command_timeout_seconds: float
    screenshot_seconds: float
    leader_term_seconds: float
    leader_kill_seconds: float
    group_cleanup_seconds: float
    parent_supervision_seconds: float

    def __post_init__(self) -> None:
        """Reject non-finite, non-positive, or incomplete timing budgets."""
        durations = (
            self.startup_seconds,
            self.acp_session_seconds,
            self.acp_prompt_seconds,
            self.command_timeout_seconds,
            self.screenshot_seconds,
            self.leader_term_seconds,
            self.leader_kill_seconds,
            self.group_cleanup_seconds,
            self.parent_supervision_seconds,
        )
        if any(not math.isfinite(value) or value <= 0 for value in durations):
            raise ValueError("Zed timing durations must be finite and positive")

    @property
    def phase_seconds(self) -> float:
        """Return the sum of the three absolute editor phases."""
        return self.startup_seconds + self.acp_session_seconds + self.acp_prompt_seconds

    @property
    def driver_budget_seconds(self) -> float:
        """Return the complete in-driver phase, evidence, and cleanup budget."""
        return (
            self.phase_seconds
            + (2 * self.screenshot_seconds)
            + self.leader_term_seconds
            + self.leader_kill_seconds
        )

    @property
    def parent_timeout_seconds(self) -> int:
        """Return the total parent timeout including group cleanup and margin."""
        return math.ceil(
            self.driver_budget_seconds
            + self.group_cleanup_seconds
            + self.parent_supervision_seconds
        )


DEFAULT_ZED_TIMING = ZedTimingBudget(
    startup_seconds=60.0,
    acp_session_seconds=60.0,
    acp_prompt_seconds=60.0,
    command_timeout_seconds=10.0,
    screenshot_seconds=15.0,
    leader_term_seconds=10.0,
    leader_kill_seconds=5.0,
    group_cleanup_seconds=PROCESS_GROUP_CLEANUP_TIMEOUT_SECONDS,
    parent_supervision_seconds=60.0,
)
