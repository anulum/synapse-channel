# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — `synapse ledger restore`: plan the cut seats, and start them on request
"""``synapse ledger restore``: which seats a crash or reboot cut, and their start.

Without ``--execute`` the command is a dry run. It reads the host in memory and
writes nothing at all: no ledger line, no ``seq``, no ``current.json``, no
``boots/`` file and no lock. It prints each identity's last row with what a run
would do with it, the headroom limits it would wait for, and the names of the
environment variables a seat's child would get and would not, never their
values.

With ``--execute`` it holds ``restore.lock``, takes a real snapshot, and starts
the planned seats one at a time (see :mod:`synapse_channel.restore_run`). It
restores agent sessions only; it never arranges windows.
"""

from __future__ import annotations

import argparse
import asyncio
import functools
import json
import os
import shutil
import socket
import subprocess  # nosec B404 # fixed argument vectors only, never a shell
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from itertools import chain
from pathlib import Path
from typing import Any

from synapse_channel.client.agent import default_hub_uri
from synapse_channel.core.private_dir import ensure_private_dir
from synapse_channel.restore_grace import HubGrace
from synapse_channel.restore_pacing import Limits, config_path, describe, load_limits, read_host
from synapse_channel.restore_plan import START, Entry, Scope, plan, previous_boot
from synapse_channel.restore_run import (
    Outcome,
    RestoreBusyError,
    Run,
    child_environ,
    execute,
    recheck,
    restore_lock,
)
from synapse_channel.seat_hold import SEAT_LIVE_EXIT_CODE
from synapse_channel.session_ledger import (
    LedgerError,
    Row,
    fold,
    ledger_dir,
    pid_starttime,
    read_events,
)
from synapse_channel.session_ledger_collect import (
    CollectError,
    Host,
    collect,
    read_procs,
    running_identities,
    survey,
)

USAGE_EXIT_CODE = 2
HOST_UNREADABLE_EXIT_CODE = 3


