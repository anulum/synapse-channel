# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the session ledger's collector
"""The collector: what it reads from processes and tmux, and what it records."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest

from synapse_channel import cli_ledger, session_ledger_collect
from synapse_channel.session_ledger import LedgerError, append_event, fold, read_events
from synapse_channel.session_ledger_collect import (
    CollectError,
    Host,
    Proc,
    agent_vendor,
    agy_root,
    collect,
    descendants,
    named_environ,
    native_session,
    observe_host,
    open_files,
    read_procs,
    topmost_agent,
)

BOOT = "boot-a"
SID = "11111111-2222-3333-4444-555555555555"
PARENT = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

PROCS = {
    100: Proc(100, 1, ("tmux", "new-session"), 10),
    200: Proc(200, 100, ("/bin/sh",), 20),  # a pane's shell
    300: Proc(300, 200, ("node", "/usr/lib/codex/bin/codex.js"), 30),
    310: Proc(310, 300, ("codex", "--helper"), 31),  # the same agent's child
    400: Proc(400, 100, ("/bin/bash",), 40),  # a pane with no agent
    500: Proc(500, 1, ("claude", "--resume", SID), 50),  # an agent outside tmux
}


class FakeTmux:
    """Answers tmux queries from a table; records what was asked."""

    def __init__(self, answers: Mapping[str, tuple[int, str, str]]) -> None:
        self.answers = answers
        self.calls: list[list[str]] = []

    def __call__(
        self, args: list[str], *, capture_output: bool, text: bool, check: bool
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append(args)
        code, out, err = self.answers.get(args[1], (0, "", ""))
        return subprocess.CompletedProcess(args, code, out, err)


def _tmux(**extra: tuple[int, str, str]) -> FakeTmux:
    details = json.dumps({"focus": "ledger"})
    return FakeTmux(
        {
            "list-panes": (0, "synapse-P_a\t0.0\t200\nsynapse-P_b\t0.0\t400\n", ""),
            "show-environment": (
                0,
                "SYN_IDENTITY=P/a\nSYN_PROJECT=P\n-SYN_TMUX_PROVIDER\nOTHER=x\n",
                "",
            ),
            "show": (0, details + "\n", ""),
            **extra,
        }
    )


def _proc_root(tmp_path: Path) -> Path:
    root = tmp_path / "proc"
    (root / "500").mkdir(parents=True, exist_ok=True)
    (root / "500" / "environ").write_bytes(b"SYN_IDENTITY=P/c\0SYN_PROJECT=P\0SECRET_TOKEN=no\0")
    (root / "uptime").write_text("1000.0 0\n", encoding="ascii")
    return root


def _host(
    tmp_path: Path, runner: FakeTmux | None = None, procs: dict[int, Proc] | None = None
) -> Host:
    return Host(
        procs if procs is not None else dict(PROCS),
        runner=runner or _tmux(),
        home=tmp_path / "home",
        proc_root=_proc_root(tmp_path),
        boot=BOOT,
    )


# --- reading processes ------------------------------------------------------------------


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="/proc is Linux-only")
def test_the_real_process_table_holds_this_process() -> None:
    procs = read_procs()
    me = procs[os.getpid()]
    assert me.ppid == os.getppid() and me.start_ticks > 0 and me.argv


def test_entries_that_are_not_readable_processes_are_skipped(tmp_path: Path) -> None:
    (tmp_path / "self").mkdir()
    (tmp_path / "7").mkdir()  # no stat: it ended meanwhile
    (tmp_path / "8").mkdir()
    (tmp_path / "8" / "stat").write_text(
        "8 (a) S 1 " + " ".join(["0"] * 17) + " 42 0\n", encoding="utf-8"
    )
    (tmp_path / "8" / "cmdline").write_bytes(b"claude\0--resume\0x\0")
    assert read_procs(tmp_path) == {8: Proc(8, 1, ("claude", "--resume", "x"), 42)}


def test_the_topmost_agent_is_found_under_a_pane() -> None:
    kids = session_ledger_collect.children(PROCS)
    assert descendants(100, kids) == [200, 400, 300, 310]
    assert topmost_agent(200, PROCS, kids) == 300
    assert topmost_agent(400, PROCS, kids) is None
    assert topmost_agent(300, PROCS, kids) == 300  # the pane's own process is the agent
    assert topmost_agent(310, PROCS, kids) is None  # the same agent's child is not a seat


@pytest.mark.parametrize(
    ("argv", "vendor"),
    [
        (("claude", "--resume", SID), "claude"),
        (("/home/u/.local/bin/claude",), "claude"),
        (("node", "--no-warnings", "/usr/lib/codex/bin/codex.js", "resume", SID), "codex"),
        (("/opt/codex/vendor/codex-linux-x64/codex", "--helper"), "codex"),
        (("/usr/bin/python3", "-u", "/opt/grok/grok.py"), "grok"),
        (("agy", "--conversation", SID), "agy"),
        # An argument never makes a process an agent:
        (("tmux", "-L", "s", "new-session", "-d", "-s", "x", "/tmp/bin/claude"), None),
        (("fish", "-c", "env SYN_IDENTITY=P/a claude"), None),
        (("/usr/bin/kitty", "--title", "Claude-TL", "--class", "Claude-TL"), None),
        (("/venv/bin/python", "/venv/bin/synapse", "seat-hold", "--", "codex"), None),
        (("less", "/home/u/claude-notes.md"), None),
        (("node",), None),
        ((), None),
    ],
)
def test_an_agent_is_known_by_its_executable_only(
    argv: tuple[str, ...], vendor: str | None
) -> None:
    assert agent_vendor(argv) == vendor


def test_the_agent_behind_a_seat_holder_is_the_seat_and_the_holder_is_not(
    tmp_path: Path,
) -> None:
    procs = {
        100: Proc(100, 1, ("tmux", "new-session", "--", "/venv/bin/synapse"), 10),
        200: Proc(
            200, 100, ("/venv/bin/python", "/venv/bin/synapse", "seat-hold", "--", "codex"), 20
        ),
        300: Proc(300, 200, ("node", "/usr/lib/codex/bin/codex.js"), 30),
        900: Proc(900, 1, ("/usr/bin/kitty", "--title", "Claude-TL"), 90),
    }
    runner = _tmux(**{"list-panes": (0, "synapse-P_a\t0.0\t200\n", "")})
    rows = observe_host(_host(tmp_path, runner, procs))
    # The row's PID is the agent's, the one its launcher records; no terminal is a seat.
    assert [row["pid"] for row in rows.values()] == [300]


def test_only_the_two_named_keys_are_read_from_an_environment(tmp_path: Path) -> None:
    root = _proc_root(tmp_path)
    assert named_environ(500, root) == {"SYN_IDENTITY": "P/c", "SYN_PROJECT": "P"}
    assert named_environ(999, root) == {}


def test_open_files_are_read_and_a_vanished_one_is_skipped(tmp_path: Path) -> None:
    fd = tmp_path / "proc" / "300" / "fd"
    fd.mkdir(parents=True)
    (fd / "3").symlink_to(f"/home/u/.codex/sessions/rollout-{SID}.jsonl")
    (fd / "4").write_text("", encoding="utf-8")  # not a link: unreadable as one
    assert open_files([300, 999], tmp_path / "proc") == [
        f"/home/u/.codex/sessions/rollout-{SID}.jsonl"
    ]


# --- native session ids --------------------------------------------------------------------


def test_a_claude_session_comes_from_its_per_process_file(tmp_path: Path) -> None:
    procs = {600: Proc(600, 1, ("claude",), 60)}
    sessions = tmp_path / ".claude" / "sessions"
    sessions.mkdir(parents=True)
    (sessions / "600.json").write_text(json.dumps({"sessionId": SID}), encoding="utf-8")
    kids = session_ledger_collect.children(procs)
    assert native_session("claude", 600, procs, kids, home=tmp_path) == (
        SID,
        "claude_sessions_file",
    )
    (sessions / "600.json").write_text("{}", encoding="utf-8")  # no id in it
    assert native_session("claude", 600, procs, kids, home=tmp_path, proc_root=tmp_path) == (
        None,
        None,
    )
    assert native_session("claude", 600, procs, kids, home=None, proc_root=tmp_path) == (None, None)


def test_a_resume_flag_names_the_session(tmp_path: Path) -> None:
    kids = session_ledger_collect.children(PROCS)
    found = native_session("claude", 500, PROCS, kids, home=None, proc_root=tmp_path)
    assert found == (SID, "argv_resume_flag")


def test_an_open_codex_rollout_names_the_session(tmp_path: Path) -> None:
    fd = tmp_path / "300" / "fd"
    fd.mkdir(parents=True)
    (fd / "2").symlink_to("/x/history.jsonl")  # open, but not a rollout
    (fd / "3").symlink_to(f"/x/rollout-2026-11-19-{SID}.jsonl")
    kids = session_ledger_collect.children(PROCS)
    assert native_session("codex", 300, PROCS, kids, home=None, proc_root=tmp_path) == (
        SID,
        "open_rollout_file",
    )


def _conversation(folder: Path, own: str, refers_to: str | None) -> Path:
    path = folder / f"{own}.db"
    con = sqlite3.connect(path)
    con.execute("create table trajectory_metadata_blob (data blob)")
    con.execute(
        "insert into trajectory_metadata_blob values (?)",
        (f"parent={refers_to}".encode() if refers_to else b"",),
    )
    con.commit()
    con.close()
    return path


def test_an_agy_sub_conversation_is_traced_to_its_root(tmp_path: Path) -> None:
    folder = tmp_path / "conversations"
    folder.mkdir()
    _conversation(folder, PARENT, None)
    child = _conversation(folder, SID, PARENT)
    assert agy_root(str(child)) == PARENT
    assert agy_root(str(folder / "not-an-id.db")) is None
    assert (
        agy_root(str(folder / "99999999-2222-3333-4444-555555555555.db"))
        == "99999999-2222-3333-4444-555555555555"
    )
    procs = {700: Proc(700, 1, ("agy",), 70)}
    fd = tmp_path / "proc" / "700" / "fd"
    fd.mkdir(parents=True)
    (fd / "5").symlink_to(child)
    kids = session_ledger_collect.children(procs)
    found = native_session("agy", 700, procs, kids, home=None, proc_root=tmp_path / "proc")
    assert found == (PARENT, "open_conversation_root")


def test_an_agy_file_without_an_id_names_no_session(tmp_path: Path) -> None:
    procs = {701: Proc(701, 1, ("agy",), 71)}
    fd = tmp_path / "701" / "fd"
    fd.mkdir(parents=True)
    (fd / "5").symlink_to("/x/conversations/index.db")
    kids = session_ledger_collect.children(procs)
    assert native_session("agy", 701, procs, kids, home=None, proc_root=tmp_path) == (None, None)


def test_a_runaway_chain_of_conversations_stops(tmp_path: Path) -> None:
    folder = tmp_path / "conversations"
    folder.mkdir()
    ids = [f"{n:08d}-2222-3333-4444-555555555555" for n in range(6)]
    for own, parent in zip(ids, ids[1:] + [ids[0]], strict=False):
        _conversation(folder, own, parent)
    assert agy_root(str(folder / f"{ids[0]}.db")) == ids[4]  # four hops at most


def test_an_unknown_vendor_has_no_session(tmp_path: Path) -> None:
    procs = {800: Proc(800, 1, ("vim",), 80)}
    kids = session_ledger_collect.children(procs)
    assert native_session(None, 800, procs, kids, home=None, proc_root=tmp_path) == (None, None)


# --- tmux ----------------------------------------------------------------------------------


def test_no_tmux_server_is_no_sessions_but_a_failed_query_stops(tmp_path: Path) -> None:
    absent = _tmux(**{"list-panes": (1, "", "no server running on /tmp/tmux-1000/default\n")})
    rows = observe_host(_host(tmp_path, absent))
    # With no tmux server every agent is an agent outside tmux; none has a tmux place.
    assert {row["pid"]: row["identity"] for row in rows.values()} == {300: None, 500: "P/c"}
    assert {row["tmux"] is None for row in rows.values()} == {True}
    broken = _tmux(**{"list-panes": (1, "", "lost server\n")})
    with pytest.raises(CollectError, match="list-panes failed: lost server"):
        observe_host(_host(tmp_path, broken))


def test_a_tmux_that_cannot_run_stops(tmp_path: Path) -> None:
    def missing(
        args: list[str], *, capture_output: bool, text: bool, check: bool
    ) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("tmux")

    host = _host(tmp_path)
    host.runner = missing
    with pytest.raises(CollectError, match="tmux could not be run"):
        observe_host(host)


def test_sessions_and_agents_are_observed(tmp_path: Path) -> None:
    runner = _tmux()
    rows = observe_host(_host(tmp_path, runner))
    by_pid = {row["pid"]: row for row in rows.values()}
    assert set(by_pid) == {300, 500}
    pane = by_pid[300]
    assert pane["row_id"] == f"{BOOT}:300:30" and pane["vendor"] == "codex"
    assert (pane["identity"], pane["project"]) == ("P/a", "P")
    assert pane["tmux"] == {"session": "synapse-P_a", "pane": "0.0"}
    assert pane["label"] == {"focus": "ledger", "now": None, "state": None}
    bare = by_pid[500]
    assert bare["tmux"] is None and bare["identity"] == "P/c"
    assert bare["native_session_id"] == SID and bare["started_at"].endswith("Z")
    assert "argv" not in bare and "SECRET_TOKEN" not in json.dumps(rows)
    # One session's facts are asked once, at its exact name.
    assert ["tmux", "show-environment", "-t", "=synapse-P_a:"] in runner.calls


def test_a_sessions_facts_are_asked_once_for_all_its_panes(tmp_path: Path) -> None:
    procs = {**PROCS, 900: Proc(900, 100, ("/bin/sh",), 90), 910: Proc(910, 900, ("claude",), 91)}
    runner = _tmux(**{"list-panes": (0, "synapse-P_a\t0.0\t200\nsynapse-P_a\t0.1\t900\n", "")})
    rows = observe_host(_host(tmp_path, runner, procs=procs))
    places = sorted(row["tmux"]["pane"] for row in rows.values() if row["tmux"])
    assert places == ["0.0", "0.1"]
    assert [call[1] for call in runner.calls].count("show-environment") == 1


def test_a_label_that_is_not_json_has_no_focus(tmp_path: Path) -> None:
    rows = observe_host(_host(tmp_path, _tmux(show=(0, "not json\n", ""))))
    assert {row["label"]["focus"] for row in rows.values()} == {None}


def test_a_start_time_without_uptime_is_unknown(tmp_path: Path) -> None:
    proc = Proc(1, 0, ("x",), 1)
    assert session_ledger_collect._started_at(proc, tmp_path) is None
    (tmp_path / "uptime").write_text("100.0 1\n", encoding="ascii")
    when = session_ledger_collect._started_at(proc, tmp_path, now=lambda: 1_000_000.0)
    assert when is not None and when.startswith("1970-01-12T")


# --- what is recorded --------------------------------------------------------------------------


def _events(directory: Path) -> list[dict[str, Any]]:
    return list(read_events(directory))


def test_a_first_snapshot_starts_every_unlaunched_row_and_records_what_it_found(
    tmp_path: Path,
) -> None:
    ledger = tmp_path / "ledger"
    found = collect(ledger, _host(tmp_path))
    assert (found.rows, found.started, found.observed, found.ended) == (2, 2, 2, 0)
    kinds = [(event["event"], event.get("launcher")) for event in _events(ledger)]
    assert kinds.count(("start", "observed")) == 2 and kinds.count(("observe", None)) == 2
    start = next(
        event for event in _events(ledger) if event["event"] == "start" and event["pid"] == 500
    )
    assert "native_session_id" not in start and start["holder"] is None
    snapshot = json.loads((ledger / "current.json").read_text(encoding="utf-8"))
    assert snapshot["boot_id"] == BOOT and set(snapshot["rows"]) == {
        f"{BOOT}:300:30",
        f"{BOOT}:500:50",
    }
    assert (ledger / "boots" / f"{BOOT}.json").read_text(encoding="utf-8") == (
        ledger / "current.json"
    ).read_text(encoding="utf-8")
    assert stat.S_IMODE((ledger / "current.json").stat().st_mode) == 0o600
    assert stat.S_IMODE((ledger / "boots").stat().st_mode) == 0o700


def test_an_unchanged_host_records_nothing_new(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger"
    collect(ledger, _host(tmp_path))
    before = len(_events(ledger))
    assert collect(ledger, _host(tmp_path)) == session_ledger_collect.Collected(2, 0, 0, 0)
    assert len(_events(ledger)) == before


def test_a_gone_row_ends_unless_its_launcher_ended_it(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger"
    collect(ledger, _host(tmp_path))
    append_event(
        {"event": "end", "row_id": f"{BOOT}:500:50", "end_kind": "exit", "exit_status": 0},
        directory=ledger,
        boot=BOOT,
    )
    gone = {pid: proc for pid, proc in PROCS.items() if pid not in (300, 310, 500)}
    found = collect(ledger, _host(tmp_path, procs=gone))
    assert found.ended == 1  # the pane's agent; the other row already has its end
    end = fold(read_events(ledger))[f"{BOOT}:300:30"].end
    assert end is not None and end["end_kind"] == "observed_gone"


def test_a_reboot_cuts_every_open_row(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger"
    collect(ledger, _host(tmp_path))
    host = _host(tmp_path, procs={})
    host.boot = "boot-b"
    found = collect(ledger, host)
    assert (found.rows, found.ended) == (0, 2)
    ends = [event for event in _events(ledger) if event["event"] == "ended_unobserved"]
    assert {event["end_kind"] for event in ends} == {"boot_cut"}
    assert (ledger / "boots" / f"{BOOT}.json").exists() and (
        ledger / "boots" / "boot-b.json"
    ).exists()


def test_a_launched_row_gets_no_second_start(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger"
    append_event(
        {"event": "start", "row_id": f"{BOOT}:300:30", "launcher": "seat-hold", "identity": "P/a"},
        directory=ledger,
        boot=BOOT,
    )
    found = collect(ledger, _host(tmp_path))
    assert found.started == 1  # only the row nobody launched
    starts = [event for event in _events(ledger) if event["event"] == "start"]
    assert [event["launcher"] for event in starts] == ["seat-hold", "observed"]


def test_a_failed_read_writes_nothing(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger"
    with pytest.raises(CollectError):
        collect(ledger, _host(tmp_path, _tmux(**{"list-panes": (1, "", "lost server")})))
    assert not ledger.exists()


def test_an_earlier_view_of_another_schema_is_ignored(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger"
    ledger.mkdir(mode=0o700)
    (ledger / "current.json").write_text(
        '{"schema": "ws-session-ledger.v0", "rows": {"x": {}}}', encoding="utf-8"
    )
    assert collect(ledger, _host(tmp_path)).ended == 0


# --- the command ------------------------------------------------------------------------------


def test_the_snapshot_command_reports_and_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "ledger"
    monkeypatch.setattr(cli_ledger, "ledger_dir", lambda: ledger)
    monkeypatch.setattr(session_ledger_collect, "read_procs", lambda: dict(PROCS))
    real_host = session_ledger_collect.Host

    def host(procs: dict[int, Proc], *, tmux_bin: str, home: Path | None) -> Host:
        return real_host(
            procs,
            tmux_bin=tmux_bin,
            runner=_tmux(),
            home=home,
            proc_root=_proc_root(tmp_path),
            boot=BOOT,
        )

    monkeypatch.setattr(session_ledger_collect, "Host", host)
    assert cli_ledger._cmd_snapshot(argparse.Namespace(tmux_bin="tmux")) == 0
    assert (
        capsys.readouterr().out == "synapse ledger: 2 session(s); 2 started, 2 observed, 0 ended\n"
    )

    def failing(procs: dict[int, Proc], *, tmux_bin: str, home: Path | None) -> Host:
        return real_host(
            procs,
            runner=_tmux(**{"list-panes": (1, "", "lost")}),
            proc_root=_proc_root(tmp_path),
            boot=BOOT,
        )

    monkeypatch.setattr(session_ledger_collect, "Host", failing)
    assert (
        cli_ledger._cmd_snapshot(argparse.Namespace(tmux_bin="tmux"))
        == cli_ledger.HOST_UNREADABLE_EXIT_CODE
        == 3
    )
    assert "nothing was written" in capsys.readouterr().out
    assert len(_events(ledger)) == 4  # the first run's two starts and two observations only


def test_the_snapshot_command_refuses_a_ledger_it_may_not_use(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def refused() -> Path:
        raise LedgerError("the session ledger would lie in a shared or versioned tree")

    monkeypatch.setattr(cli_ledger, "ledger_dir", refused)
    assert (
        cli_ledger._cmd_snapshot(argparse.Namespace(tmux_bin="tmux")) == cli_ledger.USAGE_EXIT_CODE
    )
    assert "shared or versioned tree" in capsys.readouterr().out


# --- a journey on real tmux and the real process table ------------------------------------


@pytest.fixture
def private_tmux(tmp_path: Path) -> Iterator[str]:
    """A tmux wrapper on a private socket with no user configuration."""
    if not sys.platform.startswith("linux") or shutil.which("tmux") is None:
        pytest.skip("the collector reads /proc and tmux")
    socket = f"synapse-test-{uuid.uuid4().hex[:10]}"
    wrapper = tmp_path / "tmux-private"
    wrapper.write_text(f'#!/bin/sh\nexec tmux -L {socket} -f /dev/null "$@"\n', encoding="utf-8")
    wrapper.chmod(0o755)
    try:
        yield str(wrapper)
    finally:
        subprocess.run(["tmux", "-L", socket, "kill-server"], capture_output=True, check=False)
        socket_dir = Path(os.environ.get("TMUX_TMPDIR", "/tmp")) / f"tmux-{os.getuid()}"
        (socket_dir / socket).unlink(missing_ok=True)


def _own_server(tmux: str) -> dict[int, Proc]:
    """The real process table, cut to the private tmux server's own tree."""
    server = int(
        subprocess.run(
            [tmux, "display", "-p", "#{pid}"], capture_output=True, text=True, check=True
        ).stdout
    )
    procs = read_procs()
    kids = session_ledger_collect.children(procs)
    keep = {server, *descendants(server, kids)}
    return {pid: proc for pid, proc in procs.items() if pid in keep}


