# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — identity-scoped stale waiter listing and cleanup
"""List and safely reap stale shell-hook waiter sidecars.

The shell integration starts one background ``synapse arm`` process per resolved
identity and records its PID in ``$XDG_RUNTIME_DIR/synapse-shell/<safe identity>.pid``
or, when no XDG runtime directory exists, under the operator's private cache
(``$XDG_CACHE_HOME/synapse-shell`` / ``~/.cache/synapse-shell``, with a
uid-keyed temp fallback only as last resort). This module is the safe cleanup
companion for that hook: it only looks at the pidfile for the resolved
``syn`` identity, verifies a live process is actually that identity's
``synapse arm --name <identity>-rx --for <project>`` waiter before signalling it,
and removes dead pidfiles without sending any signal.

There is intentionally no pattern matching or process-name sweeping here. Every
cleanup decision is scoped by identity, PID, pidfile, and command-line evidence.
"""

from __future__ import annotations

import argparse
import os
import re
import signal
import sys
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol

from synapse_channel.waiter_identity import is_waiter, waiter_owner

CmdlineReader = Callable[[int], Sequence[str] | None]
"""Callable that returns a process argv vector for a PID, or ``None`` when dead."""

ProcessKiller = Callable[[int, signal.Signals], None]
"""Callable used to signal a PID."""

SAFE_KEY_PATTERN = re.compile(r"[^A-Za-z0-9_.-]")
"""Characters outside the shell-hook safe-key set."""


class ReapIdentity(Protocol):
    """Structural identity contract needed by the reaper."""

    @property
    def project(self) -> str:
        """Project that the waiter listens for."""
        ...  # pragma: no cover

    @property
    def identity(self) -> str:
        """Full sender identity represented by the pidfile."""
        ...  # pragma: no cover

    @property
    def waiter_name(self) -> str:
        """Distinct ``-rx`` waiter identity."""
        ...  # pragma: no cover


class ReapStatus(Enum):
    """Outcome of an identity-scoped waiter cleanup attempt."""

    NOT_FOUND = "not-found"
    REMOVED_STALE_PIDFILE = "removed-stale-pidfile"
    SIGNALED = "signaled"
    REFUSED_UNVERIFIED = "refused-unverified"
    SIGNAL_FAILED = "signal-failed"


@dataclass(frozen=True)
class WaiterProcess:
    """A shell-hook waiter candidate discovered from the identity pidfile.

    Attributes
    ----------
    pid : int
        PID recorded in the identity-scoped pidfile.
    identity : str
        Resolved sender identity whose shell-hook sidecar is represented.
    waiter_name : str
        Distinct waiter identity, normally ``<identity>-rx``.
    project : str
        Project the waiter listens for.
    pidfile : Path
        Identity-scoped pidfile path.
    argv : tuple[str, ...]
        Process command line read from ``/proc`` or an injected reader.
    live : bool
        ``True`` when the process still has a readable command line.
    verified : bool
        ``True`` only when the argv matches the expected Synapse arm waiter.
    """

    pid: int
    identity: str
    waiter_name: str
    project: str
    pidfile: Path
    argv: tuple[str, ...]
    live: bool
    verified: bool


@dataclass(frozen=True)
class ReapResult:
    """Result returned by ``reap_waiter``."""

    status: ReapStatus
    pid: int
    detail: str | None


def safe_key(identity: str) -> str:
    """Return the shell-hook pidfile key for an identity.

    The implementation mirrors the hook's ``tr -c 'A-Za-z0-9_.-' '_'`` transform,
    which means project sub-identities such as ``project/codex-1`` map to
    ``project_codex-1.pid``.
    """
    return SAFE_KEY_PATTERN.sub("_", identity)


