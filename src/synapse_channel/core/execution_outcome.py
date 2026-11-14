# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — scoped execution observations, owned by the core
"""Share execution truth without equating a transport result with an external effect.

The record type lives in the core because the hub validates and journals
outcome reports (operation records). :mod:`synapse_channel.execution_outcome`
re-exports it unchanged for the edges.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from synapse_channel.core.member_text import MemberText

OutcomeBoundary = Literal[
    "a2a_exchange", "mcp_exchange", "app_task_store", "wake_transport", "worker_process"
]
"""The concrete execution boundary an observation describes."""

OUTCOME_SCHEMA = "synapse.execution-outcome.v1"
"""Version of the additive observation envelope; never an authority grant."""


class OutcomeState(MemberText, StrEnum):
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


_BOUNDARIES: tuple[OutcomeBoundary, ...] = (
    "a2a_exchange",
    "mcp_exchange",
    "app_task_store",
    "wake_transport",
    "worker_process",
)


def _text(document: Mapping[str, object], key: str) -> str:
    value = document.get(key)
    if not isinstance(value, str):
        raise ValueError(f"outcome report field {key} must be text")
    return value


def _flag(document: Mapping[str, object], key: str) -> bool:
    value = document.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"outcome report field {key} must be a boolean")
    return value


def _state(value: object) -> OutcomeState:
    if not isinstance(value, str):
        raise ValueError("outcome report state must be text")
    try:
        return OutcomeState(value)
    except ValueError:
        raise ValueError("outcome report state is not one of the five states") from None


def _boundary(value: object) -> OutcomeBoundary:
    for boundary in _BOUNDARIES:
        if value == boundary:
            return boundary
    raise ValueError("unknown execution outcome boundary")


def outcome_from_json(document: object) -> ExecutionOutcome:
    """Decode one ``synapse.execution-outcome.v1`` report into its record.

    Only the fields of :class:`ExecutionOutcome` are read; derived fields such
    as ``recovery`` and producer extras such as ``returncode`` are ignored, so
    the decoded record carries no more than the report's observation.

    Parameters
    ----------
    document : object
        The decoded JSON report.

    Returns
    -------
    ExecutionOutcome
        The validated record.

    Raises
    ------
    ValueError
        If the document is not such a report, a field has the wrong type, or the
        record's own rules refuse it. Rejected values are not echoed.
    """
    if not isinstance(document, Mapping):
        raise ValueError("an outcome report must be a JSON object")
    if document.get("schema") != OUTCOME_SCHEMA:
        raise ValueError(f"the report is not a {OUTCOME_SCHEMA} record")
    return ExecutionOutcome(
        _text(document, "operation_id"),
        _boundary(document.get("boundary")),
        _state(document.get("state")),
        _text(document, "observation"),
        external_effect=_state(document.get("external_effect")),
        stop_requested=_flag(document, "stop_requested"),
        stop_confirmed=_flag(document, "stop_confirmed"),
        effects_undone=_flag(document, "effects_undone"),
    )