def test_a_real_pane_agent_is_recorded_and_its_end_noticed(
    private_tmux: str, tmp_path: Path
) -> None:
    agent = tmp_path / "bin" / "claude"
    agent.parent.mkdir()
    agent.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(60)\n", encoding="utf-8")
    agent.chmod(0o755)
    subprocess.run(
        [
            private_tmux,
            "new-session",
            "-d",
            "-s",
            "synapse-P_j",
            "-e",
            "SYN_IDENTITY=P/j",
            "-e",
            "SYN_PROJECT=P",
            str(agent),
        ],
        check=True,
    )
    ledger = tmp_path / "ledger"
    deadline = time.monotonic() + 10
    while True:
        found = collect(ledger, Host(_own_server(private_tmux), tmux_bin=private_tmux, home=None))
        if found.rows or time.monotonic() > deadline:
            break
        time.sleep(0.2)
    rows = fold(read_events(ledger))
    (row,) = rows.values()
    assert row.start["launcher"] == "observed" and row.start["vendor"] == "claude"
    assert row.start["identity"] == "P/j" and row.start["tmux"] == {
        "session": "synapse-P_j",
        "pane": "0.0",
    }
    assert _own_server(private_tmux)  # the agent still runs under the private server
    subprocess.run([private_tmux, "kill-session", "-t", "=synapse-P_j"], check=True)
    # The session is gone; the server answers with no sessions, or is gone too.
    assert collect(ledger, Host({}, tmux_bin=private_tmux, home=None)).ended == 1
    end = fold(read_events(ledger))[row.row_id].end
    assert end is not None and end["end_kind"] == "observed_gone"
