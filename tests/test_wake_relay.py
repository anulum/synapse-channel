# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the wake relay, its marks and the shared file lock
"""A wake is an effect outside the product; these tests count the effect.

Every adapter here does something real and countable: it appends one line to
a file in the test directory. "At most one wake" is then a statement about
that file, also when two processes relay the same message, and when the
relaying process dies between the effect and its outcome mark.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from synapse_channel import wake_marks
from synapse_channel.advisory_lock import locked
from synapse_channel.agent_tmux import build_wake_prompt
from synapse_channel.wake_adapters import (
    ACCEPTED,
    PROVES,
    REFUSED,
    STATES,
    UNCERTAIN,
    Availability,
    WakeOutcome,
    WakeTarget,
)
from synapse_channel.wake_marks import WakeMarkError
from synapse_channel.wake_relay import WAKE_FAILED, RelayResult, relay

SEAT = "PROJECT/seat-aaaa"
TARGET = WakeTarget(SEAT, "native-session-1", "host-a")
_T0 = datetime(2026, 10, 11, 12, 0, 0, tzinfo=timezone.utc)


class FileAdapter:
    """An adapter whose wake appends one line to a file, then gives its answer."""

    def __init__(
        self,
        kind: str,
        effects: Path,
        answer: Any = None,
        *,
        available: bool = True,
        gate: threading.Event | None = None,
    ) -> None:
        self._kind = kind
        self.effects = effects
        self.answer = WakeOutcome(ACCEPTED, "queued_by_native_daemon") if answer is None else answer
        self.is_available = available
        self.gate = gate
        self.entered = threading.Event()

    @property
    def kind(self) -> str:
        return self._kind

    def available(self, target: WakeTarget) -> Availability:
        return Availability(self.is_available, "" if self.is_available else "daemon down")

    def wake(
        self, target: WakeTarget, wake_id: int, prompt: str, deadline_seconds: float
    ) -> WakeOutcome:
        with self.effects.open("a", encoding="utf-8") as handle:
            line = {
                "kind": self._kind,
                "seat": target.seat,
                "session": target.native_session,
                "wake_id": wake_id,
                "prompt": prompt,
                "deadline": deadline_seconds,
            }
            handle.write(json.dumps(line) + "\n")
        self.entered.set()
        if self.gate is not None:
            assert self.gate.wait(timeout=20)
        if isinstance(self.answer, Exception):
            raise self.answer
        answer: WakeOutcome = self.answer
        return answer


def _effects(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _relay(tmp_path: Path, wake_id: int, *adapters: FileAdapter, seat: str = SEAT) -> RelayResult:
    target = WakeTarget(seat, TARGET.native_session, TARGET.execution_host)
    return relay(tmp_path / "marks", target, wake_id, adapters, now=lambda: _T0)


def test_the_first_adapter_that_accepts_ends_the_work_and_is_named(tmp_path: Path) -> None:
    effects = tmp_path / "effects"
    first = FileAdapter("native_queue", effects)
    second = FileAdapter("pane_bridge", effects)

    result = _relay(tmp_path, 41, first, second)
    again = _relay(tmp_path, 41, first, second)

    assert (result.state, result.wake_path, result.proves) == (
        ACCEPTED,
        "native_queue",
        "queued_by_native_daemon",
    )
    assert result.sent_now and not again.sent_now
    assert (again.state, again.wake_path) == (ACCEPTED, "native_queue")
    (effect,) = _effects(effects)
    assert (effect["kind"], effect["wake_id"], effect["session"]) == (
        "native_queue",
        41,
        "native-session-1",
    )
    assert effect["deadline"] == 30.0
    (mark,) = wake_marks.read(tmp_path / "marks", SEAT, 41)
    assert (mark.order, mark.adapter, mark.state) == (1, "native_queue", ACCEPTED)
    assert mark.at == "2026-10-11T12:00:00.000000Z"


def test_only_a_refusal_lets_the_next_adapter_run_and_a_refused_one_is_not_asked_again(
    tmp_path: Path,
) -> None:
    effects = tmp_path / "effects"
    refusing = FileAdapter("native_queue", effects, WakeOutcome(REFUSED, "", "unknown session"))
    taking = FileAdapter("pane_bridge", effects, WakeOutcome(ACCEPTED, "typed_into_idle_composer"))

    result = _relay(tmp_path, 7, refusing, taking)
    _relay(tmp_path, 7, refusing, taking)

    assert (result.state, result.wake_path, result.proves) == (
        ACCEPTED,
        "pane_bridge",
        "typed_into_idle_composer",
    )
    assert [e["kind"] for e in _effects(effects)] == ["native_queue", "pane_bridge"]
    assert [(m.order, m.adapter, m.state, m.detail) for m in result.attempts] == [
        (1, "native_queue", REFUSED, "unknown session"),
        (2, "pane_bridge", ACCEPTED, ""),
    ]


@pytest.mark.parametrize(
    ("answer", "detail"),
    [
        (WakeOutcome(UNCERTAIN, "", "no acknowledgement"), "no acknowledgement"),
        (RuntimeError("lost"), "the adapter raised RuntimeError"),
        (WakeOutcome("delivered"), "the adapter gave no known state"),
        ("accepted", "the adapter gave no known state"),
    ],
)
def test_whatever_is_not_a_clear_answer_is_uncertain_and_nothing_else_is_tried(
    tmp_path: Path, answer: Any, detail: str
) -> None:
    effects = tmp_path / "effects"
    unclear = FileAdapter("native_queue", effects, answer)
    fallback = FileAdapter("pane_bridge", effects)

    result = _relay(tmp_path, 9, unclear, fallback)
    later = _relay(tmp_path, 9, fallback, unclear)

    assert (result.state, result.wake_path, result.sent_now) == (UNCERTAIN, "native_queue", True)
    assert (later.state, later.wake_path, later.sent_now) == (UNCERTAIN, "native_queue", False)
    assert [e["kind"] for e in _effects(effects)] == ["native_queue"]
    assert result.attempts[0].detail == detail


def test_when_every_adapter_refused_the_wake_failed_and_stays_failed(tmp_path: Path) -> None:
    effects = tmp_path / "effects"
    one = FileAdapter("native_queue", effects, WakeOutcome(REFUSED, "", "no such thread"))
    two = FileAdapter("pane_bridge", effects, WakeOutcome(REFUSED, "", "session gone"))

    result = _relay(tmp_path, 3, one, two)
    again = _relay(tmp_path, 3, one, two)

    assert (result.state, result.wake_path, result.sent_now) == (WAKE_FAILED, "", True)
    assert [(m.adapter, m.detail) for m in result.attempts] == [
        ("native_queue", "no such thread"),
        ("pane_bridge", "session gone"),
    ]
    assert (again.state, again.sent_now, again.attempts) == (WAKE_FAILED, False, result.attempts)
    assert len(_effects(effects)) == 2
    assert _relay(tmp_path, 4).state == WAKE_FAILED


def test_an_unavailable_adapter_leaves_no_mark_and_may_be_tried_later(tmp_path: Path) -> None:
    effects = tmp_path / "effects"
    down = FileAdapter("native_queue", effects, available=False)

    first = _relay(tmp_path, 5, down)
    down.is_available = True
    second = _relay(tmp_path, 5, down)

    assert (first.state, first.attempts, first.sent_now) == (WAKE_FAILED, (), False)
    assert first.passed_over == (("native_queue", "daemon down"),)
    assert (second.state, second.wake_path, second.passed_over) == (ACCEPTED, "native_queue", ())
    assert len(_effects(effects)) == 1


def test_the_adapter_gets_the_fixed_prompt_and_no_message_text_exists_to_pass(
    tmp_path: Path,
) -> None:
    effects = tmp_path / "effects"
    hostile_seat = "PROJECT/seat;$(touch marker)"

    _relay(tmp_path, 1, FileAdapter("pane_bridge", effects), seat=hostile_seat)

    (effect,) = _effects(effects)
    assert effect["prompt"] == build_wake_prompt(hostile_seat)
    stored = "".join(
        path.read_text(encoding="utf-8") for path in (tmp_path / "marks").rglob("*.json")
    )
    assert "Synapse wake for" not in stored and not (tmp_path / "marker").exists()
    # Seats that look alike once made safe for a file name keep separate marks.
    assert wake_marks.seat_key("P/a") != wake_marks.seat_key("P:a")
    assert wake_marks.read(tmp_path / "marks", "PROJECT/seat;$(touch other)", 1) == ()


_DIE_AFTER_THE_EFFECT = """
import os, sys
from pathlib import Path
from synapse_channel.wake_adapters import Availability, WakeTarget
from synapse_channel.wake_relay import relay

