# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — uncertain wakes are reported, asked about and resolved
"""An uncertain wake is never repeated, and it is never forgotten either.

Each adapter here makes a real, countable effect: it appends a line to a
file. The relay writes real marks. The tests show that unresolved uncertain
wakes are listed with their age and evidence and become an operator question
once old. A later wake of the same seat is detected as a possible second
effect. A resolution ends the question without waking again, and pruning
keeps unresolved uncertainty.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from synapse_channel import wake_marks
from synapse_channel.cli import main
from synapse_channel.execution_outcome import OutcomeState
from synapse_channel.wake_adapters import (
    ACCEPTED,
    UNCERTAIN,
    Availability,
    WakeOutcome,
    WakeTarget,
)
from synapse_channel.wake_relay import relay
from synapse_channel.wake_uncertainty import (
    DEFAULT_QUESTION_AFTER_SECONDS,
    OPERATOR_ACTIONS,
    unresolved,
)

SEAT = "PROJECT/seat-uncertain"
T0 = datetime(2026, 10, 29, 12, 0, 0, tzinfo=timezone.utc)


class CountingAdapter:
    """Append one line per wake to ``effects`` and give a fixed answer."""

    def __init__(self, kind: str, effects: Path, state: str) -> None:
        """Remember the transport kind, the effects file and the answer to give."""
        self._kind = kind
        self.effects = effects
        self.state = state

    @property
    def kind(self) -> str:
        """Return the transport kind written into marks."""
        return self._kind

    def available(self, target: WakeTarget) -> Availability:
        """Every target is reachable."""
        return Availability(True, "")

    def wake(self, target: WakeTarget, wake_id: int, prompt: str, seconds: float) -> WakeOutcome:
        """Make the countable effect, then answer."""
        with self.effects.open("a", encoding="utf-8") as handle:
            handle.write(f"{target.seat} {wake_id}\n")
        proves = "queued_by_native_daemon" if self.state == ACCEPTED else ""
        return WakeOutcome(self.state, proves, "")


def _relay(tmp_path: Path, wake_id: int, adapter: CountingAdapter, *, seat: str = SEAT) -> None:
    """Relay one wake of ``seat`` through the real relay with ``adapter``, at ``T0``."""
    relay(
        tmp_path / "marks",
        WakeTarget(seat, "session", "host"),
        wake_id,
        [adapter],
        prompt_for=lambda seat: f"wake {seat}",
        now=lambda: T0,
    )


def _effects(tmp_path: Path) -> list[str]:
    """Return the effect lines the counting adapters wrote, or none."""
    path = tmp_path / "effects"
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def test_an_uncertain_wake_is_listed_with_age_evidence_and_repeat_class(tmp_path: Path) -> None:
    """A young uncertain wake is listed without a question; an old one asks the operator."""
    _relay(tmp_path, 7, CountingAdapter("pane_bridge", tmp_path / "effects", UNCERTAIN))
    marks = tmp_path / "marks"

    (young,) = unresolved(marks, now=T0 + timedelta(seconds=60))
    (old,) = unresolved(marks, now=T0 + timedelta(seconds=DEFAULT_QUESTION_AFTER_SECONDS))

    assert (young.seat, young.wake_id, young.adapter, young.state) == (
        SEAT,
        7,
        "pane_bridge",
        UNCERTAIN,
    )
    assert young.age_seconds == 60.0
    assert young.repeat == "second_effect"
    assert young.operator_actions == OPERATOR_ACTIONS
    assert young.question == ""
    assert Path(young.evidence).is_dir()
    assert old.question.startswith(f"Did {SEAT} read message 7?")
    assert old.evidence in old.question
    assert _effects(tmp_path) == [f"{SEAT} 7"]


def test_an_interrupted_attempt_is_uncertain_and_dated_by_its_attempt_mark(tmp_path: Path) -> None:
    """A process that died between effect and outcome leaves an attempt that is listed."""
    marks = tmp_path / "marks"
    wake_marks.begin(marks, SEAT, 3, "harness_waiter", now=T0)

    (row,) = unresolved(marks, now=T0 + timedelta(seconds=5), question_after_seconds=10)

    assert row.state == "interrupted"
    assert row.since == "2026-10-29T12:00:00.000000Z"
    assert row.repeat == "safe"
    assert row.to_json()["operator_actions"] == list(OPERATOR_ACTIONS)


def test_a_later_wake_of_the_same_seat_is_detected_as_a_possible_second_effect(
    tmp_path: Path,
) -> None:
    """A new message woke the seat again while the first wake was still uncertain."""
    effects = tmp_path / "effects"
    _relay(tmp_path, 7, CountingAdapter("pane_bridge", effects, UNCERTAIN))
    _relay(tmp_path, 9, CountingAdapter("pane_bridge", effects, ACCEPTED))
    _relay(tmp_path, 4, CountingAdapter("pane_bridge", effects, UNCERTAIN), seat="P/other")

    rows = {(row.seat, row.wake_id): row for row in unresolved(tmp_path / "marks", now=T0)}

    assert set(rows) == {(SEAT, 7), ("P/other", 4)}
    assert rows[(SEAT, 7)].followed_by == (9,)
    assert rows[(SEAT, 7)].to_json()["followed_by"] == [9]
    assert rows[("P/other", 4)].followed_by == ()
    assert len(_effects(tmp_path)) == 3


def test_resolution_ends_the_question_without_waking_and_is_written_once(tmp_path: Path) -> None:
    """A resolution ends the question, never wakes the seat, and cannot be written twice."""
    effects = tmp_path / "effects"
    adapter = CountingAdapter("pane_bridge", effects, UNCERTAIN)
    _relay(tmp_path, 7, adapter)
    marks = tmp_path / "marks"

    wake_marks.resolve(marks, SEAT, 7, "read", "operator-a", now=T0)
    _relay(tmp_path, 7, adapter)

    assert unresolved(marks, now=T0) == ()
    assert wake_marks.resolution(marks, SEAT, 7) == {
        "schema": wake_marks.RESOLUTION_SCHEMA,
        "seat": SEAT,
        "wake_id": "7",
        "decision": "read",
        "operator": "operator-a",
        "at": "2026-10-29T12:00:00.000000Z",
    }
    assert _effects(tmp_path) == [f"{SEAT} 7"]
    with pytest.raises(FileExistsError):
        wake_marks.resolve(marks, SEAT, 7, "abandoned", "operator-b", now=T0)


@pytest.mark.parametrize(
    ("decision", "operator", "message"),
    [
        ("retry", "operator-a", "a resolution is one of"),
        ("read", " ", "printable text"),
        ("read", "line\nbreak", "printable text"),
        ("read", "x" * 301, "printable text"),
    ],
)
def test_invalid_resolutions_are_refused(
    tmp_path: Path, decision: str, operator: str, message: str
) -> None:
    """A decision or operator outside the allowed values is refused before anything is written."""
    _relay(tmp_path, 7, CountingAdapter("pane_bridge", tmp_path / "effects", UNCERTAIN))

    with pytest.raises(wake_marks.WakeMarkError, match=message):
        wake_marks.resolve(tmp_path / "marks", SEAT, 7, decision, operator, now=T0)


def test_only_an_uncertain_wake_is_resolved(tmp_path: Path) -> None:
    """An accepted wake has nothing to resolve; resolving it is refused."""
    _relay(tmp_path, 7, CountingAdapter("pane_bridge", tmp_path / "effects", ACCEPTED))

    with pytest.raises(wake_marks.WakeMarkError, match="only an uncertain wake"):
        wake_marks.resolve(tmp_path / "marks", SEAT, 7, "read", "operator-a", now=T0)
    assert wake_marks.resolution(tmp_path / "marks", SEAT, 7) is None


@pytest.mark.parametrize(
    "content",
    ["{", json.dumps({"schema": "other"}), json.dumps(["not", "a", "mapping"])],
)
def test_a_damaged_resolution_mark_is_refused(tmp_path: Path, content: str) -> None:
    """A resolution mark that is not JSON or not this schema is refused, not obeyed."""
    _relay(tmp_path, 7, CountingAdapter("pane_bridge", tmp_path / "effects", UNCERTAIN))
    path = tmp_path / "marks" / wake_marks.seat_key(SEAT) / "7" / "resolution.json"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(wake_marks.WakeMarkError, match="resolution mark"):
        wake_marks.resolution(tmp_path / "marks", SEAT, 7)


def test_pruning_keeps_unresolved_uncertainty_and_drops_it_once_resolved(tmp_path: Path) -> None:
    """Pruning keeps an unresolved uncertain wake and may drop it once resolved."""
    effects = tmp_path / "effects"
    marks = tmp_path / "marks"
    _relay(tmp_path, 1, CountingAdapter("pane_bridge", effects, UNCERTAIN))
    _relay(tmp_path, 2, CountingAdapter("pane_bridge", effects, ACCEPTED))
    _relay(tmp_path, 3, CountingAdapter("pane_bridge", effects, ACCEPTED))

    assert wake_marks.prune(marks, SEAT, keep_newest=1) == 1
    assert wake_marks.wake_ids(marks, SEAT) == (1, 3)
    wake_marks.resolve(marks, SEAT, 1, "abandoned", "operator-a", now=T0)
    assert wake_marks.prune(marks, SEAT, keep_newest=1) == 1
    assert wake_marks.wake_ids(marks, SEAT) == (3,)


def test_unreadable_start_times_still_ask_the_operator(tmp_path: Path) -> None:
    """A wake whose start cannot be dated has no age and always carries a question."""
    marks = tmp_path / "marks"
    wake_marks.begin(marks, SEAT, 1, "pane_bridge", now=T0)
    wake_marks.begin(marks, SEAT, 2, "pane_bridge", now=T0)
    wake_marks.begin(marks, SEAT, 3, "pane_bridge", now=T0)
    base = marks / wake_marks.seat_key(SEAT)
    (base / "2" / "01-pane_bridge.attempt.json").write_text("[]", encoding="utf-8")
    (base / "3" / "01-pane_bridge.attempt.json").write_text(
        json.dumps({"seat": SEAT, "at": "yesterday"}), encoding="utf-8"
    )
    unreadable = base / "1" / "01-pane_bridge.attempt.json"
    unreadable.chmod(0)
    try:
        rows = unresolved(marks, now=T0, question_after_seconds=10_000)
    finally:
        unreadable.chmod(0o600)

    assert [(row.wake_id, row.since, row.age_seconds) for row in rows] == [
        (1, "", None),
        (2, "", None),
        (3, "yesterday", None),
    ]
    assert all(row.question and "since" not in row.question for row in rows)


def test_seat_discovery_ignores_foreign_and_damaged_marks(tmp_path: Path) -> None:
    """Only seats with readable marks are listed; foreign or damaged entries are skipped."""
    marks = tmp_path / "marks"
    assert wake_marks.seats(marks) == ()
    assert wake_marks.wake_ids(marks, SEAT) == ()
    wake_marks.begin(marks, SEAT, 1, "pane_bridge", now=T0)
    stray = marks / "not-a-seat-key" / "1"
    stray.mkdir(parents=True)
    (stray / "01-pane_bridge.attempt.json").write_text(json.dumps({"seat": SEAT}), "utf-8")
    broken = marks / "broken" / "1"
    broken.mkdir(parents=True)
    (broken / "01-pane_bridge.attempt.json").write_text("{", encoding="utf-8")
    listed = marks / "listed" / "1"
    listed.mkdir(parents=True)
    (listed / "01-pane_bridge.attempt.json").write_text("[1]", encoding="utf-8")

    assert wake_marks.seats(marks) == (SEAT,)


def test_the_question_threshold_must_be_finite_and_not_negative(tmp_path: Path) -> None:
    """A negative or infinite question threshold is refused."""
    for value in (-1.0, float("inf"), float("nan")):
        with pytest.raises(ValueError, match="question threshold"):
            unresolved(tmp_path, now=T0, question_after_seconds=value)


def test_the_cli_lists_asks_and_resolves(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``synapse waker uncertain`` and ``resolve`` through the real command line."""
    marks = str(tmp_path / "marks")
    assert main(["waker", "uncertain", "--marks-dir", marks]) == 0
    assert capsys.readouterr().out == ""
    _relay(tmp_path, 7, CountingAdapter("pane_bridge", tmp_path / "effects", UNCERTAIN))

    assert main(["waker", "uncertain", "--marks-dir", marks]) == 3
    (line,) = capsys.readouterr().out.splitlines()
    assert json.loads(line)["wake_id"] == 7
    assert main(["waker", "uncertain", "--marks-dir", marks, "--question-after", "1e12"]) == 0
    capsys.readouterr()
    assert main(["waker", "uncertain", "--marks-dir", marks, "--question-after", "-1"]) == 2
    assert "question threshold" in capsys.readouterr().err

    resolve = ["waker", "resolve", "--marks-dir", marks, "--seat", SEAT, "--wake-id", "7"]
    assert main([*resolve, "--as", "read", "--operator", " "]) == 2
    assert "printable text" in capsys.readouterr().err
    assert main([*resolve, "--as", "read", "--operator", "operator-a"]) == 0
    assert json.loads(capsys.readouterr().out) == {"seat": SEAT, "wake_id": 7, "resolved": "read"}
    assert main([*resolve, "--as", "abandoned", "--operator", "operator-b"]) == 1
    assert "already resolved" in capsys.readouterr().err
    assert main(["waker", "uncertain", "--marks-dir", marks]) == 0
    assert _effects(tmp_path) == [f"{SEAT} 7"]