def add_restore_parser(nested: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``ledger restore``."""
    restore = nested.add_parser(
        "restore",
        help="Show the seats a crash or reboot cut (a dry run); start them with --execute.",
    )
    restore.add_argument(
        "--boot", default=None, help="Read the cut rows of this boot id, not the previous boot's."
    )
    restore.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="IDENTITY",
        help="Only this identity (repeatable); it may then start a row that needs its name.",
    )
    restore.add_argument("--json", action="store_true", help="Print the plan as JSON.")
    restore.add_argument(
        "--execute", action="store_true", help="Start the planned seats; otherwise write nothing."
    )
    restore.add_argument("--uri", default=default_hub_uri(), help="The hub the seats hold.")
    restore.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    restore.add_argument("--token-file", default=None, help="Read the token from this file.")
    restore.add_argument("--tmux-bin", default="tmux", help="The tmux executable.")
    restore.add_argument(
        "--synapse-bin", default="synapse", help="The synapse executable seats are started with."
    )
    restore.set_defaults(func=_cmd_restore)


@dataclass(frozen=True)
class View:
    """The ledger's rows with this snapshot folded in, and who runs now."""

    rows: dict[str, Row]
    live: frozenset[str]
    boot: str


def dry_view(directory: Path, host: Host, host_name: str) -> View:
    """Return the view a snapshot would give, holding its events in memory only."""
    events, snapshot = survey(directory, host)
    written = [{"host": host_name, "boot_id": host.boot, **event} for event in events]
    stored = read_events(directory) if directory.is_dir() else iter(())
    return View(fold(chain(stored, written)), running_identities(snapshot), host.boot)


def written_view(directory: Path, host: Host) -> View:
    """Take a real snapshot and return the view from the ledger."""
    found = collect(directory, host)
    return View(fold(read_events(directory)), found.identities, host.boot)


def process_alive(row: Row) -> bool:
    """Return whether the row's process runs, by its PID and start ticks."""
    pid, ticks = row.start.get("pid"), row.start.get("pid_starttime")
    return isinstance(pid, int) and ticks is not None and pid_starttime(pid) == ticks


def tmux_taken(tmux_bin: str, name: str) -> bool:
    """Return whether a tmux session of exactly this name exists."""
    try:
        done = subprocess.run(  # nosec B603 # argument vector, never a shell
            [tmux_bin, "has-session", "-t", f"={name}"], capture_output=True, check=False
        )
    except OSError:
        return False
    return done.returncode == 0


def build_plan(
    view: View,
    *,
    host_name: str,
    boot: str | None,
    only: list[str],
    taken: Callable[[str], bool],
) -> tuple[list[Entry], Scope]:
    """Return the plan for this view and the scope it was made for."""
    chosen = boot or previous_boot(view.rows.values(), view.boot)
    scope = Scope(host_name, view.boot, chosen, by_default=boot is None, only=frozenset(only))
    entries = plan(
        view.rows,
        scope,
        live=view.live,
        alive=process_alive,
        cwd_exists=os.path.isdir,
        tmux_taken=taken,
    )
    return entries, scope


def environment_summary(environ: Mapping[str, str], token: str | None) -> dict[str, list[str]]:
    """Return the names a seat's child gets and the names it does not, never a value."""
    env, dropped = child_environ(environ, Entry("P/seat", "", START), token)
    return {"passes": sorted(env), "drops": dropped}


def _host(args: argparse.Namespace) -> Host:
    home = os.environ.get("HOME")
    return Host(read_procs(), tmux_bin=args.tmux_bin, home=Path(home) if home else None)


def _entry_line(entry: Entry) -> str:
    start = (
        f"  {' '.join(entry.command)}  in {entry.cwd}  tmux {entry.session}"
        if entry.state == START
        else ""
    )
    return f"{entry.identity}  {entry.state}  {entry.vendor or '-'}  {entry.row_id}{start}"


def _print_plan(
    args: argparse.Namespace,
    scope: Scope,
    entries: list[Entry],
    head: Mapping[str, Any],
) -> None:
    if args.json:
        return
    which = "the previous boot" if scope.by_default else "the boot named"
    mode = "executing" if args.execute else "dry run, nothing is written"
    print(f"synapse ledger restore ({mode}) on {scope.host}: boot {scope.boot or '-'} ({which})")
    print(f"headroom: {head['headroom']}")
    for note in head["notes"]:
        print(f"note: {note}")
    env = head["environment"]
    print(f"environment: passes {', '.join(env['passes'])}; drops {', '.join(env['drops']) or '-'}")
    for entry in entries:
        print(_entry_line(entry))
    print(f"{sum(entry.state == START for entry in entries)} to start")


def _document(
    args: argparse.Namespace, scope: Scope, entries: list[Entry], head: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        "mode": "execute" if args.execute else "dry run",
        "host": scope.host,
        "boot": scope.boot,
        "boot_by_default": scope.by_default,
        "current_boot": scope.current_boot,
        **head,
        "entries": [entry.as_dict() for entry in entries],
    }


def _cmd_restore(args: argparse.Namespace) -> int:
    """Plan the restore; start the planned seats with ``--execute``."""
    host_name = socket.gethostname()
    synapse_bin = shutil.which(args.synapse_bin)
    try:
        directory = ledger_dir()
        limits, notes = load_limits(config_path(os.environ), host_name)
    except (LedgerError, ValueError) as exc:
        print(f"synapse ledger restore: {exc}")
        return USAGE_EXIT_CODE
    if synapse_bin is None:
        print(f"synapse ledger restore: {args.synapse_bin} is not an executable on PATH")
        return USAGE_EXIT_CODE
    head = {
        "headroom": describe(limits),
        "limits": asdict(limits),
        "notes": notes,
        "environment": environment_summary(os.environ, args.token),
    }
    if not args.execute:
        return _dry_run(args, directory, host_name, head)
    try:
        ensure_private_dir(directory, parents=True, purpose="session ledger")
        with restore_lock(directory):
            return _execute(args, directory, host_name, head, limits, synapse_bin)
    except RestoreBusyError as exc:
        print(f"synapse ledger restore: {exc}; this run stops")
        return SEAT_LIVE_EXIT_CODE


def _dry_run(
    args: argparse.Namespace, directory: Path, host_name: str, head: Mapping[str, Any]
) -> int:
    taken = functools.partial(tmux_taken, args.tmux_bin)
    try:
        view = dry_view(directory, _host(args), host_name)
    except CollectError as exc:
        print(f"synapse ledger restore: the host could not be read: {exc}")
        return HOST_UNREADABLE_EXIT_CODE
    entries, scope = build_plan(
        view, host_name=host_name, boot=args.boot, only=args.only, taken=taken
    )
    _print_plan(args, scope, entries, head)
    if args.json:
        print(json.dumps(_document(args, scope, entries, head), indent=1, sort_keys=True))
    return 0


async def _with_signals(entries: Sequence[Entry], run: Run) -> tuple[int, list[Outcome]]:
    """Run :func:`execute` with ``SIGINT`` and ``SIGTERM`` noted by the run's interrupt.

    The first signal starts no further seat and lets the one in flight finish;
    a second ends that seat's process group. The run then exits 130.
    """
    with run.interrupt.installed(asyncio.get_running_loop()):
        return await execute(entries, run)


def _execute(
    args: argparse.Namespace,
    directory: Path,
    host_name: str,
    head: Mapping[str, Any],
    limits: Limits,
    synapse_bin: str,
) -> int:
    taken = functools.partial(tmux_taken, args.tmux_bin)

    def fresh() -> tuple[dict[str, Row], frozenset[str]]:
        view = written_view(directory, _host(args))
        return view.rows, view.live

    try:
        view = written_view(directory, _host(args))
    except (CollectError, LedgerError) as exc:
        print(f"synapse ledger restore: the host could not be read or recorded: {exc}")
        return HOST_UNREADABLE_EXIT_CODE
    entries, scope = build_plan(
        view, host_name=host_name, boot=args.boot, only=args.only, taken=taken
    )
    _print_plan(args, scope, entries, head)
    from synapse_channel.client.agent import SynapseAgent

    report: Callable[[str], None] = functools.partial(
        print, file=sys.stderr if args.json else sys.stdout, flush=True
    )
    run = Run(
        directory=directory,
        environ=os.environ,
        uri=args.uri,
        synapse_bin=synapse_bin,
        tmux_bin=args.tmux_bin,
        limits=limits,
        grace=HubGrace(
            f"{host_name}-restore-planner", uri=args.uri, token=args.token, factory=SynapseAgent
        ),
        token=args.token,
        read=read_host,
        recheck=functools.partial(recheck, snapshot=fresh, tmux_taken=taken),
        report=report,
    )
    code, outcomes = (
        asyncio.run(_with_signals(entries, run))
        if any(e.state == START for e in entries)
        else (0, [])
    )
    if args.json:
        document = _document(args, scope, entries, head)
        document.update({"outcomes": [asdict(item) for item in outcomes], "exit_code": code})
        print(json.dumps(document, indent=1, sort_keys=True))
    return code
