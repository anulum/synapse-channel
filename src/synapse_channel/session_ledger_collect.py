# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the session ledger's collector: what runs on this host now
"""``synapse ledger snapshot``: record the agent sessions that run on this host now.

The launchers record the rows they start. The collector finds every agent
session, launched or not, in tmux panes and outside tmux, and records what the
launchers cannot know:

* a ``start`` (``launcher: "observed"``) for a row nobody launched;
* an ``observe`` with the fields that changed: the vendor's native session id
  and where it was read, the seat's label focus, its tmux place;
* an ``ended_unobserved`` for a row that is gone (``observed_gone``) or was cut
  by a reboot (``boot_cut``), never for a row that already has an end.

It keeps its last view in ``current.json`` and in ``boots/<boot_id>.json``, so a
restore after a reboot reads the previous boot's view.

It works without any desktop: tmux sessions and processes are all it reads. A
tmux query that fails, as opposed to a host with no tmux server, stops the
snapshot before it writes anything, so an error is never read as "every seat
is gone". Argument vectors are read in memory to find the vendor and a resume
id, and are never stored; of a process's environment only ``SYN_IDENTITY`` and
``SYN_PROJECT`` are read.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import sqlite3
import subprocess  # nosec B404 # fixed argument vectors only, never a shell
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from synapse_channel.agent_tmux import provider_family_of
from synapse_channel.core.errors import SynapseError
from synapse_channel.core.private_dir import ensure_private_dir
from synapse_channel.session_ledger import (
    COLLECTOR_LOCK_WAIT_SECONDS,
    SCHEMA,
    append_event,
    boot_id,
    fold,
    read_events,
    row_id,
    sync_directory,
)

UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
INTERPRETER = re.compile(r"python[0-9.]*|node|nodejs|bun|deno")
"""Executables that run an agent's script; the script then names the agent."""
RESUME_FLAGS = frozenset({"--resume", "-r", "--conversation", "resume"})
NO_SERVER_MARKERS = ("no server running", "error connecting to")
"""tmux's words for "there is no server", which is not an error for a collector."""

NAMED_KEYS = ("SYN_IDENTITY", "SYN_PROJECT")
OBSERVED_FIELDS = ("native_session_id", "native_session_id_source", "label", "tmux")


class CollectError(SynapseError):
    """A query failed; nothing is written, so a failure never reads as a gone seat."""

    code = "ledger_collect"


def agent_vendor(argv: Sequence[str]) -> str | None:
    """Return the vendor whose agent a process runs, judged by its executable only.

    The executable is the first word, or the script an interpreter
    (``node``, ``python``, ``bun``, ``deno``) runs. An argument never makes a
    process an agent: a tmux server's arguments name its first command, a shell
    names the agent it starts, a seat holder names the agent after ``--``, and
    a terminal's title or an open file can carry a vendor's name.
    """
    words = list(argv[:1])
    if words and INTERPRETER.fullmatch(Path(words[0]).name):
        words = [word for word in argv[1:] if not word.startswith("-")][:1]
    return provider_family_of(words)


class Runner(Protocol):
    """Runs one fixed argument vector; ``subprocess.run`` in production."""

    def __call__(
        self, args: list[str], *, capture_output: bool, text: bool, check: bool
    ) -> subprocess.CompletedProcess[str]:
        """Run ``args`` and return the completed process."""


@dataclass(frozen=True)
class Proc:
    """One process, as read from ``/proc``."""

    pid: int
    ppid: int
    argv: tuple[str, ...]
    start_ticks: int


def read_procs(root: Path = Path("/proc")) -> dict[int, Proc]:
    """Read every process this user can see; one that vanishes meanwhile is skipped."""
    procs: dict[int, Proc] = {}
    for entry in root.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()
            raw = (entry / "cmdline").read_bytes()
        except (OSError, IndexError):
            continue
        argv = tuple(part.decode(errors="replace") for part in raw.split(b"\0") if part)
        procs[int(entry.name)] = Proc(int(entry.name), int(stat[1]), argv, int(stat[19]))
    return procs


def children(procs: Mapping[int, Proc]) -> dict[int, list[int]]:
    """Map each PID to its children's PIDs."""
    kids: dict[int, list[int]] = {}
    for proc in procs.values():
        kids.setdefault(proc.ppid, []).append(proc.pid)
    return kids


def descendants(pid: int, kids: Mapping[int, list[int]]) -> list[int]:
    """Return every descendant of ``pid``, nearest first."""
    found: list[int] = []
    frontier = list(kids.get(pid, []))
    while frontier:
        found.extend(frontier)
        frontier = [child for parent in frontier for child in kids.get(parent, [])]
    return found


