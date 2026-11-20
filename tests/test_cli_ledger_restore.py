# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests: `synapse ledger restore`, its dry run and its execution
"""``synapse ledger restore``: a dry run writes nothing; ``--execute`` holds the lock and starts."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import socket
import stat
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from synapse_channel import cli_ledger_restore
from synapse_channel.cli import main as cli_main
from synapse_channel.restore_plan import Entry
from synapse_channel.restore_run import Outcome, RestoreBusyError, Run, restore_lock
from synapse_channel.seat_hold import (
    _own_regular,
    _unlink_own_regular,
    nonces_of_live_holders,
    sweep_stale_token_files,
    token_file_nonce,
)
from synapse_channel.seat_restore import MARKER_WAIT_SECONDS
from synapse_channel.session_ledger import (
    SCHEMA,
    LedgerError,
    Row,
    append_event,
    boot_id,
    pid_starttime,
)
from synapse_channel.session_ledger_collect import Proc

SID = "11111111-2222-3333-4444-555555555555"
PREV = "boot-prev"


def _tmux(
    tmp_path: Path, *, list_panes: str = "no server running on /tmp/x", taken: bool = False
) -> str:
    script = tmp_path / "tmux-fake"
    script.write_text(
        "#!/bin/sh\n"
        'case "$1" in\n'
        f"  has-session) exit {0 if taken else 1};;\n"
        f"  list-panes) echo '{list_panes}' >&2; exit 1;;\n"
        "esac\nexit 1\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return str(script)


def _seat(ledger: Path, identity: str, pid: int, cwd: Path, *, end: str = "boot_cut") -> str:
    row = f"{PREV}:{pid}:7"
    start = {
        "event": "start",
        "row_id": row,
        "identity": identity,
        "project": identity.partition("/")[0],
        "vendor": "claude",
        "pid": pid,
        "pid_starttime": 7,
        "cwd": str(cwd),
        "started_at": "2026-11-18T10:00:00Z",
        "launcher": "seat-hold",
        "holder": None,
        "tmux": None,
        "permission_profile": None,
        "restore_priority": None,
    }
    append_event(start, directory=ledger, boot=PREV)
    native = {"event": "observe", "row_id": row, "native_session_id": SID}
    append_event(native, directory=ledger, boot=PREV)
    append_event(
        {"event": "ended_unobserved", "row_id": row, "end_kind": end}, directory=ledger, boot=PREV
    )
    return row


@pytest.fixture(autouse=True)
def marker_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Keep the token sweep on a private directory, never the host's runtime directory."""
    directory = tmp_path / "seat-markers"
    monkeypatch.setattr(cli_ledger_restore, "default_marker_dir", lambda environ=None: directory)
    return directory


