# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the typed task-status lifecycle

from __future__ import annotations

import pytest

from synapse_channel.core.lifecycle import (
    TaskStatus,
    can_transition,
    is_known,
    is_terminal,
)


def test_status_values_are_stable_wire_strings() -> None:
    assert TaskStatus.CLAIMED == "claimed"
    assert TaskStatus.INPUT_REQUIRED == "input_required"
    assert TaskStatus.FAILED == "failed"


@pytest.mark.parametrize(
    ("status", "known"),
    [("claimed", True), ("done", True), ("nonsense", False), ("", False)],
)
def test_is_known(status: str, known: bool) -> None:
    assert is_known(status) is known


@pytest.mark.parametrize(
    ("status", "terminal"),
    [("done", True), ("failed", True), ("working", False), ("claimed", False)],
)
def test_is_terminal(status: str, terminal: bool) -> None:
    assert is_terminal(status) is terminal


def test_legal_forward_transitions() -> None:
    assert can_transition("claimed", "working") is True
    assert can_transition("working", "input_required") is True
    assert can_transition("input_required", "working") is True
    assert can_transition("working", "done") is True
    assert can_transition("claimed", "failed") is True


def test_self_transition_is_allowed() -> None:
    assert can_transition("working", "working") is True


def test_terminal_states_cannot_transition() -> None:
    assert can_transition("done", "working") is False
    assert can_transition("failed", "claimed") is False


def test_transition_to_unknown_status_is_rejected() -> None:
    assert can_transition("working", "nonsense") is False


def test_illegal_skip_is_rejected() -> None:
    # input_required is not reachable directly from claimed.
    assert can_transition("claimed", "input_required") is False
