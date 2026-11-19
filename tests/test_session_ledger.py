# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the session ledger's record store
"""The session ledger's store: where it lives, how a line is written, how rows fold."""

from __future__ import annotations

import errno
import fcntl
import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from synapse_channel import session_ledger
from synapse_channel.session_ledger import (
    SCHEMA,
    LedgerError,
    NoLedger,
    append_event,
    boot_id,
    fold,
    ledger_dir,
    pid_starttime,
    read_events,
    row_id,
    shared_tree,
)

BOOT = "boot-a"


def _lines(directory: Path) -> list[dict[str, Any]]:
    return list(read_events(directory))


# --- where the ledger lives ---------------------------------------------------------


def test_the_ledger_lives_in_the_users_private_state() -> None:
    # Paths that do not exist, so no real parent can be a shared or versioned tree.
    home = {"HOME": "/nonexistent-ledger-home"}
    assert ledger_dir(home) == Path("/nonexistent-ledger-home/.local/state/synapse/ledger")
    assert ledger_dir({"USERPROFILE": "/nonexistent-profile"}).parts[1] == "nonexistent-profile"
    state = Path("/nonexistent-ledger-state")
    assert ledger_dir({"XDG_STATE_HOME": str(state)}) == state / "synapse/ledger"
    # A relative XDG_STATE_HOME is not a base directory; the home is used.
    assert ledger_dir({"XDG_STATE_HOME": "rel", **home}) == ledger_dir(home)
    with pytest.raises(NoLedger):
        ledger_dir({})