def private_runtime_parent(env: Mapping[str, str] | None = None) -> Path:
    """Return a private directory parent when ``XDG_RUNTIME_DIR`` is unset.

    Preference order matches the generated shell hooks:

    1. ``$XDG_CACHE_HOME`` when set
    2. ``$HOME/.cache`` when home is known
    3. uid-keyed directory under the process temp root (never a shared
       world-writable ``/tmp/synapse-*`` path shared by every user)

    Parameters
    ----------
    env : Mapping[str, str] or None, optional
        Environment mapping; defaults to ``os.environ``.

    Returns
    -------
    Path
        Private parent for ``synapse-shell`` / ``synapse-provider-tmux``.
    """
    from tempfile import gettempdir

    env = os.environ if env is None else env
    cache = env.get("XDG_CACHE_HOME", "").strip()
    if cache:
        return Path(cache)
    home = env.get("HOME", "").strip()
    if home:
        return Path(home) / ".cache"
    from synapse_channel.core.secure_path import private_temp_user_segment

    return Path(gettempdir()) / f"synapse-user-{private_temp_user_segment()}"


def runtime_dir(env: Mapping[str, str] | None = None) -> Path:
    """Return the shell-hook runtime directory for ``env``.

    Parameters
    ----------
    env : Mapping[str, str] or None, optional
        Environment mapping to read ``XDG_RUNTIME_DIR`` from; defaults to
        ``os.environ``.

    Returns
    -------
    Path
        ``$XDG_RUNTIME_DIR/synapse-shell`` when set; otherwise a private
        cache path (never a shared world-writable ``/tmp/synapse-shell``).
    """
    env = os.environ if env is None else env
    root = env.get("XDG_RUNTIME_DIR", "").strip()
    if root:
        return Path(root) / "synapse-shell"
    return private_runtime_parent(env) / "synapse-shell"


def provider_runtime_dir(env: Mapping[str, str] | None = None) -> Path:
    """Return the tmux-provider pidfile directory for ``env``.

    Parameters
    ----------
    env : Mapping[str, str] or None, optional
        Environment mapping; defaults to ``os.environ``.

    Returns
    -------
    Path
        ``$XDG_RUNTIME_DIR/synapse-provider-tmux`` or private-cache fallback.
    """
    env = os.environ if env is None else env
    root = env.get("XDG_RUNTIME_DIR", "").strip()
    if root:
        return Path(root) / "synapse-provider-tmux"
    return private_runtime_parent(env) / "synapse-provider-tmux"


def pidfile_for(identity: ReapIdentity, *, runtime: Path) -> Path:
    """Return the identity-scoped shell-hook pidfile."""
    return runtime / f"{safe_key(identity.identity)}.pid"


def _read_pidfile(path: Path) -> int | None:
    """Return the positive PID recorded in ``path``, if valid."""
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text.isdigit():
        return None
    pid = int(text)
    return pid if pid > 0 else None


def read_proc_cmdline(pid: int) -> tuple[str, ...] | None:
    """Read ``/proc/<pid>/cmdline`` as an argv tuple.

    Returns ``None`` when the process is gone, inaccessible, or has no command
    line. The caller treats that as a dead or non-actionable PID rather than
    attempting any broad process search.
    """
    try:
        raw = (Path(os.path.sep) / "proc" / str(pid) / "cmdline").read_bytes()
    except OSError:
        return None
    parts = tuple(part.decode("utf-8", errors="replace") for part in raw.split(b"\0") if part)
    return parts or None


def _has_arg_pair(argv: Sequence[str], flag: str, value: str) -> bool:
    """Return whether ``argv`` contains ``flag`` immediately followed by ``value``."""
    return any(
        item == flag and index + 1 < len(argv) and argv[index + 1] == value
        for index, item in enumerate(argv)
    )


def _looks_like_synapse_entrypoint(argv: Sequence[str]) -> bool:
    """Return whether argv includes a Synapse CLI entrypoint token."""
    names = {Path(item).name for item in argv}
    return bool({"synapse", "syn"} & names) or "synapse_channel.cli" in argv


def _is_verified_waiter(argv: Sequence[str], identity: ReapIdentity) -> bool:
    """Return whether ``argv`` is the exact waiter for ``identity``."""
    return (
        "arm" in argv
        and _looks_like_synapse_entrypoint(argv)
        and _has_arg_pair(argv, "--name", identity.waiter_name)
        and (
            _has_arg_pair(argv, "--for", identity.identity)
            or _has_arg_pair(argv, "--for", identity.project)
        )
    )


