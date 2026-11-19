# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the session ledger: one append-only record of every seat, per host
"""The session ledger, schema ``synapse.session-ledger.v1``.

Every agent session on a host gets one row, keyed by ``row_id`` =
``<boot_id>:<pid>:<pid_starttime>``. Events about the row are appended to a
private, per-user file, so a crash or a reboot leaves a record from which the
seats can be restored:

* ``start``: written by the launcher that owns the agent process (``seat-hold``,
  ``worker-session``), or by the collector for a row nobody launched;
* ``observe``: the fields that changed, such as a native session id or the
  state of a seat's pane waiter;
* ``end``: written by the launcher that waited for the process, with its exit
  status or signal;
* ``ended_unobserved``: written by the collector for a row that is gone and has
  no end, never with an exit status.

**Where:** ``$XDG_STATE_HOME/synapse/ledger/`` (``~/.local/state`` when unset),
mode 0700, never inside a git work tree or a coordination tree.

**How a line is written:** under one hold of ``ledger.lock``, the per-boot
``seq`` is written to ``seq.json`` first, then the whole line is appended with a
single ``write``. A failed write is cut back, and readers skip an unterminated
last line, so there is never a partial line and never a repeated ``seq``; a
crash between the two steps leaves only a gap. A launcher waits for the lock
about a second at most, then reports and goes on: the ledger never delays a
seat.

**What is never stored:** an argument vector, any environment variable other
than ``SYN_IDENTITY`` and ``SYN_PROJECT``, screen text, or a token.

**Retention:** v1 keeps every monthly file; nothing is removed.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import signal
import socket
import sys
import time
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from synapse_channel.core.private_dir import ensure_private_dir

SCHEMA = "synapse.session-ledger.v1"
"""The schema name on every v1 line and file."""

LAUNCHER_LOCK_WAIT_SECONDS = 1.0
"""How long a launcher waits for the ledger's lock before it gives the line up."""

COLLECTOR_LOCK_WAIT_SECONDS = 30.0
"""How long the collector waits for the lock."""

COORDINATION_MARKER = ".coordination"
"""A directory of this name marks a shared coordination tree."""

END_KINDS = frozenset({"exit", "signal"})
UNOBSERVED_END_KINDS = frozenset({"observed_gone", "boot_cut"})


class LedgerError(Exception):
    """The ledger cannot be used here, or a line could not be written."""


class NoLedger(LedgerError):
    """The environment names no state or home directory, so there is no ledger."""


def ledger_dir(environ: Mapping[str, str] | None = None) -> Path:
    """Return the ledger directory, refusing a shared or versioned location.

    The directory lies under the state directory the environment names
    (``XDG_STATE_HOME``, when absolute), or under its home directory.

    Raises
    ------
    NoLedger
        When the environment names neither.
    LedgerError
        When the directory would lie inside a git work tree or a coordination
        tree: it holds identities, working directories and session ids.
    """
    env = os.environ if environ is None else environ
    state = env.get("XDG_STATE_HOME", "").strip()
    home = env.get("HOME", "").strip() or env.get("USERPROFILE", "").strip()
    if state and Path(state).is_absolute():
        base = Path(state)
    elif home:
        base = Path(home) / ".local" / "state"
    else:
        raise NoLedger("the environment names no state or home directory")
    directory = (base / "synapse" / "ledger").resolve()
    tree = shared_tree(directory)
    if tree is not None:
        raise LedgerError(
            f"the session ledger would lie in a shared or versioned tree ({tree}); "
            "unset XDG_STATE_HOME or point it at ~/.local/state"
        )
    return directory


def shared_tree(path: Path) -> Path | None:
    """Return the git work tree or coordination tree that ``path`` lies in, if any.

    A git work tree is marked by a ``.git`` file (a linked work tree or a
    submodule) or by a ``.git`` directory that holds a ``HEAD``. A stray
    ``.git`` directory without one, which git itself does not read as a
    repository, does not make a home directory a work tree.
    """
    for parent in (path, *path.parents):
        marker = parent / ".git"
        if parent.name == COORDINATION_MARKER or marker.is_file() or (marker / "HEAD").is_file():
            return parent
    return None


def boot_id() -> str:
    """Return this boot's id, or an empty string where none can be read."""
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
    except OSError:
        return ""


def pid_starttime(pid: int) -> int | None:
    """Return the process's start time in clock ticks (field 22 of its stat), if readable."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        return int(stat.rsplit(")", 1)[1].split()[19])
    except (OSError, IndexError, ValueError):
        return None


def row_id(boot: str, pid: int, starttime: int) -> str:
    """Return the row's key: the boot, the PID and its start ticks."""
    return f"{boot}:{pid}:{starttime}"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@contextlib.contextmanager