def test_the_cli_defaults_to_the_data_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Without --marks-dir the CLI reads the marks under the data directory."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    wake_marks.begin(wake_marks.default_marks_dir(), SEAT, 2, "pane_bridge", now=T0)

    assert main(["waker", "uncertain"]) == 3
    assert json.loads(capsys.readouterr().out)["state"] == "interrupted"
    assert (
        main(
            [
                "waker",
                "resolve",
                "--seat",
                SEAT,
                "--wake-id",
                "2",
                "--as",
                "abandoned",
                "--operator",
                "operator-a",
            ]
        )
        == 0
    )


def test_the_relay_carries_the_question_of_an_old_uncertain_wake_and_does_not_repeat_it(
    tmp_path: Path,
) -> None:
    """The question of an old unresolved uncertain wake reaches the relay caller."""
    effects = tmp_path / "effects.txt"
    marks = tmp_path / "marks"
    target = WakeTarget(SEAT, "session", "host")
    adapter = CountingAdapter("tmux_pane", effects, UNCERTAIN)
    young = T0 + timedelta(seconds=DEFAULT_QUESTION_AFTER_SECONDS - 1)
    old = T0 + timedelta(seconds=DEFAULT_QUESTION_AFTER_SECONDS + 1)

    first = relay(marks, target, 7, [adapter], prompt_for=lambda seat: "wake", now=lambda: T0)
    early = relay(marks, target, 7, [adapter], prompt_for=lambda seat: "wake", now=lambda: young)
    late = relay(marks, target, 7, [adapter], prompt_for=lambda seat: "wake", now=lambda: old)
    accepted = relay(
        marks,
        target,
        8,
        [CountingAdapter("tmux_pane", effects, ACCEPTED)],
        prompt_for=lambda seat: "wake",
        now=lambda: old,
    )
    again = relay(marks, target, 8, [adapter], prompt_for=lambda seat: "wake", now=lambda: old)
    later = relay(marks, target, 9, [adapter], prompt_for=lambda seat: "wake", now=lambda: T0)
    asked = relay(marks, target, 9, [adapter], prompt_for=lambda seat: "wake", now=lambda: old)

    assert (first.state, first.sent_now, first.operator_question) == (UNCERTAIN, True, "")
    assert (early.sent_now, early.operator_question) == (False, "")
    assert late.sent_now is False and late.operator_question
    assert late.to_json()["operator_question"] == late.operator_question
    assert accepted.operator_question == "" and again.operator_question == ""
    assert accepted.execution_outcome.state == OutcomeState.CONFIRMED_DONE
    assert (
        later.sent_now
        and asked.operator_question
        and asked.operator_question != late.operator_question
    )
    assert effects.read_text(encoding="utf-8").splitlines() == [
        f"{SEAT} 7",
        f"{SEAT} 8",
        f"{SEAT} 9",
    ]