@pytest.fixture
def ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A ledger with two seats a reboot cut, one view file each, and a quiet host."""
    directory = tmp_path / "ledger"
    work = tmp_path / "work"
    work.mkdir()
    _seat(directory, "P/a", 10, work)
    _seat(directory, "P/b", 11, work)
    (directory / "boots").mkdir(mode=0o700)
    view = json.dumps({"schema": SCHEMA, "boot_id": PREV, "recorded_at": "x", "rows": {}})
    (directory / "current.json").write_text(view, encoding="utf-8")
    (directory / "boots" / f"{PREV}.json").write_text(view, encoding="utf-8")
    monkeypatch.setattr(cli_ledger_restore, "ledger_dir", lambda: directory)
    monkeypatch.setattr(cli_ledger_restore, "read_procs", dict)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    return directory


def _tree(directory: Path) -> dict[str, tuple[bytes | None, int, int]]:
    """Every entry under ``directory``: its bytes, its mtime and its mode."""
    found = {}
    for path in sorted([directory, *directory.rglob("*")]):
        status = path.stat()
        content = path.read_bytes() if path.is_file() else None
        found[str(path.relative_to(directory))] = (content, status.st_mtime_ns, status.st_mode)
    return found


def _restore(tmp_path: Path, *extra: str, tmux: str | None = None) -> int:
    return cli_main(
        [
            "ledger",
            "restore",
            "--tmux-bin",
            tmux or _tmux(tmp_path),
            "--synapse-bin",
            sys.executable,
            *extra,
        ]
    )


def test_a_dry_run_writes_nothing_at_all(
    tmp_path: Path, ledger: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    before = _tree(ledger)
    assert _restore(tmp_path) == 0
    assert _tree(ledger) == before  # bytes, mtimes and modes, seq.json and current.json too
    out = capsys.readouterr().out.splitlines()
    assert out[0] == (
        f"synapse ledger restore (dry run, nothing is written) on {socket.gethostname()}: "
        f"boot {PREV} (the previous boot)"
    )
    assert out[1].startswith("headroom: MemAvailable >= max(4 GiB, 15% of MemTotal)")
    assert out[2].startswith("environment: passes ")
    assert out[3] == (
        f"P/a  start  claude  {PREV}:10:7  claude --resume {SID}  in {tmp_path / 'work'}  "
        "tmux synapse-P_a"
    )
    assert out[-1] == "2 to start"


def test_a_dry_run_of_a_host_with_no_ledger_creates_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    absent = tmp_path / "absent"
    monkeypatch.setattr(cli_ledger_restore, "ledger_dir", lambda: absent)
    monkeypatch.setattr(cli_ledger_restore, "read_procs", dict)
    assert _restore(tmp_path) == 0
    assert not absent.exists()
    assert capsys.readouterr().out.splitlines()[-1] == "0 to start"


def test_the_plan_as_json_names_the_environment_but_never_a_value(
    tmp_path: Path,
    ledger: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("SYN_HOME", "/secret/place")
    monkeypatch.setenv("SECRET_API_KEY", "value-never-shown")
    config = tmp_path / "config" / "synapse"
    config.mkdir(parents=True)
    looser = {"hosts": {socket.gethostname(): {"max_load_per_cpu": 2.0, "spacing_seconds": 60}}}
    (config / "restore.json").write_text(json.dumps(looser), encoding="utf-8")
    assert _restore(tmp_path, "--json", "--only", "P/b") == 0
    printed = capsys.readouterr().out
    assert "value-never-shown" not in printed and "/secret/place" not in printed
    document = json.loads(printed)
    assert document["mode"] == "dry run" and document["boot"] == PREV
    assert document["boot_by_default"] is True and document["current_boot"] == boot_id()
    assert [entry["identity"] for entry in document["entries"]] == ["P/b"]
    assert document["limits"]["spacing_seconds"] == 60.0
    assert document["limits"]["max_load_per_cpu"] == 0.75
    assert document["notes"] == ["max_load_per_cpu: 2.0 is looser than the default 0.75, ignored"]
    assert "SYN_IDENTITY" in document["environment"]["passes"]
    assert "SECRET_API_KEY" not in document["environment"]["passes"]
    assert "SYN_HOME (redirects Synapse state)" in document["environment"]["drops"]


def test_a_named_boot_reads_that_boots_rows(
    tmp_path: Path, ledger: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _restore(tmp_path, "--boot", "boot-other") == 0
    out = capsys.readouterr().out.splitlines()
    assert "boot boot-other (the boot named)" in out[0]
    assert out[-1] == "0 to start" and "cut in another boot" in out[3]


def test_a_tmux_name_in_use_is_shown_and_not_started(
    tmp_path: Path, ledger: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _restore(tmp_path, tmux=_tmux(tmp_path, taken=True)) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[3] == f"P/a  tmux name in use  claude  {PREV}:10:7"


@pytest.mark.parametrize("execute", [[], ["--execute"]])
def test_a_host_that_cannot_be_read_stops_before_anything(
    tmp_path: Path,
    ledger: Path,
    execute: list[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    before = _tree(ledger)
    assert _restore(tmp_path, *execute, tmux=_tmux(tmp_path, list_panes="permission denied")) == 3
    assert "the host could not be read" in capsys.readouterr().out
    after = _tree(ledger)
    if execute:  # an execution's lock file is the one thing it may add, and so the folder
        after.pop("restore.lock")
        after.pop(".")
        before.pop(".")
    assert after == before  # no line, no seq, no view


def test_a_ledger_in_a_shared_tree_or_a_bad_configuration_is_refused(
    tmp_path: Path,
    ledger: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = tmp_path / "config" / "synapse"
    config.mkdir(parents=True)
    (config / "restore.json").write_text("{", encoding="utf-8")
    assert _restore(tmp_path) == 2
    assert "cannot be read" in capsys.readouterr().out

    def refused() -> Path:
        raise LedgerError("the session ledger would lie in a shared or versioned tree (/x)")

    monkeypatch.setattr(cli_ledger_restore, "ledger_dir", refused)
    assert _restore(tmp_path) == 2
    assert "shared or versioned tree" in capsys.readouterr().out


def test_a_synapse_executable_that_cannot_be_found_is_refused(
    tmp_path: Path, ledger: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = cli_main(["ledger", "restore", "--synapse-bin", str(tmp_path / "no-such-synapse")])
    assert code == 2
    assert "is not an executable on PATH" in capsys.readouterr().out


# --- execution -----------------------------------------------------------------------


def test_execute_snapshots_holds_the_lock_and_starts_the_plan(
    tmp_path: Path,
    ledger: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    seen: dict[str, Any] = {}

    async def fake_execute(entries: Sequence[Entry], run: Run) -> tuple[int, list[Outcome]]:
        seen["entries"], seen["run"] = list(entries), run
        with pytest.raises(RestoreBusyError):  # a second planner is refused meanwhile
            with restore_lock(ledger):
                pass
        run.report("a progress line")
        return 0, [Outcome("P/a", entries[0].row_id, "started", 0, "ok", "new-row")]

    monkeypatch.setattr(cli_ledger_restore, "execute", fake_execute)
    assert _restore(tmp_path, "--execute", "--json", "--uri", "ws://127.0.0.1:9") == 0
    captured = capsys.readouterr()
    document = json.loads(captured.out)
    assert captured.err == "a progress line\n"  # progress never mixes into the JSON
    assert document["mode"] == "execute" and document["exit_code"] == 0
    assert document["outcomes"] == [
        {
            "identity": "P/a",
            "row_id": f"{PREV}:10:7",
            "state": "started",
            "code": 0,
            "detail": "ok",
            "new_row_id": "new-row",
        }
    ]
    run = seen["run"]
    assert [entry.identity for entry in seen["entries"]] == ["P/a", "P/b"]
    assert run.uri == "ws://127.0.0.1:9" and run.synapse_bin == sys.executable
    assert run.grace.name == f"{socket.gethostname()}-restore-planner"
    # The real snapshot was taken: this boot's view is written.
    assert json.loads((ledger / "current.json").read_text(encoding="utf-8"))["boot_id"] == boot_id()
    with restore_lock(ledger):  # and the lock is free again
        pass


def test_execute_notes_a_real_signal_and_exits_130(
    tmp_path: Path, ledger: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_execute(entries: Sequence[Entry], run: Run) -> tuple[int, list[Outcome]]:
        # The run's interrupt holds SIGTERM while it runs; only then is one sent.
        assert signal.getsignal(signal.SIGTERM) not in (signal.SIG_DFL, signal.SIG_IGN, None)
        os.kill(os.getpid(), signal.SIGTERM)
        await asyncio.wait_for(run.interrupt.requested.wait(), 5.0)
        return 130, []

    monkeypatch.setattr(cli_ledger_restore, "execute", fake_execute)
    assert _restore(tmp_path, "--execute", "--uri", "ws://127.0.0.1:9") == 130
    assert signal.getsignal(signal.SIGTERM) == signal.SIG_DFL  # given back afterwards


def test_execute_reports_progress_on_standard_output_without_json(
    tmp_path: Path,
    ledger: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rechecked: list[str | None] = []

    async def fake_execute(entries: Sequence[Entry], run: Run) -> tuple[int, list[Outcome]]:
        # Before each start the run takes a fresh snapshot of the host.
        rechecked.append(run.recheck(entries[0]))
        run.report("P/a  live (75)")
        return 69, []

    config = tmp_path / "config" / "synapse"
    config.mkdir(parents=True)
    looser = {"hosts": {socket.gethostname(): {"spacing_seconds": 5}}}
    (config / "restore.json").write_text(json.dumps(looser), encoding="utf-8")
    monkeypatch.setattr(cli_ledger_restore, "execute", fake_execute)
    assert _restore(tmp_path, "--execute") == 69
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("synapse ledger restore (executing) on ")
    assert out[2] == "note: spacing_seconds: 5 is looser than the default 30.0, ignored"
    assert out[-1] == "P/a  live (75)"
    assert rechecked == [None]  # nothing changed between the plan and the start


def test_execute_with_nothing_to_start_connects_to_nothing(
    tmp_path: Path,
    ledger: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def never(entries: Sequence[Entry], run: Run) -> tuple[int, list[Outcome]]:
        raise AssertionError("nothing to start runs nothing")

    monkeypatch.setattr(cli_ledger_restore, "execute", never)
    assert _restore(tmp_path, "--execute", "--only", "P/none") == 0
    assert capsys.readouterr().out.splitlines()[-1] == "0 to start"


def test_a_held_lock_does_not_sweep_a_stale_token_file(
    tmp_path: Path, ledger: Path, marker_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    old = _token(marker_dir, f"T_seat.{NONCE}.token")
    _age(old)
    with restore_lock(ledger):
        assert _restore(tmp_path, "--execute") == 75
    assert old.is_file()
    assert capsys.readouterr().out == (
        "synapse ledger restore: another restore run holds the lock; this run stops\n"
    )


NONCE = "nonce-0001"


def _token(directory: Path, name: str, text: str = "secret\n") -> Path:
    directory.mkdir(exist_ok=True)
    path = directory / name
    path.write_text(text, encoding="utf-8")
    return path


def _age(path: Path) -> None:
    """Move ``path``'s mtime far enough past the sweep's young-file bound."""
    bound = MARKER_WAIT_SECONDS + cli_ledger_restore.TOKEN_SWEEP_MARGIN_SECONDS
    old = time.time() - bound - 10
    os.utime(path, (old, old))


def test_only_a_cut_holders_regular_token_file_is_stale(marker_dir: Path) -> None:
    stale = _token(marker_dir, f"T_seat.{NONCE}.token")
    kept = _token(marker_dir, "other.token", "keep\n")
    _token(marker_dir, "T_seat.short.token")
    _token(marker_dir, "T seat.nonce-0002.token")
    assert token_file_nonce(stale.name) == NONCE
    assert token_file_nonce("notes.txt") is None
    assert token_file_nonce(f"{NONCE}.token") is None
    assert token_file_nonce(f".{NONCE}.token") is None
    assert token_file_nonce("a.b.nonce-0002.token") == "nonce-0002"
    nested = marker_dir / "T_seat.nonce-0003.token"
    nested.mkdir()
    link = marker_dir / "T_seat.nonce-0004.token"
    link.symlink_to(kept)
    assert sweep_stale_token_files(marker_dir, frozenset(), execute=False, minimum_age=0) == 1
    assert stale.is_file() and kept.is_file() and nested.is_dir() and link.is_symlink()
    assert sweep_stale_token_files(marker_dir, frozenset({NONCE}), execute=True, minimum_age=0) == 0
    assert stale.is_file()
    assert (
        sweep_stale_token_files(
            marker_dir, frozenset(), execute=True, minimum_age=0, uid=os.geteuid() + 1
        )
        == 0
    )
    assert stale.read_text(encoding="utf-8") == "secret\n"
    assert (
        sweep_stale_token_files(marker_dir / "missing", frozenset(), execute=True, minimum_age=0)
        == 0
    )
    assert sweep_stale_token_files(kept, frozenset(), execute=True, minimum_age=0) == 0
    assert sweep_stale_token_files(marker_dir, frozenset(), execute=True, minimum_age=0) == 1
    assert not stale.exists()
    assert kept.is_file() and nested.is_dir() and link.is_symlink()


def test_a_dry_run_does_not_count_another_users_stale_token_file(marker_dir: Path) -> None:
    stale = _token(marker_dir, f"T_seat.{NONCE}.token")
    _age(stale)
    assert (
        sweep_stale_token_files(
            marker_dir, frozenset(), execute=False, minimum_age=0, uid=os.geteuid() + 1
        )
        == 0
    )
    assert stale.is_file()


def test_a_live_holders_stale_token_file_is_not_counted(
    tmp_path: Path,
    ledger: Path,
    marker_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    stale = _token(marker_dir, f"T_seat.{NONCE}.token")
    _age(stale)
    holder = Proc(os.getpid(), 1, ("synapse", "seat-hold", "--nonce", NONCE), 1)
    monkeypatch.setattr(cli_ledger_restore, "read_procs", lambda: {os.getpid(): holder})
    assert _restore(tmp_path) == 0
    assert "stale token files: 0" in capsys.readouterr().out
    assert stale.is_file()


def test_a_token_file_that_vanishes_or_cannot_be_removed_is_not_counted(
    marker_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stale = _token(marker_dir, f"T_seat.{NONCE}.token")
    real_lstat = Path.lstat

    def gone(self: Path) -> os.stat_result:
        if self.name == stale.name:
            raise OSError("gone")
        return real_lstat(self)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "lstat", gone)
        assert sweep_stale_token_files(marker_dir, frozenset(), execute=True, minimum_age=0) == 0
    assert stale.is_file()

    def refuse(path: Path, uid: int) -> None:
        raise OSError("busy")

    monkeypatch.setattr("synapse_channel.seat_hold._unlink_own_regular", refuse)
    assert sweep_stale_token_files(marker_dir, frozenset(), execute=True, minimum_age=0) == 0
    assert stale.is_file()


def test_unlink_refuses_a_directory_that_matches_the_token_name(tmp_path: Path) -> None:
    directory = tmp_path / f"T_seat.{NONCE}.token"
    directory.mkdir()
    uid = os.geteuid()
    assert _own_regular(stat.S_IFREG, uid, uid)
    assert not _own_regular(stat.S_IFDIR, uid, uid)
    assert not _own_regular(stat.S_IFREG, uid + 1, uid)
    with pytest.raises(OSError):
        _unlink_own_regular(directory, uid)
    assert directory.is_dir()


def test_live_holder_nonces_come_only_from_this_users_seat_hold() -> None:
    nonce = NONCE
    commands = {
        1: ("synapse", "seat-hold", "--nonce", nonce, "--", "true"),
        2: ("synapse", "seat-hold", f"--nonce={nonce}"),
        3: ("synapse", "seat-hold", "--nonce"),
        4: ("python", "--nonce", nonce),
        5: ("synapse", "seat-hold", "--identity", "T/seat"),
    }
    owners = {1: 7, 2: 7, 3: 7, 4: 7, 5: 9}

    def owner_of(pid: int) -> int | None:
        return owners.get(pid)

    assert nonces_of_live_holders(commands, uid=7, owner_of=owner_of) == frozenset({nonce})
    assert nonces_of_live_holders(commands, uid=9, owner_of=owner_of) == frozenset()
    assert nonces_of_live_holders({4: commands[4]}, uid=7, owner_of=owner_of) == frozenset()
    assert cli_ledger_restore._process_uid(os.getpid()) == os.getuid()
    assert cli_ledger_restore._process_uid(1 << 30) is None


def test_a_dry_run_counts_a_cut_token_file_and_execute_removes_it(
    tmp_path: Path,
    ledger: Path,
    marker_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    stale = _token(marker_dir, f"T_seat.{NONCE}.token")
    fresh = _token(marker_dir, "T_seat.nonce-0008.token", "fresh\n")
    _age(stale)
    assert _restore(tmp_path) == 0
    dry = capsys.readouterr().out
    assert "stale token files: 1" in dry
    assert NONCE not in dry and "T_seat" not in dry and "secret" not in dry and "fresh" not in dry
    assert stale.is_file() and fresh.is_file()
    assert _restore(tmp_path, "--execute", "--only", "P/none") == 0
    done = capsys.readouterr().out
    assert "stale token files: 1" in done
    assert NONCE not in done and "secret" not in done and "fresh" not in done
    assert not stale.exists() and fresh.is_file()


def test_the_restore_sees_a_live_holder_nonce(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proc = Proc(os.getpid(), 1, ("synapse", "seat-hold", "--nonce", NONCE), 1)
    monkeypatch.setattr(cli_ledger_restore, "read_procs", lambda: {os.getpid(): proc})
    assert cli_ledger_restore._live_holder_nonces() == frozenset({NONCE})


# --- the host's facts ----------------------------------------------------------------


def test_a_rows_process_is_alive_only_with_its_own_start_ticks() -> None:
    """A row is alive only by its own start ticks; a host without them has no live row."""
    mine = pid_starttime(os.getpid())
    assert cli_ledger_restore.process_alive(
        Row("r", start={"pid": os.getpid(), "pid_starttime": mine})
    ) is (mine is not None)
    assert not cli_ledger_restore.process_alive(
        Row("r", start={"pid": os.getpid(), "pid_starttime": (mine or 0) + 1})
    )
    assert not cli_ledger_restore.process_alive(Row("r", start={"pid": "1", "pid_starttime": 1}))
    assert not cli_ledger_restore.process_alive(Row("r", start={"pid": 1}))


def test_a_tmux_session_is_taken_only_when_tmux_says_so(tmp_path: Path) -> None:
    assert cli_ledger_restore.tmux_taken(_tmux(tmp_path, taken=True), "s")
    assert not cli_ledger_restore.tmux_taken(_tmux(tmp_path), "s")
    assert not cli_ledger_restore.tmux_taken(str(tmp_path / "no-tmux"), "s")