def topmost_agent(
    root: int, procs: Mapping[int, Proc], kids: Mapping[int, list[int]]
) -> int | None:
    """Return the pane's own process or first descendant that is an agent, not one's child.

    tmux may run the agent as the pane's own process, without a shell between;
    a process whose parent is the same agent is that agent's helper, not a seat.
    """
    # A pane tmux lists may have ended before the process table was read.
    for pid in [root, *descendants(root, kids)] if root in procs else descendants(root, kids):
        vendor = agent_vendor(procs[pid].argv)
        parent = procs.get(procs[pid].ppid)
        if vendor and (parent is None or agent_vendor(parent.argv) != vendor):
            return pid
    return None


def named_environ(pid: int, root: Path = Path("/proc")) -> dict[str, str]:
    """Return only ``SYN_IDENTITY`` and ``SYN_PROJECT`` from a process's environment."""
    try:
        raw = (root / str(pid) / "environ").read_bytes()
    except OSError:
        return {}
    found: dict[str, str] = {}
    for item in raw.split(b"\0"):
        name, _, value = item.partition(b"=")
        if name.decode(errors="replace") in NAMED_KEYS:
            found[name.decode()] = value.decode(errors="replace")
    return found


def open_files(pids: Iterable[int], root: Path = Path("/proc")) -> list[str]:
    """Return the paths the processes hold open."""
    names: list[str] = []
    for pid in pids:
        try:
            entries = list(os.scandir(root / str(pid) / "fd"))
        except OSError:
            continue
        for entry in entries:
            with contextlib.suppress(OSError):  # the descriptor closed meanwhile
                names.append(os.readlink(entry.path))
    return names


def agy_root(db_path: str) -> str | None:
    """Return the root conversation id of an agy conversation database, or its own id."""
    own = UUID.search(os.path.basename(db_path))
    if own is None:
        return None
    current, folder = own.group(0), os.path.dirname(db_path)
    for _ in range(4):
        try:
            con = sqlite3.connect(
                f"file:{os.path.join(folder, current + '.db')}?mode=ro", uri=True, timeout=1
            )
            try:
                row = con.execute("select data from trajectory_metadata_blob").fetchone()
            finally:
                con.close()
        except sqlite3.Error:
            return current
        blob = row[0] if row and isinstance(row[0], bytes) else b""
        refs = {
            match.decode()
            for match in re.findall(rb"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", blob)
        }
        parents = [
            ref
            for ref in refs
            if ref != current and os.path.exists(os.path.join(folder, ref + ".db"))
        ]
        if not parents:
            return current
        current = parents[0]
    return current


def _claude_session(tree: Iterable[int], home: Path | None) -> str | None:
    """Return the id in Claude's per-process sessions file, if one names it."""
    if home is None:
        return None
    for member in tree:
        with contextlib.suppress(OSError, ValueError, KeyError, TypeError):
            text = (home / ".claude" / "sessions" / f"{member}.json").read_text(encoding="utf-8")
            return str(json.loads(text)["sessionId"])
    return None


def _resume_flag(tree: Iterable[int], procs: Mapping[int, Proc]) -> str | None:
    """Return the session id a resume flag names in an argument vector, read in memory only."""
    for member in tree:
        argv = procs[member].argv
        for flag, value in zip(argv, argv[1:], strict=False):
            if flag in RESUME_FLAGS and UUID.fullmatch(value):
                return value
    return None


def _open_file_session(vendor: str | None, names: Iterable[str]) -> tuple[str | None, str | None]:
    """Return the id an open Codex rollout file or agy conversation database names."""
    if vendor == "codex":
        for name in names:
            match = UUID.search(os.path.basename(name)) if "rollout-" in name else None
            if match:
                return match.group(0), "open_rollout_file"
    if vendor == "agy":
        roots = [
            agy_root(name) for name in names if "/conversations/" in name and name.endswith(".db")
        ]
        found = [root for root in roots if root]
        if found:
            return max(set(found), key=found.count), "open_conversation_root"
    return None, None


def native_session(
    vendor: str | None,
    pid: int,
    procs: Mapping[int, Proc],
    kids: Mapping[int, list[int]],
    *,
    home: Path | None,
    proc_root: Path = Path("/proc"),
) -> tuple[str | None, str | None]:
    """Return the agent's resumable session id and where it was read, or ``(None, None)``.

    The sources, in order: Claude's per-process sessions file, a resume flag in
    an argument vector, and an open Codex rollout file or agy conversation.
    """
    tree = [pid] + [d for d in descendants(pid, kids) if agent_vendor(procs[d].argv) == vendor]
    claude = _claude_session(tree, home) if vendor == "claude" else None
    if claude:
        return claude, "claude_sessions_file"
    flagged = _resume_flag(tree, procs)
    if flagged:
        return flagged, "argv_resume_flag"
    return _open_file_session(vendor, open_files(tree, proc_root))