class Dying:
    kind = "native_queue"
    def available(self, target):
        return Availability(True)
    def wake(self, target, wake_id, prompt, deadline_seconds):
        with open(sys.argv[2], "a", encoding="utf-8") as handle:
            handle.write('{"kind": "native_queue"}' + chr(10))
            handle.flush()
            os.fsync(handle.fileno())
        os._exit(9)

relay(Path(sys.argv[1]), WakeTarget(sys.argv[3]), 77, [Dying()])
"""


def test_a_process_that_dies_between_the_effect_and_its_mark_leaves_an_uncertain_wake(
    tmp_path: Path,
) -> None:
    """A real process is killed after its wake; the next relay must not wake again."""
    effects = tmp_path / "effects"
    died = subprocess.run(  # noqa: S603 - this interpreter, a fixed script
        [sys.executable, "-c", _DIE_AFTER_THE_EFFECT, str(tmp_path / "marks"), str(effects), SEAT],
        check=False,
        timeout=60,
    )
    assert died.returncode == 9 and len(_effects(effects)) == 1
    (open_mark,) = wake_marks.read(tmp_path / "marks", SEAT, 77)
    assert (open_mark.adapter, open_mark.state) == ("native_queue", wake_marks.OPEN)

    result = _relay(
        tmp_path, 77, FileAdapter("native_queue", effects), FileAdapter("pane_bridge", effects)
    )

    assert (result.state, result.wake_path, result.sent_now) == (UNCERTAIN, "native_queue", False)
    assert len(_effects(effects)) == 1
    # The dead process's lock went with it: another message of the seat is relayed.
    assert _relay(tmp_path, 78, FileAdapter("pane_bridge", effects)).state == ACCEPTED


def _relay_at_once(tmp_path: Path, wake_id: int, effects: Path, count: int) -> list[RelayResult]:
    start = threading.Barrier(count)
    results: list[RelayResult] = []

    def one() -> None:
        start.wait()
        results.append(_relay(tmp_path, wake_id, FileAdapter("native_queue", effects)))

    threads = [threading.Thread(target=one) for _ in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return results


def test_one_message_delivered_to_several_relays_at_once_wakes_once(tmp_path: Path) -> None:
    effects = tmp_path / "effects"
    for wake_id in range(100, 110):
        results = _relay_at_once(tmp_path, wake_id, effects, 5)

        assert [r.state for r in results] == [ACCEPTED] * 5
        assert sum(r.sent_now for r in results) == 1
    assert sorted(e["wake_id"] for e in _effects(effects)) == list(range(100, 110))


_HOLD_LOCK = """
import os, sys
descriptor = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o600)
if sys.platform == "win32":
    import msvcrt
    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