@pytest.mark.parametrize("marker", ["git-dir", "git-file", "coordination"])
def test_a_shared_or_versioned_location_is_refused(marker: str, tmp_path: Path) -> None:
    tree = tmp_path / ("repo" if marker != "coordination" else ".coordination") / "deep"
    tree.mkdir(parents=True)
    if marker == "git-dir":
        (tmp_path / "repo" / ".git").mkdir()
        (tmp_path / "repo" / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    elif marker == "git-file":
        (tmp_path / "repo" / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")
    with pytest.raises(LedgerError, match="unset XDG_STATE_HOME or point it at ~/.local/state"):
        ledger_dir({"XDG_STATE_HOME": str(tree)})
    assert shared_tree(tree / "synapse") == tree.parent


def test_a_stray_git_directory_without_a_head_is_not_a_work_tree(tmp_path: Path) -> None:
    # Whatever surrounds the test's own directory stays what it is.
    outer = shared_tree(tmp_path.parent)
    state = tmp_path / ".local" / "state"
    # A home directory with a leftover ``.git/info`` is not a repository; git says so too.
    (tmp_path / ".git" / "info").mkdir(parents=True)
    assert shared_tree(state) == outer
    (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    assert shared_tree(state) == tmp_path


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="/proc is Linux-only")
def test_a_rows_key_is_the_boot_the_pid_and_its_start_ticks() -> None:
    assert boot_id()
    started = pid_starttime(os.getpid())
    assert isinstance(started, int) and started > 0
    assert pid_starttime(2**22 + 12345) is None
    assert row_id("b", 7, 99) == "b:7:99"


def test_no_boot_id_reads_as_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    def unreadable(self: Path, encoding: str = "") -> str:
        raise OSError("no /proc here")

    monkeypatch.setattr(Path, "read_text", unreadable)
    assert boot_id() == ""


# --- writing lines --------------------------------------------------------------------


def test_lines_carry_the_common_fields_and_a_rising_seq(tmp_path: Path) -> None:
    directory = tmp_path / "ledger"
    first = append_event(
        {"event": "start", "row_id": "r"}, directory=directory, boot=BOOT, host="h"
    )
    second = append_event(
        {"event": "observe", "row_id": "r"}, directory=directory, boot=BOOT, host="h"
    )
    assert (first, second) == (1, 2)
    lines = _lines(directory)
    assert [line["seq"] for line in lines] == [1, 2]
    assert all(line["schema"] == SCHEMA and line["host"] == "h" for line in lines)
    assert all(line["boot_id"] == BOOT and line["ts"].endswith("Z") for line in lines)
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    for path in directory.iterdir():
        assert stat.S_IMODE(path.stat().st_mode) == 0o600, path


def test_a_new_boot_starts_its_seq_again(tmp_path: Path) -> None:
    append_event({"event": "start", "row_id": "r"}, directory=tmp_path, boot=BOOT)
    assert append_event({"event": "start", "row_id": "s"}, directory=tmp_path, boot="boot-b") == 1


def test_a_crash_between_seq_and_line_leaves_a_gap_never_a_repeat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The number is stored before the line, so a crash between them costs a number."""
    append_event({"event": "start", "row_id": "r"}, directory=tmp_path, boot=BOOT)
    real = session_ledger._append_line

    def crash(path: Path, line: bytes) -> None:
        raise OSError("killed between the two steps")

    monkeypatch.setattr(session_ledger, "_append_line", crash)
    with pytest.raises(LedgerError):
        append_event({"event": "observe", "row_id": "r"}, directory=tmp_path, boot=BOOT)
    monkeypatch.setattr(session_ledger, "_append_line", real)
    assert append_event({"event": "end", "row_id": "r"}, directory=tmp_path, boot=BOOT) == 3
    assert [line["seq"] for line in _lines(tmp_path)] == [1, 3]


def test_a_failed_write_leaves_no_partial_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A disk-full or short write is cut back to the previous size."""
    append_event({"event": "start", "row_id": "r"}, directory=tmp_path, boot=BOOT)
    events = next(tmp_path.glob("events-*.jsonl"))
    size = events.stat().st_size
    real_write = os.write

    def short(fd: int, data: bytes) -> int:
        if b'"schema"' in data:
            return real_write(fd, data[: len(data) // 2])
        return real_write(fd, data)

    def full(fd: int, data: bytes) -> int:
        if b'"schema"' in data:
            raise OSError(errno.ENOSPC, "No space left on device")
        return real_write(fd, data)

    for failing in (short, full):
        monkeypatch.setattr(os, "write", failing)
        with pytest.raises(LedgerError):
            append_event({"event": "observe", "row_id": "r"}, directory=tmp_path, boot=BOOT)
        monkeypatch.setattr(os, "write", real_write)
        assert events.stat().st_size == size


def test_an_unterminated_last_line_is_skipped_then_cut(tmp_path: Path) -> None:
    append_event({"event": "start", "row_id": "r"}, directory=tmp_path, boot=BOOT)
    events = next(tmp_path.glob("events-*.jsonl"))
    with events.open("ab") as stream:
        stream.write(b'{"schema": "synapse.session-ledger.v1", "event": "obs')
    assert [line["event"] for line in _lines(tmp_path)] == ["start"]
    append_event({"event": "end", "row_id": "r"}, directory=tmp_path, boot=BOOT)
    text = events.read_text(encoding="utf-8")
    assert text.endswith("\n") and '"obs' not in text
    assert [line["event"] for line in _lines(tmp_path)] == ["start", "end"]


def test_a_whole_but_unterminated_last_line_is_not_read(tmp_path: Path) -> None:
    """A writer that died before its newline left a complete object: it is still not a line."""
    append_event({"event": "start", "row_id": "r"}, directory=tmp_path, boot=BOOT)
    events = next(tmp_path.glob("events-*.jsonl"))
    with events.open("ab") as stream:
        stream.write(
            json.dumps({"schema": SCHEMA, "event": "end", "row_id": "r", "seq": 9}).encode()
        )
    assert [line["event"] for line in _lines(tmp_path)] == ["start"]


def test_other_lines_are_not_read_as_v1(tmp_path: Path) -> None:
    (tmp_path / "events-2026-11.jsonl").write_text(
        'not json\n{"schema": "ws-session-ledger.v0", "event": "observed"}\n["a list"]\n',
        encoding="utf-8",
    )
    assert _lines(tmp_path) == []


def test_a_held_lock_costs_a_launcher_its_line_not_its_time(tmp_path: Path) -> None:
    """The wait is bounded; the line is given up and reported."""
    append_event({"event": "start", "row_id": "r"}, directory=tmp_path, boot=BOOT)
    fd = os.open(tmp_path / "ledger.lock", os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        started = time.monotonic()
        with pytest.raises(LedgerError, match="the line was given up after 0.2 s"):
            append_event({"event": "end", "row_id": "r"}, directory=tmp_path, boot=BOOT, wait=0.2)
        assert time.monotonic() - started < 1.0
    finally:
        os.close(fd)


def test_an_unusable_directory_is_a_ledger_error(tmp_path: Path) -> None:
    target = tmp_path / "file"
    target.write_text("", encoding="utf-8")
    with pytest.raises(LedgerError, match="could not be written"):
        append_event({"event": "start", "row_id": "r"}, directory=target, boot=BOOT)


def test_a_corrupt_sequence_file_starts_again(tmp_path: Path) -> None:
    (tmp_path / "seq.json").write_text("not json", encoding="utf-8")
    tmp_path.chmod(0o700)
    assert append_event({"event": "start", "row_id": "r"}, directory=tmp_path, boot=BOOT) == 1


_WRITER = r"""
import sys
from pathlib import Path
from synapse_channel.session_ledger import append_event
for i in range(40):
    append_event({"event": "observe", "row_id": sys.argv[2], "n": i}, directory=Path(sys.argv[1]),
                 boot="boot-a", wait=30)
"""


def test_two_writers_at_once_give_whole_lines_and_unique_numbers(tmp_path: Path) -> None:
    writers = [
        subprocess.Popen([sys.executable, "-c", _WRITER, str(tmp_path), name]) for name in "ab"
    ]
    assert [writer.wait(60) for writer in writers] == [0, 0]
    lines = _lines(tmp_path)
    seqs = [line["seq"] for line in lines]
    assert len(lines) == 80 and seqs == sorted(seqs) and len(set(seqs)) == 80


# --- folding rows -----------------------------------------------------------------------


def _event(kind: str, seq: int, **fields: Any) -> dict[str, Any]:
    return {"schema": SCHEMA, "event": kind, "seq": seq, "row_id": "r", "boot_id": BOOT, **fields}


def test_a_row_takes_its_first_start_and_merges_observations() -> None:
    rows = fold(
        [
            _event("start", 1, launcher="seat-hold", identity="T/x"),
            _event("start", 2, launcher="observed"),
            _event("observe", 3, native_session_id="n1"),
            _event("observe", 4, waiter={"state": "stopped", "reason": "binding"}),
            {"event": "observe", "seq": 5},  # no row: ignored
        ]
    )
    row = rows["r"]
    assert row.start["launcher"] == "seat-hold" and row.open
    assert row.observed == {
        "native_session_id": "n1",
        "waiter": {"state": "stopped", "reason": "binding"},
    }


def test_the_first_end_is_final() -> None:
    row = fold(
        [
            _event("start", 1, launcher="seat-hold"),
            _event("end", 2, launcher="seat-hold", end_kind="exit", exit_status=0),
            _event("ended_unobserved", 3, end_kind="observed_gone"),
            _event("end", 4, launcher="seat-hold", end_kind="exit", exit_status=1),
        ]
    )["r"]
    assert row.end is not None and row.end["seq"] == 2 and not row.open


@pytest.mark.parametrize(
    ("late", "taken"),
    [
        ({"launcher": "seat-hold", "supersedes_seq": 2}, True),
        ({"launcher": "worker-session", "supersedes_seq": 2}, False),  # not the row's launcher
        ({"launcher": "seat-hold", "supersedes_seq": 2, "boot_id": "boot-b"}, False),  # other boot
        ({"launcher": "seat-hold", "supersedes_seq": 9}, False),  # names another line
    ],
)
def test_a_late_real_end_supersedes_once_and_only_from_its_launcher(
    late: dict[str, Any], taken: bool
) -> None:
    """Only the row's launcher, from the same boot, naming the end it corrects."""
    events = [
        _event("start", 1, launcher="seat-hold"),
        _event("ended_unobserved", 2, end_kind="observed_gone"),
        _event("end", 3, end_kind="exit", exit_status=0, **late),
        _event("end", 4, launcher="seat-hold", end_kind="exit", exit_status=5, supersedes_seq=2),
    ]
    row = fold(events)["r"]
    # A refused late end is ignored, and the valid correction after it is still taken.
    assert row.end is not None and row.end["seq"] == (3 if taken else 4)


def test_a_real_end_is_never_superseded() -> None:
    row = fold(
        [
            _event("start", 1, launcher="seat-hold"),
            _event("end", 2, launcher="seat-hold", end_kind="exit", exit_status=0),
            _event(
                "end", 3, launcher="seat-hold", end_kind="exit", exit_status=9, supersedes_seq=2
            ),
        ]
    )["r"]
    assert row.end is not None and row.end["seq"] == 2 and not row.corrected


def test_a_boot_cut_and_an_observed_rows_end_are_final() -> None:
    cut = fold(
        [
            _event("start", 1, launcher="seat-hold"),
            _event("ended_unobserved", 2, end_kind="boot_cut"),
            _event("end", 3, launcher="seat-hold", end_kind="exit", supersedes_seq=2),
        ]
    )["r"]
    observed = fold(
        [
            _event("start", 1, launcher="observed"),
            _event("ended_unobserved", 2, end_kind="observed_gone"),
            _event("end", 3, launcher="observed", end_kind="exit", supersedes_seq=2),
        ]
    )["r"]
    assert cut.end is not None and cut.end["seq"] == 2
    assert observed.end is not None and observed.end["seq"] == 2


def test_only_one_correction_is_taken() -> None:
    row = fold(
        [
            _event("start", 1, launcher="seat-hold"),
            _event("ended_unobserved", 2, end_kind="observed_gone"),
            _event(
                "end", 3, launcher="seat-hold", end_kind="exit", exit_status=0, supersedes_seq=2
            ),
            _event(
                "end", 4, launcher="seat-hold", end_kind="exit", exit_status=9, supersedes_seq=2
            ),
        ]
    )["r"]
    assert row.end is not None and row.end["seq"] == 3 and row.corrected


def test_events_from_a_real_file_fold_into_rows(tmp_path: Path) -> None:
    append_event(
        {"event": "start", "row_id": "r", "launcher": "seat-hold"}, directory=tmp_path, boot=BOOT
    )
    append_event(
        {
            "event": "end",
            "row_id": "r",
            "launcher": "seat-hold",
            "end_kind": "exit",
            "exit_status": 0,
        },
        directory=tmp_path,
        boot=BOOT,
    )
    row = fold(read_events(tmp_path))["r"]
    assert row.end is not None and row.end["exit_status"] == 0
    assert json.loads(json.dumps(row.start))["launcher"] == "seat-hold"


# --- the launcher's own lines ----------------------------------------------------------


def _record(tmp_path: Path, reports: list[str] | None = None) -> session_ledger.LauncherRecord:
    sink = reports if reports is not None else []
    return session_ledger.LauncherRecord("seat-hold", directory=tmp_path, report=sink.append)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="/proc is Linux-only")
def test_a_launcher_writes_start_observe_and_end_for_its_process(tmp_path: Path) -> None:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        record = _record(tmp_path)
        record.started(
            child.pid,
            identity="P/seat",
            argv=["/usr/lib/node_modules/codex/bin/codex.js", "resume"],
            cwd=tmp_path,
            holder="P/seat-seat",
            tmux={"session": "synapse-P_seat", "pane": "%3"},
        )
        record.observed(waiter={"state": "stopped", "reason": "binding refused"})
        child.terminate()
        record.ended(child.wait(10))
    finally:
        if child.poll() is None:
            child.kill()
    start, observe, end = _lines(tmp_path)
    assert start["event"] == "start" and start["row_id"] == record.row
    assert (start["identity"], start["project"], start["vendor"]) == ("P/seat", "P", "codex")
    assert (start["launcher"], start["holder"], start["pid"]) == (
        "seat-hold",
        "P/seat-seat",
        child.pid,
    )
    assert start["tmux"] == {"session": "synapse-P_seat", "pane": "%3"} and start["cwd"] == str(
        tmp_path
    )
    assert start["permission_profile"] is None and start["restore_priority"] is None
    assert "argv" not in start and "env" not in start
    assert observe["waiter"] == {"state": "stopped", "reason": "binding refused"}
    assert (end["event"], end["end_kind"], end["signal"]) == ("end", "signal", "SIGTERM")


def test_an_exit_status_and_an_unknown_signal_are_recorded(tmp_path: Path) -> None:
    record = _record(tmp_path)
    record.row = "b:1:2"
    record.ended(3)
    record.ended(-200)
    exit_line, signal_line = _lines(tmp_path)
    assert (exit_line["end_kind"], exit_line["exit_status"]) == ("exit", 3)
    assert (signal_line["end_kind"], signal_line["signal"]) == ("signal", "200")


def test_nothing_is_written_without_a_row_or_a_status(tmp_path: Path) -> None:
    record = _record(tmp_path)
    record.started(2**22 + 12345, identity="bare", argv=["x"], cwd=tmp_path)  # no such process
    record.observed(waiter={"state": "stopped"})
    record.ended(0)
    record.row = "b:1:2"
    record.ended(None)
    assert record.row == "b:1:2" and not list(tmp_path.glob("events-*"))


def test_a_failed_line_is_reported_and_no_ledger_is_silent(tmp_path: Path) -> None:
    reports: list[str] = []
    blocked = tmp_path / "file"
    blocked.write_text("", encoding="utf-8")
    record = session_ledger.LauncherRecord("seat-hold", directory=blocked, report=reports.append)
    record.row = "b:1:2"
    record.ended(0)
    assert len(reports) == 1 and reports[0].startswith("synapse ledger: the ledger line")
    quiet: list[str] = []
    none = session_ledger.LauncherRecord("seat-hold", environ={}, report=quiet.append)
    none.row = "b:1:2"
    none.ended(0)
    assert quiet == []


def test_the_default_report_goes_to_standard_error(capsys: pytest.CaptureFixture[str]) -> None:
    session_ledger._report("synapse ledger: x")
    assert capsys.readouterr().err == "synapse ledger: x\n"