def _tmux(tmux_bin: str, runner: Runner, *args: str) -> list[list[str]] | None:
    """Run one tmux query; ``None`` when no server runs, an error when the query failed."""
    try:
        done = runner([tmux_bin, *args], capture_output=True, text=True, check=False)
    except OSError as exc:
        raise CollectError(f"tmux could not be run: {exc}") from exc
    if done.returncode != 0:
        if any(marker in (done.stderr or "") for marker in NO_SERVER_MARKERS):
            return None
        raise CollectError(f"tmux {args[0]} failed: {(done.stderr or '').strip()[:200]}")
    return [line.split("\t") for line in (done.stdout or "").splitlines() if line]


def _session_facts(tmux_bin: str, runner: Runner, session: str) -> dict[str, Any]:
    """Return a session's named environment keys and its label focus."""
    target = f"={session}:"
    env: dict[str, str] = {}
    for line in _tmux(tmux_bin, runner, "show-environment", "-t", target) or []:
        name, _, value = "\t".join(line).partition("=")
        if name in NAMED_KEYS:
            env[name] = value
    details = (
        _tmux(tmux_bin, runner, "show", "-t", target, "-v", "-q", "@synapse_label_details") or []
    )
    try:
        focus = json.loads("\t".join(details[0])).get("focus") if details else None
    except (ValueError, AttributeError):
        focus = None
    return {**env, "label": {"focus": focus, "now": None, "state": None}}


def _started_at(proc: Proc, proc_root: Path, now: Callable[[], float] = time.time) -> str | None:
    """Return when a process started, from its start ticks and the time since boot."""
    try:
        since_boot = float((proc_root / "uptime").read_text(encoding="ascii").split()[0])
    except (OSError, ValueError, IndexError):
        return None
    started = now() - since_boot + proc.start_ticks / os.sysconf("SC_CLK_TCK")
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started))


@dataclass
class Host:
    """What a collector reads: processes, tmux, the user's home and ``/proc``."""

    procs: dict[int, Proc]
    tmux_bin: str = "tmux"
    runner: Runner = subprocess.run
    home: Path | None = None
    proc_root: Path = Path("/proc")
    boot: str = field(default_factory=boot_id)


def _observation(
    host: Host, pid: int, kids: Mapping[int, list[int]], facts: Mapping[str, Any]
) -> dict[str, Any]:
    proc = host.procs[pid]
    vendor = agent_vendor(proc.argv)
    native, source = native_session(
        vendor, pid, host.procs, kids, home=host.home, proc_root=host.proc_root
    )
    try:
        cwd: str | None = os.readlink(host.proc_root / str(pid) / "cwd")
    except OSError:
        cwd = None
    return {
        "row_id": row_id(host.boot, pid, proc.start_ticks),
        "identity": facts.get("SYN_IDENTITY"),
        "project": facts.get("SYN_PROJECT"),
        "vendor": vendor,
        "pid": pid,
        "pid_starttime": proc.start_ticks,
        "cwd": cwd,
        "started_at": _started_at(proc, host.proc_root),
        "tmux": facts.get("tmux"),
        "label": facts.get("label", {"focus": None, "now": None, "state": None}),
        "native_session_id": native,
        "native_session_id_source": source,
    }


def observe_host(host: Host) -> dict[str, dict[str, Any]]:
    """Return every agent session on the host now, keyed by row.

    Raises
    ------
    CollectError
        When a tmux query fails; a host without a tmux server is not a failure.
    """
    kids = children(host.procs)
    rows: dict[str, dict[str, Any]] = {}
    covered: set[int] = set()
    panes = (
        _tmux(
            host.tmux_bin,
            host.runner,
            "list-panes",
            "-a",
            "-F",
            "#{session_name}\t#{window_index}.#{pane_index}\t#{pane_pid}",
        )
        or []
    )
    facts_of: dict[str, dict[str, Any]] = {}
    for session, pane, pane_pid in panes:
        covered.update([int(pane_pid), *descendants(int(pane_pid), kids)])
        agent = topmost_agent(int(pane_pid), host.procs, kids)
        if agent is None:
            continue
        if session not in facts_of:
            facts_of[session] = _session_facts(host.tmux_bin, host.runner, session)
        facts = {**facts_of[session], "tmux": {"session": session, "pane": pane}}
        found = _observation(host, agent, kids, facts)
        rows[found["row_id"]] = found
    for pid, proc in host.procs.items():
        vendor = agent_vendor(proc.argv)
        parent = host.procs.get(proc.ppid)
        if not vendor or pid in covered or (parent and agent_vendor(parent.argv) == vendor):
            continue
        found = _observation(host, pid, kids, named_environ(pid, host.proc_root))
        rows[found["row_id"]] = found
    return rows


