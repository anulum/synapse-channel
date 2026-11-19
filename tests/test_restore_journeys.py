# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — journeys: a restore on real tmux, a real hub and a real seat holder
"""A cut seat comes back without a terminal, a live one is left alone, a dry run writes nothing.

The planner's own pieces run as in production: its held hub connection, a
child ``synapse worker-session --restore --no-attach`` per seat, the seat
holder in a private tmux server, and the ledger. Only the grace, the spacing
and the host's headroom are shortened or given, so the journey takes seconds.
"""

from __future__ import annotations

import functools
import os
import shlex
import shutil
import socket
import subprocess
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from hub_e2e_helpers import close_agents, connect_agent, running_hub
from synapse_channel.cli_ledger_restore import build_plan, dry_view, tmux_taken
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.restore_grace import HubGrace
from synapse_channel.restore_pacing import GIB, Limits, Reading
from synapse_channel.restore_plan import START
from synapse_channel.restore_run import Run, execute
from synapse_channel.session_ledger import (
    append_event,
    fold,
    ledger_dir,
    read_events,
    shared_tree,
)
from synapse_channel.session_ledger_collect import Host, read_procs

SID = "11111111-2222-3333-4444-555555555555"
PREV = "boot-journey-prev"
ROOMY = Reading(32 * GIB, 64 * GIB, 0.0, 8, None)

_AGENT = """#!{python}
import sys
print("RESUMED " + " ".join(sys.argv[1:]), flush=True)
print("\\u276f ", end="", flush=True)
for line in sys.stdin:
    if line.strip() == "quit":
        raise SystemExit(0)
    print("\\u276f ", end="", flush=True)
"""


@pytest.fixture
def private_tmux(tmp_path: Path) -> Iterator[str]:
    """A tmux wrapper on a private socket with no user configuration."""
    if not sys.platform.startswith("linux") or shutil.which("tmux") is None:
        pytest.skip("the seat holder needs Linux and tmux")
    name = f"synapse-test-{uuid.uuid4().hex[:10]}"
    wrapper = tmp_path / "tmux-private"
    wrapper.write_text(f'#!/bin/sh\nexec tmux -L {name} -f /dev/null "$@"\n', encoding="utf-8")
    wrapper.chmod(0o755)
    try:
        yield str(wrapper)
    finally:
        subprocess.run(["tmux", "-L", name, "kill-server"], capture_output=True, check=False)
        socket_dir = Path(os.environ.get("TMUX_TMPDIR", "/tmp")) / f"tmux-{os.getuid()}"
        (socket_dir / name).unlink(missing_ok=True)


def _synapse(tmp_path: Path) -> str:
    """``synapse`` for the child and the pane: this checkout and the test machine key."""
    wrapper = tmp_path / "synapse"
    data_home = shlex.quote(os.environ["XDG_DATA_HOME"])
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o700, exist_ok=True)
    wrapper.write_text(
        f"#!/bin/sh\nXDG_DATA_HOME={data_home} XDG_RUNTIME_DIR={shlex.quote(str(runtime))} "
        f"exec {sys.executable} -c "
        "'from synapse_channel.cli import main; raise SystemExit(main())' \"$@\"\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    return str(wrapper)


def _cut_seat(ledger: Path, identity: str, pid: int, cwd: Path) -> str:
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
        "started_at": f"2026-11-18T10:00:0{pid % 10}Z",
        "launcher": "seat-hold",
        "holder": None,
        "tmux": None,
        "permission_profile": None,
        "restore_priority": None,
    }
    for event in (
        start,
        {"event": "observe", "row_id": row, "native_session_id": SID},
        {"event": "ended_unobserved", "row_id": row, "end_kind": "boot_cut"},
    ):
        append_event(event, directory=ledger, boot=PREV)
    return row


def _files(directory: Path) -> dict[str, tuple[bytes | None, int]]:
    return {
        str(path.relative_to(directory)): (
            path.read_bytes() if path.is_file() else None,
            path.stat().st_mtime_ns,
        )
        for path in sorted([directory, *directory.rglob("*")])
    }