def _interrupted(tmp_path: Path, seat: str, wake_id: int) -> None:
    """Leave one uncertain wake for ``seat`` through the real relay."""
    _relay(
        tmp_path,
        wake_id,
        CountingAdapter("pane_bridge", tmp_path / "effects", UNCERTAIN),
        seat=seat,
    )


@pytest.mark.parametrize("other", [(SEAT, 2), ("PROJECT/other-seat", 1)])
def test_a_copied_resolution_never_closes_another_wakes_question(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], other: tuple[str, int]
) -> None:
    """A resolution copied from another message or another seat is refused, not obeyed."""
    marks = tmp_path / "marks"
    _interrupted(tmp_path, SEAT, 1)
    _interrupted(tmp_path, other[0], other[1])
    wake_marks.resolve(marks, other[0], other[1], "read", "operator-a", now=T0)
    source = marks / wake_marks.seat_key(other[0]) / str(other[1]) / "resolution.json"
    target = marks / wake_marks.seat_key(SEAT) / "1" / "resolution.json"
    target.write_bytes(source.read_bytes())

    assert main(["waker", "uncertain", "--marks-dir", str(marks)]) == 2
    assert "resolves another wake" in capsys.readouterr().err
    question = relay(
        marks,
        WakeTarget(SEAT, "session", "host"),
        1,
        [CountingAdapter("pane_bridge", tmp_path / "effects", ACCEPTED)],
        prompt_for=lambda seat: "wake",
        now=lambda: T0,
    )
    assert question.sent_now is False
    assert "cannot be trusted" in question.operator_question


@pytest.mark.parametrize(
    "changes",
    [{"decision": []}, {"wake_id": "1"}, {"operator": 7}, {"wake_id": True}],
)
def test_a_resolution_with_wrong_field_types_is_refused_explicitly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], changes: dict[str, object]
) -> None:
    """A resolution whose fields have the wrong types is refused with exit 2, not a traceback."""
    marks = tmp_path / "marks"
    _interrupted(tmp_path, SEAT, 1)
    wake_marks.resolve(marks, SEAT, 1, "read", "operator-a", now=T0)
    path = marks / wake_marks.seat_key(SEAT) / "1" / "resolution.json"
    path.write_text(json.dumps({**json.loads(path.read_text("utf-8")), **changes}), "utf-8")

    assert main(["waker", "uncertain", "--marks-dir", str(marks)]) == 2
    assert "not a known resolution" in capsys.readouterr().err
