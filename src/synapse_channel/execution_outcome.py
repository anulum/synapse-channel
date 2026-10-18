# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — scoped execution observations
"""Share execution truth without equating a transport result with an external effect."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal

OutcomeBoundary = Literal[
    "a2a_exchange", "mcp_exchange", "app_task_store", "wake_transport", "worker_process"
]
"""The concrete execution boundary an observation describes."""

OUTCOME_SCHEMA = "synapse.execution-outcome.v1"
"""Version of the additive observation envelope; never an authority grant."""


class OutcomeState(str, Enum):
    """Five states of an execution, independent of protocol-specific lifecycle."""

    NOT_STARTED = "not_started"
    STARTED = "started"
    CONFIRMED_DONE = "confirmed_done"
    CONFIRMED_FAILED = "confirmed_failed"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ExecutionOutcome:
    """Describe one scoped observation and its independently known stop facts.

    State concerns only the named boundary. External effect concerns requested
    work beyond it: socket submission, a reply or process exit alone cannot
    verify that work. False stop/undo fields mean no confirmation is supplied,
    rather than proof that the opposite happened. Reports are observations;
    they do not issue execution authority.
    """

    operation_id: str
    boundary: OutcomeBoundary
    state: OutcomeState
    observation: str
    external_effect: OutcomeState = OutcomeState.UNKNOWN
    stop_requested: bool = False
    stop_confirmed: bool = False
    effects_undone: bool = False

    def __post_init__(self) -> None:
        """Reject ambiguous or unbounded observations before they are emitted."""
        for value in (self.operation_id, self.observation):
            if not isinstance(value, str) or not value or len(value) > 512:
                raise ValueError("outcome identity and observation require bounded text")
            if any(ord(char) < 32 for char in value):
                raise ValueError("outcome text cannot contain control characters")
        if self.boundary not in {
            "a2a_exchange",
            "mcp_exchange",
            "app_task_store",
            "wake_transport",
            "worker_process",
        }:
            raise ValueError("unknown execution outcome boundary")
        if not isinstance(self.state, OutcomeState) or not isinstance(
            self.external_effect, OutcomeState
        ):
            raise ValueError("execution outcomes require explicit states")
        for flag in (self.stop_requested, self.stop_confirmed, self.effects_undone):
            if not isinstance(flag, bool):
                raise ValueError("stop and undo observations require booleans")
        if self.stop_confirmed and not self.stop_requested:
            raise ValueError("a stop confirmation requires an actual stop request")

    def to_json(self) -> dict[str, object]:
        """Return a bounded machine-readable report with no automatic replay."""
        recovery = "none"
        if self.state is OutcomeState.UNKNOWN:
            recovery = "inquire_or_operator"
        elif self.state is OutcomeState.STARTED:
            recovery = "await_observation"
        return {
            "schema": OUTCOME_SCHEMA,
            "operation_id": self.operation_id,
            "boundary": self.boundary,
            "state": self.state.value,
            "observation": self.observation,
            "external_effect": self.external_effect.value,
            "stop_requested": self.stop_requested,
            "stop_confirmed": self.stop_confirmed,
            "effects_undone": self.effects_undone,
            "automatic_retry": False,
            "recovery": recovery,
            "external_recovery": (
                "inquire_or_operator"
                if self.external_effect is OutcomeState.UNKNOWN
                else "await_observation"
                if self.external_effect is OutcomeState.STARTED
                else "none"
            ),
        }