def _has_session(tmux: str, name: str) -> bool:
    return tmux_taken(tmux, name)


async def test_a_cut_seat_comes_back_unattached_and_a_live_one_is_left_alone(
    private_tmux: str, tmp_path: Path
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    agent = bin_dir / "claude"
    agent.write_text(_AGENT.format(python=sys.executable), encoding="utf-8")
    agent.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    environ = {"PATH": f"{bin_dir}:{os.environ['PATH']}", "HOME": str(home), "LANG": "C.UTF-8"}
    # The planner reads the ledger the holder in the pane writes: its HOME's state.
    ledger = home / ".local" / "state" / "synapse" / "ledger"
    work = tmp_path / "work"
    work.mkdir()
    cut_row = _cut_seat(ledger, "T/cut", 1, work)
    _cut_seat(ledger, "T/live", 2, work)
    host = socket.gethostname()
    taken = functools.partial(tmux_taken, private_tmux)

    # The dry run first: the same plan, and not one byte or mtime changes.
    before = _files(ledger)
    view = dry_view(ledger, Host(read_procs(), tmux_bin=private_tmux, home=None), host)
    entries, scope = build_plan(
        view, host_name=host, boot=None, only=["T/cut", "T/live"], taken=taken
    )
    assert _files(ledger) == before
    assert scope.boot == PREV and [(e.identity, e.state) for e in entries] == [
        ("T/cut", START),
        ("T/live", START),
    ]

    lines: list[str] = []
    async with running_hub() as (hub, uri):
        live = await connect_agent("T/live-seat", uri)  # the seat runs elsewhere
        grace = HubGrace(
            f"{host}-restore-planner",
            uri=uri,
            token=None,
            factory=SynapseAgent,
            health=None,
            grace=0.5,
        )
        run = Run(
            directory=ledger,
            environ=environ,
            uri=uri,
            synapse_bin=_synapse(tmp_path),
            tmux_bin=private_tmux,
            limits=Limits(spacing_seconds=0.2),
            grace=grace,
            token=None,
            read=lambda: ROOMY,
            recheck=lambda _entry: None,
            report=lines.append,
        )
        try:
            code, outcomes = await execute(entries, run)
            assert code == 0, lines
            assert [(o.identity, o.state, o.code) for o in outcomes] == [
                ("T/cut", "started", 0),
                ("T/live", "live", 75),
            ], lines
            # The cut seat runs behind its holder in its own session, with nothing attached.
            assert _has_session(private_tmux, "synapse-T_cut")
            clients = subprocess.run(
                [private_tmux, "list-clients"], capture_output=True, text=True, check=False
            )
            assert clients.stdout == ""
            assert "T/cut-seat" in hub.online_agents()
            shown = subprocess.run(
                [private_tmux, "capture-pane", "-p", "-t", "=synapse-T_cut:"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            assert f"RESUMED --resume {SID}" in shown
            # The live seat was never touched: no session was left for it.
            assert not _has_session(private_tmux, "synapse-T_live")
            assert f"{host}-restore-planner" not in hub.online_agents()  # the run let go
        finally:
            await close_agents(live)
            subprocess.run(
                [private_tmux, "kill-session", "-t", "=synapse-T_cut"], capture_output=True
            )

    # Each outcome is on its old row. The holder records its new row in the same ledger,
    # unless the ledger would lie in a shared tree (a host whose temporary files do), and
    # the old row names the new one exactly when it is there.
    rows = fold(read_events(ledger))
    restore = rows[cut_row].observed["restore"]
    assert (restore["state"], restore["code"], restore["failures"]) == ("started", 0, 0)
    held = [
        key
        for key, row in rows.items()
        if row.start.get("identity") == "T/cut"
        and row.start.get("launcher") == "seat-hold"
        and key != cut_row
    ]
    assert held == ([] if shared_tree(home) is not None else [restore["new_row_id"]])
    if held:
        assert ledger_dir({"HOME": str(home)}) == ledger
        assert rows[held[0]].start["cwd"] == str(work)
    assert rows[f"{PREV}:2:7"].observed["restore"]["state"] == "live"