def _flag_value(argv: Sequence[str], flag: str) -> str | None:
    """Return the value following ``flag`` in ``argv``, if any."""
    for index, item in enumerate(argv):
        if item == flag and index + 1 < len(argv):
            return argv[index + 1]
    return None


TERMINAL_PID_PATTERN = re.compile(r"/terminal-(\d+)$")
"""Shell-hook identities embed the arming shell's PID as ``…/terminal-<pid>``."""


def _owner_pid_of(argv: Sequence[str], identity: str) -> int | None:
    """Return the process the waiter serves: its recorded owner or terminal PID.

    A leashed waiter carries ``--owner-pid``; an older hook encodes the arming
    shell's PID in the identity itself (``…/terminal-<pid>``). ``None`` means the
    waiter names no checkable owner (a service identity), so staleness cannot be
    judged from process liveness and the waiter is left alone.
    """
    recorded = _flag_value(argv, "--owner-pid")
    if recorded is not None and recorded.isdigit():
        return int(recorded)
    match = TERMINAL_PID_PATTERN.search(identity)
    return int(match.group(1)) if match else None


def _pid_exists(pid: int) -> bool:
    """Return whether ``pid`` is a live process (signal 0 probe)."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def discover_waiters(
    identity: ReapIdentity,
    *,
    runtime: Path | None = None,
    env: Mapping[str, str] | None = None,
    cmdline_reader: CmdlineReader = read_proc_cmdline,
) -> list[WaiterProcess]:
    """Discover the shell-hook waiter pidfile for one resolved identity.

    Parameters
    ----------
    identity : ReapIdentity
        Resolved ``syn`` identity whose sidecar should be listed.
    runtime : Path or None, optional
        Runtime directory override for tests. Defaults to ``runtime_dir(env)``.
    env : Mapping[str, str] or None, optional
        Environment mapping used when ``runtime`` is omitted.
    cmdline_reader : callable, optional
        Process argv reader. Defaults to ``/proc/<pid>/cmdline``.

    Returns
    -------
    list[WaiterProcess]
        Empty when the identity has no pidfile, otherwise one candidate describing
        whether the recorded PID is live and verified.
    """
    root = runtime_dir(env) if runtime is None else runtime
    pidfile = pidfile_for(identity, runtime=root)
    pid = _read_pidfile(pidfile)
    if pid is None:
        return []
    argv = tuple(cmdline_reader(pid) or ())
    return [
        WaiterProcess(
            pid=pid,
            identity=identity.identity,
            waiter_name=identity.waiter_name,
            project=identity.project,
            pidfile=pidfile,
            argv=argv,
            live=bool(argv),
            verified=bool(argv) and _is_verified_waiter(argv, identity),
        )
    ]


def reap_waiter(
    identity: ReapIdentity,
    pid: int,
    *,
    runtime: Path | None = None,
    env: Mapping[str, str] | None = None,
    cmdline_reader: CmdlineReader = read_proc_cmdline,
    killer: ProcessKiller = os.kill,
) -> ReapResult:
    """Clean up one identity-scoped waiter PID.

    Dead PIDs only remove the identity pidfile. Live PIDs receive ``SIGTERM`` only
    after their argv verifies as this exact identity's Synapse arm waiter.

    Parameters
    ----------
    identity : ReapIdentity
        Resolved identity whose waiter pidfile is authoritative.
    pid : int
        PID requested by the operator.
    runtime : Path or None, optional
        Runtime directory override for tests.
    env : Mapping[str, str] or None, optional
        Environment mapping used when ``runtime`` is omitted.
    cmdline_reader : callable, optional
        Process argv reader.
    killer : callable, optional
        Signal sender. Defaults to ``os.kill``.

    Returns
    -------
    ReapResult
        Status, PID, and optional detail for CLI reporting.
    """
    found = discover_waiters(identity, runtime=runtime, env=env, cmdline_reader=cmdline_reader)
    if not found or found[0].pid != pid:
        return ReapResult(ReapStatus.NOT_FOUND, pid, "no matching identity pidfile")
    waiter = found[0]
    if not waiter.live:
        try:
            waiter.pidfile.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            return ReapResult(ReapStatus.SIGNAL_FAILED, pid, str(exc))
        return ReapResult(ReapStatus.REMOVED_STALE_PIDFILE, pid, None)
    if not waiter.verified:
        return ReapResult(ReapStatus.REFUSED_UNVERIFIED, pid, "process is not this synapse waiter")
    try:
        killer(pid, signal.SIGTERM)
    except OSError as exc:
        return ReapResult(ReapStatus.SIGNAL_FAILED, pid, str(exc))
    return ReapResult(ReapStatus.SIGNALED, pid, "TERM")


@dataclass(frozen=True)
class StaleVerdict:
    """One pidfile-driven sweep candidate and what was decided about it.

    Attributes
    ----------
    waiter : WaiterProcess
        The candidate as discovered from its pidfile and argv.
    owner_pid : int or None
        Process the waiter serves (recorded ``--owner-pid`` or the terminal PID
        embedded in the identity), when one is nameable.
    action : str
        ``"kept"`` (owner alive or unjudgeable), ``"signaled"`` (stale, TERM
        sent), ``"removed-pidfile"`` (recorded process already dead), or
        ``"refused-unverified"`` (live process is not a Synapse waiter — never
        signalled).
    """

    waiter: WaiterProcess
    owner_pid: int | None
    action: str


def sweep_stale_waiters(
    *,
    runtime: Path | None = None,
    env: Mapping[str, str] | None = None,
    cmdline_reader: CmdlineReader = read_proc_cmdline,
    killer: ProcessKiller = os.kill,
    pid_probe: Callable[[int], bool] = _pid_exists,
    act: bool = True,
) -> list[StaleVerdict]:
    """Sweep every shell-hook pidfile and reap waiters whose owner is gone.

    The sweep stays evidence-scoped, like the single-identity path: candidates
    come only from pidfiles in the hook's runtime directory, a live process is
    touched only when its argv verifies as a Synapse arm waiter whose ``--name``
    matches the pidfile it was recorded in, and it is signalled only when the
    process it serves — the recorded ``--owner-pid`` or the terminal PID in the
    identity — is demonstrably dead. A waiter with no nameable owner is kept.
    This is the first-class form of the cleanup that removed 105 dead-terminal
    waiters (of 166) found holding live hub sockets on one workstation.

    Parameters
    ----------
    runtime, env, cmdline_reader, killer : optional
        Injection points mirroring :func:`reap_waiter`.
    pid_probe : callable, optional
        Liveness probe for owner PIDs.
    act : bool, optional
        When ``False``, report verdicts without signalling or unlinking.

    Returns
    -------
    list[StaleVerdict]
        One verdict per pidfile, in sorted pidfile order.
    """
    root = runtime_dir(env) if runtime is None else runtime
    try:
        pidfiles = sorted(root.glob("*.pid"))
    except OSError:
        return []
    verdicts: list[StaleVerdict] = []
    for pidfile in pidfiles:
        pid = _read_pidfile(pidfile)
        if pid is None:
            continue
        argv = tuple(cmdline_reader(pid) or ())
        name = _flag_value(argv, "--name") or ""
        identity = waiter_owner(name)
        project = _flag_value(argv, "--for") or ""
        verified = (
            bool(argv)
            and "arm" in argv
            and _looks_like_synapse_entrypoint(argv)
            and is_waiter(name)
            and pidfile.name == f"{safe_key(identity)}.pid"
        )
        waiter = WaiterProcess(
            pid=pid,
            identity=identity,
            waiter_name=name,
            project=project,
            pidfile=pidfile,
            argv=argv,
            live=bool(argv),
            verified=verified,
        )
        if not waiter.live:
            if act:
                with suppress(OSError):
                    pidfile.unlink()
            verdicts.append(StaleVerdict(waiter, None, "removed-pidfile"))
            continue
        if not waiter.verified:
            verdicts.append(StaleVerdict(waiter, None, "refused-unverified"))
            continue
        owner = _owner_pid_of(argv, identity)
        if owner is None or pid_probe(owner):
            verdicts.append(StaleVerdict(waiter, owner, "kept"))
            continue
        action = "signaled"
        if act:
            try:
                killer(pid, signal.SIGTERM)
            except OSError:
                action = "signal-failed"
            else:
                with suppress(OSError):
                    pidfile.unlink()
        verdicts.append(StaleVerdict(waiter, owner, action))
    return verdicts


def build_parser() -> argparse.ArgumentParser:
    """Build the ``syn reap`` parser."""
    parser = argparse.ArgumentParser(
        prog="syn reap",
        description="List or safely terminate this identity's shell-hook waiter sidecar.",
    )
    parser.add_argument(
        "--pid",
        type=int,
        default=None,
        help="PID to clean up; must match this identity's pidfile.",
    )
    parser.add_argument(
        "--stale",
        action="store_true",
        help=(
            "Sweep every shell-hook pidfile and reap verified waiters whose owner "
            "shell or terminal process is dead."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="With --stale: report verdicts without signalling or removing anything.",
    )
    return parser


def _print_waiters(waiters: Sequence[WaiterProcess]) -> None:
    """Print waiter candidates in a compact operator-readable form."""
    if not waiters:
        print("no waiter pidfile for this identity")
        return
    for waiter in waiters:
        live = "live" if waiter.live else "stale"
        verified = "verified" if waiter.verified else "unverified"
        argv = " ".join(waiter.argv) if waiter.argv else "-"
        print(f"{waiter.pid} {live} {verified} {waiter.pidfile} {argv}")


def main(
    identity: ReapIdentity,
    argv: Sequence[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    runtime: Path | None = None,
    cmdline_reader: CmdlineReader = read_proc_cmdline,
    killer: ProcessKiller = os.kill,
    pid_probe: Callable[[int], bool] = _pid_exists,
) -> int:
    """Run the ``syn reap`` identity-scoped cleanup command.

    With no ``--pid``, the command lists the single pidfile candidate for the
    resolved identity. With ``--pid``, it removes a dead pidfile or sends SIGTERM
    only when that PID is verified as this identity's Synapse arm waiter.
    """
    args = build_parser().parse_args(list(argv or ()))
    if args.stale:
        verdicts = sweep_stale_waiters(
            runtime=runtime,
            env=env,
            cmdline_reader=cmdline_reader,
            killer=killer,
            pid_probe=pid_probe,
            act=not args.dry_run,
        )
        prefix = "would " if args.dry_run else ""
        for verdict in verdicts:
            owner = f" owner={verdict.owner_pid}" if verdict.owner_pid is not None else ""
            print(
                f"{prefix}{verdict.action}: {verdict.waiter.pid} {verdict.waiter.identity}{owner}"
            )
        swept = sum(1 for verdict in verdicts if verdict.action == "signaled")
        kept = sum(1 for verdict in verdicts if verdict.action == "kept")
        print(f"{prefix}reaped {swept} stale waiter(s); kept {kept} live")
        return 0
    if args.pid is None:
        _print_waiters(
            discover_waiters(identity, runtime=runtime, env=env, cmdline_reader=cmdline_reader)
        )
        return 0
    result = reap_waiter(
        identity,
        args.pid,
        runtime=runtime,
        env=env,
        cmdline_reader=cmdline_reader,
        killer=killer,
    )
    if result.status in {ReapStatus.SIGNALED, ReapStatus.REMOVED_STALE_PIDFILE}:
        detail = f" ({result.detail})" if result.detail else ""
        print(f"{result.status.value}: {result.pid}{detail}")
        return 0
    detail = f": {result.detail}" if result.detail else ""
    print(f"refused: {result.status.value}: {result.pid}{detail}", file=sys.stderr)
    return 1