else:
    import fcntl
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
print("held", flush=True)
sys.stdin.readline()
"""


def test_a_relay_waits_while_another_process_is_at_work_for_the_seat(tmp_path: Path) -> None:
    """Another process holds the operating system's lock on the seat's lock file."""
    effects = tmp_path / "effects"
    lock = tmp_path / "marks" / wake_marks.seat_key(SEAT) / ".lock"
    with locked(lock, blocking=False) as free:
        assert free
    holder = subprocess.Popen(  # noqa: S603 - this interpreter, a fixed script
        [sys.executable, "-c", _HOLD_LOCK, str(lock)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert holder.stdout is not None and holder.stdin is not None
    done = threading.Event()

    def run() -> None:
        _relay(tmp_path, 12, FileAdapter("native_queue", effects))
        done.set()

    try:
        assert holder.stdout.readline().strip() == "held"
        with locked(lock, blocking=False) as taken:
            assert not taken
        waiting = threading.Thread(target=run)
        waiting.start()
        assert not done.wait(timeout=0.5)
        assert _effects(effects) == [] and wake_marks.read(tmp_path / "marks", SEAT, 12) == ()
        # Another seat is not held up.
        assert _relay(tmp_path, 12, FileAdapter("pane_bridge", effects), seat="P/other").sent_now
    finally:
        holder.stdin.write("\n")
        holder.stdin.close()
        holder.wait(timeout=20)
        holder.stdout.close()
    assert done.wait(timeout=20)
    waiting.join()
    assert [e["kind"] for e in _effects(effects)] == ["pane_bridge", "native_queue"]


def test_marks_refuse_values_that_cannot_name_one_and_are_never_rewritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marks = tmp_path / "marks"
    not_ids: list[Any] = [-1, True, "7", 1.5]
    for wake_id in not_ids:
        with pytest.raises(WakeMarkError, match="whole number"):
            wake_marks.read(marks, SEAT, wake_id)
    with pytest.raises(WakeMarkError, match="needs a seat"):
        wake_marks.read(marks, "  ", 1)
    with pytest.raises(WakeMarkError, match="needs a seat"), wake_marks.seat_lock(marks, ""):
        raise AssertionError("no lock for a seat without a name")
    with pytest.raises(WakeMarkError, match="adapter kind"):
        wake_marks.begin(marks, SEAT, 1, "Native Queue", now=_T0)
    assert not marks.exists()

    order = wake_marks.begin(marks, SEAT, 1, "native_queue", now=_T0)
    wake_marks.finish(marks, SEAT, 1, "native_queue", order, WakeOutcome(REFUSED), now=_T0)
    with pytest.raises(FileExistsError):
        wake_marks.finish(marks, SEAT, 1, "native_queue", order, WakeOutcome(ACCEPTED), now=_T0)
    assert [m.state for m in wake_marks.read(marks, SEAT, 1)] == [REFUSED]
    base = marks / wake_marks.seat_key(SEAT) / "1"
    assert sorted(p.name for p in base.iterdir()) == [
        "01-native_queue.attempt.json",
        "01-native_queue.outcome.json",
    ]
    if os.name == "posix":
        assert all((p.stat().st_mode & 0o777) == 0o600 for p in base.iterdir())

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    assert wake_marks.default_marks_dir() == tmp_path / "data" / "synapse" / "wake-marks"
    monkeypatch.delenv("XDG_DATA_HOME")
    monkeypatch.setenv("HOME", str(tmp_path / "h"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "h"))
    assert wake_marks.default_marks_dir() == (
        tmp_path / "h" / ".local" / "share" / "synapse" / "wake-marks"
    )
    assert {ACCEPTED, REFUSED, UNCERTAIN} == STATES and len(PROVES) == 3


@pytest.mark.parametrize("content", ["not json", "[]", '{"state": "delivered"}'])
def test_an_outcome_mark_that_cannot_be_trusted_reads_as_uncertain(
    tmp_path: Path, content: str
) -> None:
    marks = tmp_path / "marks"
    effects = tmp_path / "effects"
    order = wake_marks.begin(marks, SEAT, 2, "native_queue", now=_T0)
    base = marks / wake_marks.seat_key(SEAT) / "2"
    (base / f"{order:02d}-native_queue.outcome.json").write_text(content, encoding="utf-8")
    (base / "notes.txt").write_text("not a mark", encoding="utf-8")
    (base / "02-pane_bridge.outcome.json").write_text('{"state": "accepted"}', encoding="utf-8")

    result = _relay(tmp_path, 2, FileAdapter("pane_bridge", effects))

    assert (result.state, result.wake_path, result.sent_now) == (UNCERTAIN, "native_queue", False)
    assert len(result.attempts) == 1 and _effects(effects) == []
    # An outcome with an unknown state is stored as uncertain, never as written.
    wake_marks.finish(
        marks,
        SEAT,
        3,
        "x",
        wake_marks.begin(marks, SEAT, 3, "x", now=_T0),
        WakeOutcome("odd", "p" * 400),
        now=_T0,
    )
    (mark,) = wake_marks.read(marks, SEAT, 3)
    assert (mark.state, len(mark.proves)) == (UNCERTAIN, 300)


def test_pruning_keeps_the_newest_messages_of_a_seat(tmp_path: Path) -> None:
    marks = tmp_path / "marks"
    effects = tmp_path / "effects"
    assert wake_marks.prune(marks, SEAT, keep_newest=1) == 0
    for wake_id in (5, 20, 100):
        _relay(tmp_path, wake_id, FileAdapter("native_queue", effects))
    _relay(tmp_path, 5, FileAdapter("native_queue", effects), seat="P/other")

    assert wake_marks.prune(marks, SEAT, keep_newest=5) == 0
    assert wake_marks.prune(marks, SEAT, keep_newest=1) == 2
    assert wake_marks.read(marks, SEAT, 100) and not wake_marks.read(marks, SEAT, 20)
    assert not wake_marks.read(marks, SEAT, 5)
    assert wake_marks.prune(marks, SEAT, keep_newest=-3) == 1

    assert wake_marks.read(marks, SEAT, 100) == () and wake_marks.read(marks, "P/other", 5)
    assert (marks / wake_marks.seat_key(SEAT) / ".lock").is_file()


def test_a_hundredth_attempt_is_read_like_the_first(tmp_path: Path) -> None:
    """Ninety-nine refusals and then an acceptance: the order has three digits."""
    effects = tmp_path / "effects"
    refusing = [
        FileAdapter(f"refuser_{number:03d}", effects, WakeOutcome(REFUSED, "", "no"))
        for number in range(99)
    ]
    taking = FileAdapter("native_queue", effects)

    first = _relay(tmp_path, 300, *refusing, taking)
    again = _relay(tmp_path, 300)
    with_adapters = _relay(tmp_path, 300, *refusing, taking)

    assert (first.state, first.wake_path, first.sent_now) == (ACCEPTED, "native_queue", True)
    assert [mark.order for mark in first.attempts] == list(range(1, 101))
    assert (first.attempts[98].state, first.attempts[99].state) == (REFUSED, ACCEPTED)
    for later in (again, with_adapters):
        assert (later.state, later.wake_path, later.sent_now) == (ACCEPTED, "native_queue", False)
    assert len(_effects(effects)) == 100
    names = sorted(
        p.name for p in (tmp_path / "marks" / wake_marks.seat_key(SEAT) / "300").iterdir()
    )
    assert "100-native_queue.outcome.json" in names and "99-refuser_098.attempt.json" in names


def test_more_than_a_hundred_refusals_fail_once_and_stay_failed(tmp_path: Path) -> None:
    effects = tmp_path / "effects"
    refusing = [
        FileAdapter(f"refuser_{number:03d}", effects, WakeOutcome(REFUSED, "", "no"))
        for number in range(120)
    ]

    first = _relay(tmp_path, 301, *refusing)
    again = _relay(tmp_path, 301, *refusing)

    assert (first.state, len(first.attempts), first.sent_now) == (WAKE_FAILED, 120, True)
    assert (again.state, again.sent_now, again.attempts) == (WAKE_FAILED, False, first.attempts)
    assert [mark.order for mark in again.attempts] == list(range(1, 121))
    assert len(_effects(effects)) == 120
