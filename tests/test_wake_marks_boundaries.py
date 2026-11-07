# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — wake mark boundary refusals
"""Refuse a mark that cannot name one wake, and keep a broken outcome uncertain."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from synapse_channel import wake_marks
from synapse_channel.wake_adapters import UNCERTAIN

SEAT = "PROJECT/seat-marks"
WHEN = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)


def test_a_wake_id_must_be_a_non_negative_whole_number(tmp_path: Path) -> None:
    """A boolean, a negative id, and a non-number never create a mark."""
    marks = tmp_path / "marks"
    for wake_id in (True, -1):
        with pytest.raises(wake_marks.WakeMarkError, match="whole number"):
            wake_marks.begin(marks, SEAT, wake_id, "pane_bridge", now=WHEN)
    assert not marks.exists()


def test_a_blank_seat_is_refused_before_any_directory_is_created(tmp_path: Path) -> None:
    """Begin and the seat lock both refuse a seat that is only whitespace."""
    marks = tmp_path / "marks"
    with pytest.raises(wake_marks.WakeMarkError, match="needs a seat"):
        wake_marks.begin(marks, "  ", 1, "pane_bridge", now=WHEN)
    with pytest.raises(wake_marks.WakeMarkError, match="needs a seat"):
        with wake_marks.seat_lock(marks, ""):
            raise AssertionError("the lock must not be entered")
    assert not marks.exists()


def test_an_adapter_kind_must_fit_a_mark_name(tmp_path: Path) -> None:
    """An adapter kind outside the mark-name pattern is refused."""
    with pytest.raises(wake_marks.WakeMarkError, match="adapter kind"):
        wake_marks.begin(tmp_path / "marks", SEAT, 1, "Pane", now=WHEN)


def test_an_unreadable_or_unknown_outcome_stays_uncertain(tmp_path: Path) -> None:
    """A broken outcome does not become a successful wake."""
    marks = tmp_path / "marks"
    order = wake_marks.begin(marks, SEAT, 4, "pane_bridge", now=WHEN)
    outcome = marks / wake_marks.seat_key(SEAT) / "4" / f"{order:02d}-pane_bridge.outcome.json"
    outcome.write_text("{", encoding="utf-8")
    (broken,) = wake_marks.read(marks, SEAT, 4)
    assert broken.state == UNCERTAIN
    assert broken.detail == "the outcome mark cannot be read"

    outcome.write_text('{"state": "not-a-state"}\n', encoding="utf-8")
    (unknown,) = wake_marks.read(marks, SEAT, 4)
    assert unknown.state == UNCERTAIN
    assert unknown.detail == "the outcome mark is not a known state"

    outcome.write_text('{"state": "uncertain"}\n', encoding="utf-8")
    outcome.chmod(0)
    try:
        (hidden,) = wake_marks.read(marks, SEAT, 4)
    finally:
        outcome.chmod(0o600)
    assert hidden.state == UNCERTAIN
    assert hidden.detail == "the outcome mark cannot be read"


def test_pruning_a_seat_that_has_no_marks_removes_nothing(tmp_path: Path) -> None:
    """Pruning a missing seat directory reports that nothing was removed."""
    assert wake_marks.prune(tmp_path / "absent", SEAT, keep_newest=1) == 0