START_FIELDS = (
    "identity",
    "project",
    "vendor",
    "pid",
    "pid_starttime",
    "cwd",
    "started_at",
    "tmux",
)


@dataclass
class Collected:
    """What one snapshot found and wrote, and the identities it saw running."""

    rows: int = 0
    started: int = 0
    observed: int = 0
    ended: int = 0
    identities: frozenset[str] = frozenset()


def running_identities(view: Mapping[str, Any]) -> frozenset[str]:
    """Return the identities a snapshot's view sees running."""
    rows = view.get("rows", {})
    return frozenset(
        str(row["identity"])
        for row in rows.values()
        if isinstance(row, dict) and row.get("identity")
    )


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _load(path: Path) -> dict[str, Any]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) and loaded.get("schema") == SCHEMA else {}


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Replace ``path`` atomically with a private copy of ``payload``."""
    temp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, (json.dumps(payload, indent=1, sort_keys=True) + "\n").encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(temp, path)
    sync_directory(path.parent)


def _ends(
    previous: Mapping[str, Any],
    observed: Mapping[str, Any],
    open_rows: set[str],
    boot: str,
    at: str,
) -> list[dict[str, Any]]:
    """Return an end for every earlier row that is gone or was cut by a reboot and has none yet."""
    same_boot = previous.get("boot_id") == boot
    kind = "observed_gone" if same_boot else "boot_cut"
    return [
        {
            "event": "ended_unobserved",
            "row_id": key,
            "end_kind": kind,
            "ended_at": at,
            "last_seen": previous.get("recorded_at"),
        }
        for key in previous.get("rows", {})
        if key in open_rows and not (same_boot and key in observed)
    ]


def _news(
    previous: Mapping[str, Any],
    observed: Mapping[str, Mapping[str, Any]],
    started: set[str],
    boot: str,
) -> list[dict[str, Any]]:
    """Return a start for each row nobody started, and an observation of what changed."""
    before_rows = previous.get("rows", {}) if previous.get("boot_id") == boot else {}
    events: list[dict[str, Any]] = []
    for key, now in observed.items():
        if key not in started:
            events.append(
                {
                    "event": "start",
                    "row_id": key,
                    **{name: now.get(name) for name in START_FIELDS},
                    "launcher": "observed",
                    "holder": None,
                    "permission_profile": None,
                    "restore_priority": None,
                }
            )
        before = before_rows.get(key, {})
        changed = {
            name: now.get(name) for name in OBSERVED_FIELDS if now.get(name) != before.get(name)
        }
        if changed:
            events.append({"event": "observe", "row_id": key, **changed})
    return events


def survey(directory: Path, host: Host) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return the events a snapshot would write and the view it would keep; write nothing.

    A restore's dry run reads the host this way: no line, no ``seq``, no
    ``current.json`` and no ``boots/`` file, and no directory is created.

    Raises
    ------
    CollectError
        When the host cannot be read.
    """
    observed = observe_host(host)
    previous = _load(directory / "current.json")
    rows = fold(read_events(directory)) if directory.is_dir() else {}
    open_rows = {key for key, row in rows.items() if row.open}
    started = {key for key, row in rows.items() if row.start}
    at = _now()
    events = _ends(previous, observed, open_rows, host.boot, at) + _news(
        previous, observed, started, host.boot
    )
    return events, {"schema": SCHEMA, "boot_id": host.boot, "recorded_at": at, "rows": observed}


def collect(directory: Path, host: Host) -> Collected:
    """Record what runs on the host now, and keep the view for the next snapshot.

    Raises
    ------
    CollectError
        When the host cannot be read; nothing is written then.
    LedgerError
        When a line cannot be written.
    """
    events, snapshot = survey(directory, host)
    ensure_private_dir(directory, parents=True, purpose="session ledger")
    ensure_private_dir(directory / "boots", purpose="session ledger boots")
    for event in events:
        append_event(event, directory=directory, wait=COLLECTOR_LOCK_WAIT_SECONDS, boot=host.boot)
    _write_json(directory / "current.json", snapshot)
    _write_json(directory / "boots" / f"{host.boot or 'unknown'}.json", snapshot)
    kinds = [event["event"] for event in events]
    return Collected(
        len(snapshot["rows"]),
        kinds.count("start"),
        kinds.count("observe"),
        kinds.count("ended_unobserved"),
        running_identities(snapshot),
    )