def _locked(
    directory: Path, wait: float, *, sleep: Callable[[float], None] = time.sleep
) -> Iterator[None]:
    fd = os.open(directory / "ledger.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        deadline = time.monotonic() + wait
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise LedgerError(
                        f"the ledger is locked; the line was given up after {wait:g} s"
                    ) from None
                sleep(0.05)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _next_seq(directory: Path, boot: str) -> int:
    """Take the next per-boot number and store it before the line is written."""
    path = directory / "seq.json"
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        stored = {}
    current = (
        stored.get("seq", 0) if isinstance(stored, dict) and stored.get("boot_id") == boot else 0
    )
    following = (current if isinstance(current, int) else 0) + 1
    temp = path.with_name(f"seq.json.{os.getpid()}.tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, json.dumps({"boot_id": boot, "seq": following}).encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(temp, path)
    return following


def _append_line(path: Path, line: bytes) -> None:
    """Append one whole line; cut a failed write back so no partial line stays."""
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        size = os.fstat(fd).st_size
        if size:
            os.lseek(fd, size - 1, os.SEEK_SET)
            if os.read(fd, 1) != b"\n":  # a crashed writer's unterminated line
                with open(path, "rb") as stream:
                    size = stream.read().rfind(b"\n") + 1
                os.ftruncate(fd, size)
        try:
            if os.write(fd, line) != len(line):
                raise OSError("short write")
        except OSError:
            os.ftruncate(fd, size)
            raise
    finally:
        os.close(fd)


def append_event(
    event: Mapping[str, Any],
    *,
    directory: Path | None = None,
    wait: float = LAUNCHER_LOCK_WAIT_SECONDS,
    boot: str | None = None,
    host: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> int:
    """Append one event and return its ``seq``.

    The common fields (``schema``, ``host``, ``boot_id``, ``seq``, ``ts``) are
    added here.

    Raises
    ------
    LedgerError
        When the ledger cannot be used, its lock is not free within ``wait``,
        or the line cannot be written.
    """
    target = directory or ledger_dir(environ)
    try:
        ensure_private_dir(target, parents=True, purpose="session ledger")
        this_boot = boot_id() if boot is None else boot
        with _locked(target, wait):
            seq = _next_seq(target, this_boot)
            record = {
                "schema": SCHEMA,
                "host": host or socket.gethostname(),
                "boot_id": this_boot,
                "seq": seq,
                "ts": _now(),
                **event,
            }
            line = (json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
            _append_line(target / f"events-{time.strftime('%Y-%m', time.gmtime())}.jsonl", line)
    except LedgerError:
        raise
    except (OSError, ValueError) as exc:
        raise LedgerError(f"the ledger line could not be written: {exc}") from exc
    return seq


def read_events(directory: Path) -> Iterator[dict[str, Any]]:
    """Yield every v1 event in file order, skipping an unterminated last line."""
    for path in sorted(directory.glob("events-*.jsonl")):
        data = path.read_bytes()
        for raw in data[: data.rfind(b"\n") + 1].splitlines():
            try:
                event = json.loads(raw)
            except ValueError:
                continue
            if isinstance(event, dict) and event.get("schema") == SCHEMA:
                yield event


@dataclass
class Row:
    """What the events say about one row."""

    row_id: str
    start: dict[str, Any] = field(default_factory=dict)
    observed: dict[str, Any] = field(default_factory=dict)
    end: dict[str, Any] | None = None
    corrected: bool = False

    @property
    def open(self) -> bool:
        """Whether the row has no end."""
        return self.end is None


def fold(events: Iterable[Mapping[str, Any]]) -> dict[str, Row]:
    """Fold events into rows, applying the one-end rule.

    The first end of a row is final, with one exception: a real ``end`` may
    supersede an ``ended_unobserved`` when it comes from the launcher named in
    the row's ``start``, from the same boot, and names that line in
    ``supersedes_seq``. The corrected end is itself a real end, so it is
    final: there is at most one correction. Every other late end is ignored.
    """
    rows: dict[str, Row] = {}
    for event in events:
        key = event.get("row_id")
        if not isinstance(key, str):
            continue
        row = rows.setdefault(key, Row(key))
        kind = event.get("event")
        if kind == "start" and not row.start:
            row.start = dict(event)
        elif kind == "observe":
            row.observed.update({k: v for k, v in event.items() if k not in _COMMON})
        elif kind == "ended_unobserved" and row.end is None:
            row.end = dict(event)
        elif kind == "end":
            _take_end(row, event)
    return rows


_COMMON = frozenset({"schema", "host", "boot_id", "seq", "ts", "event", "row_id"})


def _take_end(row: Row, event: Mapping[str, Any]) -> None:
    if row.end is None:
        row.end = dict(event)
        return
    launcher = row.start.get("launcher")
    if (
        row.end.get("event") == "ended_unobserved"
        and launcher not in (None, "observed")
        and event.get("launcher") == launcher
        and event.get("boot_id") == row.end.get("boot_id")
        and row.end.get("end_kind") != "boot_cut"
        and event.get("supersedes_seq") == row.end.get("seq")
    ):
        row.end = dict(event)
        row.corrected = True


class LauncherRecord:
    """The lines a launcher writes about the one agent process it owns.

    A line that cannot be written is reported on standard error and never
    stops the seat; an environment without a ledger writes nothing.
    """

    def __init__(
        self,
        launcher: str,
        *,
        environ: Mapping[str, str] | None = None,
        directory: Path | None = None,
        report: Callable[[str], None] | None = None,
    ) -> None:
        self.launcher = launcher
        self.row: str | None = None
        self._environ = environ
        self._directory = directory
        self._report = report or _report

    def started(
        self,
        pid: int,
        *,
        identity: str,
        argv: Sequence[str],
        cwd: Path,
        holder: str | None = None,
        tmux: Mapping[str, str] | None = None,
    ) -> None:
        """Write the row's ``start`` once its process runs."""
        starttime = pid_starttime(pid)
        if starttime is None:
            return
        from synapse_channel.agent_tmux import provider_family_of

        self.row = row_id(boot_id(), pid, starttime)
        project, slash, _name = identity.partition("/")
        self._write(
            {
                "event": "start",
                "row_id": self.row,
                "identity": identity,
                "project": project if slash else None,
                "vendor": provider_family_of(argv),
                "pid": pid,
                "pid_starttime": starttime,
                "cwd": str(cwd),
                "started_at": _now(),
                "launcher": self.launcher,
                "holder": holder,
                "tmux": dict(tmux) if tmux else None,
                "permission_profile": None,
                "restore_priority": None,
            }
        )

    def observed(self, **fields: Any) -> None:
        """Write the fields that changed for the row."""
        if self.row is not None:
            self._write({"event": "observe", "row_id": self.row, **fields})

    def ended(self, returncode: int | None) -> None:
        """Write the row's ``end`` from the process's return code."""
        if self.row is None or returncode is None:
            return
        outcome: dict[str, Any] = (
            {"end_kind": "signal", "signal": _signal_name(-returncode)}
            if returncode < 0
            else {"end_kind": "exit", "exit_status": returncode}
        )
        self._write(
            {
                "event": "end",
                "row_id": self.row,
                "launcher": self.launcher,
                "ended_at": _now(),
                **outcome,
            }
        )

    def _write(self, event: Mapping[str, Any]) -> None:
        try:
            append_event(event, directory=self._directory, environ=self._environ)
        except NoLedger:
            return
        except LedgerError as exc:
            self._report(f"synapse ledger: {exc}")


def _signal_name(number: int) -> str:
    try:
        return signal.Signals(number).name
    except ValueError:
        return str(number)


def _report(line: str) -> None:
    print(line, file=sys.stderr, flush=True)


V0_SCHEMA = "ws-session-ledger.v0"
"""The interim tool's schema, read as input only."""

_V0_KEPT = ("row_id", "identity", "vendor", "pid", "pid_starttime", "label")


def read_v0(path: Path) -> Iterator[dict[str, Any]]:
    """Read a v0 event file as v1 events; nothing is ever written back in v0.

    ``observed`` and ``started`` become ``start`` rows with ``launcher:
    "observed"``; a ``changed`` agent row becomes the old row's
    ``ended_unobserved`` and the new row's ``start``; other changes become
    ``observe``. ``native_session`` is read as ``native_session_id``, and the
    stored resume command is dropped: a restore rebuilds it.
    """
    last_row: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(event, dict) or event.get("schema") != V0_SCHEMA:
            continue
        yield from _from_v0(event, last_row)


def _from_v0(event: dict[str, Any], last_row: dict[str, str]) -> Iterator[dict[str, Any]]:
    kind, key = event.get("event"), str(event.get("key", ""))
    common = {
        "schema": SCHEMA,
        "source_schema": V0_SCHEMA,
        "boot_id": event.get("boot_id"),
        "seq": event.get("seq"),
        "ts": event.get("ts"),
    }
    fields = {name: event.get(name) for name in _V0_KEPT}
    native = event.get("native_session_id") or event.get("native_session")
    tmux = None if key.startswith("bare:") or not key else {"session": key}
    start = {
        **common,
        **fields,
        "event": "start",
        "launcher": "observed",
        "holder": None,
        "tmux": tmux,
    }
    if native:
        start["native_session_id"] = native
    previous = last_row.get(key)
    if event.get("row_id"):
        last_row[key] = str(event["row_id"])
    if kind in ("observed", "started"):
        yield start
    elif kind == "changed" and "agent_row" in (event.get("fields") or []) and previous:
        yield {
            **common,
            "event": "ended_unobserved",
            "row_id": previous,
            "end_kind": "observed_gone",
        }
        yield start
    elif kind == "changed":
        yield {
            **common,
            "event": "observe",
            "row_id": event.get("row_id"),
            "native_session_id": native,
        }
    elif kind == "ended_unobserved":
        yield {
            **common,
            "event": "ended_unobserved",
            "row_id": event.get("row_id"),
            "end_kind": event.get("end_kind"),
            "ended_at": event.get("ended_at"),
            "last_seen": event.get("last_seen"),
        }
