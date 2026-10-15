# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — lease-serialising CLI commands (lock, release)
"""The lease-oriented ``synapse`` subcommands.

``lock`` acquires a finite hub lease before running a wrapped command and attempts
a bounded release afterwards. During the child it renews the original ownership
generation and stops the owned process on lease loss or an uncertain renewal.
``release`` manually
drops a claim the caller owns (the escape hatch for an ``--auto-release-on
manual`` claim). Both open their own short-lived client and watch for the hub's
grant/deny verdict rather than sharing the read-side query plumbing, so they live
here apart from the hub-query verbs; :func:`add_parsers` registers their
subparsers on the top-level CLI.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from synapse_channel.cli_lock_process import run_locked_subprocess
from synapse_channel.cli_lock_release import LockRelease, display_hub_uri, valid_lock_timeouts
from synapse_channel.cli_lock_supervision import (
    claim_verdict_matches,
    connect_lock_owner,
    finish_lock,
    lease_duration,
    supervise_locked_command,
)
from synapse_channel.cli_release import (
    _load_release_receipt as _load_release_receipt,
)
from synapse_channel.cli_release import (
    _receipt_freshness as _receipt_freshness,
)
from synapse_channel.cli_release import (
    _receipt_list as _receipt_list,
)
from synapse_channel.cli_release import (
    _release as _release,
)
from synapse_channel.cli_release import (
    _validate_release_receipt_identity as _validate_release_receipt_identity,
)
from synapse_channel.client.agent import SynapseAgent, default_hub_uri
from synapse_channel.client.owner_registration import close_owner
from synapse_channel.connect_failures import explain_silent_outcome
from synapse_channel.core.protocol import SENDER_HUB, MessageType
from synapse_channel.git.ordinary_claim import (
    OrdinaryClaimScopeError,
    resolve_ordinary_claim_scope,
)

logger = logging.getLogger("synapse.lock")

AgentFactory = Callable[..., SynapseAgent]
LockRunner = Callable[[list[str]], Awaitable[int]]
_run_subprocess = run_locked_subprocess


def _lock_scope(
    task_id: str,
    paths: list[str],
) -> tuple[str, list[str], dict[str, object] | None]:
    """Resolve file scope while keeping pathless mutexes in separate namespaces."""
    if not paths:
        return task_id, paths, None
    resolved = resolve_ordinary_claim_scope(paths)
    if resolved is None:
        return "", paths, None
    return resolved.worktree, list(resolved.paths), resolved.path_identity


async def _lock(
    *,
    uri: str,
    name: str,
    task_id: str,
    command: list[str],
    paths: list[str],
    wait_timeout: float,
    agent_factory: AgentFactory = SynapseAgent,
    token: str | None = None,
    runner: LockRunner = _run_subprocess,
    retry_interval: float = 1.0,
    ready_timeout: float = 5.0,
    attempts: int = 40,
    poll_interval: float = 0.05,
    release_timeout: float | None = None,
) -> int:
    """Supervise ``command`` under its original lease, then confirm release.

    The hub grants only one live lease per task id, so wrapping a commit in
    ``synapse lock <project>:git -- git push`` defers participating contenders
    while that lease remains live. The acquisition client disconnects before the
    child starts. Short-lived same-owner connections renew only the original
    live epoch. Loss or unknown renewal stops the owned command before bounded
    cleanup. External effects still require resource-side fencing.
    A lock with no explicit ``paths`` is a
    pure named mutex: its claim is namespaced to its own task id, so two different
    locks never contend (one repo's ``:git`` push-lock cannot block another repo's
    lock). Passing ``paths`` opts into shared file-scope overlap instead.

    Parameters
    ----------
    uri, name : str
        Hub URI and the connecting identity.
    task_id : str
        The lease key, e.g. ``"quantum:git"``.
    command : list[str]
        The command to supervise after confirmed acquisition.
    paths : list[str]
        Optional file-scope paths to lock alongside the id.
    wait_timeout : float
        Seconds to keep retrying while another agent holds the lease; ``0`` fails fast.
    agent_factory : AgentFactory, optional
        Factory for the client agent; injectable for testing.
    token : str or None, optional
        Shared-secret token for a secured hub.
    runner : LockRunner, optional
        Coroutine that runs the command; injectable for testing.
    retry_interval : float, optional
        Seconds to wait between denied claim attempts.
    ready_timeout : float, optional
        Seconds to wait for the hub connection readiness event.
    attempts : int, optional
        Claim verdict polling attempts per claim request.
    poll_interval : float, optional
        Seconds to wait between verdict polls.
    release_timeout : float or None, optional
        Seconds per renewal or release confirmation exchange; ``None`` keeps
        the default bound (``attempts`` polls). Renewal is additionally capped
        by the remaining confirmed lifetime and termination allowance.

    Returns
    -------
    int
        Preserve a nonzero command exit. A successful command returns ``0`` only
        after confirmed cleanup, ``1`` after an explicit release refusal, or
        ``3`` when cleanup is uncertain. Invalid timeouts return ``2`` before
        acquisition; unreachable or refused acquisition returns ``1``.
    """
    reply_timeout = release_timeout if release_timeout is not None else attempts * poll_interval
    if not valid_lock_timeouts(wait_timeout, ready_timeout, reply_timeout):
        print(
            "lock: timeouts must be finite; readiness and release must be within (0, 300]",
            file=sys.stderr,
        )
        return 2
    try:
        lock_worktree, lock_paths, path_identity = _lock_scope(task_id, paths)
    except OrdinaryClaimScopeError as exc:
        print(f"Could not acquire lock '{task_id}': {exc}")
        return 1

    outcome: dict[str, Any] = {}
    welcome: dict[str, Any] = {}
    request_id = ""

    async def collect(data: dict[str, Any]) -> None:
        """Collect this owner's claim verdict or an addressed hub refusal."""
        if data.get("type") == MessageType.WELCOME:
            welcome.update(data)
        if (
            data.get("type") == MessageType.ERROR
            and data.get("sender") == SENDER_HUB
            and data.get("target") == name
        ):
            outcome["error"] = str(data.get("payload") or "hub refused the request")
            return
        if not claim_verdict_matches(
            data,
            hub_id=agent.hub_id,
            task_id=task_id,
            request_id=request_id,
        ):
            return
        if data.get("type") == MessageType.CLAIM_GRANTED and data.get("owner") == name:
            outcome["granted"] = True
            outcome["epoch"] = data.get("epoch")
            outcome["duration"] = lease_duration(data)
            epoch = data.get("epoch")
            if type(epoch) is int and epoch > 0:
                release.initial_epoch = epoch
                release.expected_hub_id = str(data.get("hub_id"))
                release.expected_session = str(welcome.get("lock_renewal_session"))
        elif data.get("type") == MessageType.CLAIM_DENIED:
            outcome["denied"] = str(data.get("payload") or "held by another agent")

    agent = agent_factory(name, collect, uri=uri, verbose=False, token=token)
    conn_task: asyncio.Task[None] | None = None
    child_code: int | None = None
    release = LockRelease(
        uri=uri,
        name=name,
        task_id=task_id,
        initial_epoch=None,
        token=token,
        agent_factory=agent_factory,
        ready_timeout=ready_timeout,
        reply_timeout=reply_timeout,
        pinned_epoch=True,
    )
    release.renewal_paths = tuple(lock_paths)
    try:
        loop = asyncio.get_event_loop()
        deadline: float | None = None
        while True:
            request_id = uuid.uuid4().hex
            connected = await connect_lock_owner(agent, release, welcome)
            if connected is None:
                return 1
            conn_task, session = connected
            if deadline is None:
                deadline = loop.time() + wait_timeout
            outcome.clear()
            claim_started = loop.time()
            await agent.claim(
                task_id,
                worktree=lock_worktree,
                paths=lock_paths,
                path_identity=path_identity,
                local_renewal=True,
                idem_key=request_id,
            )
            for _ in range(attempts):
                if outcome or conn_task.done():
                    break
                await asyncio.sleep(poll_interval)
            if outcome.get("error"):
                print(f"Could not acquire lock '{task_id}': {outcome['error']}")
                return 1
            if outcome.get("granted"):
                if release.initial_epoch is None:
                    print("lock: malformed lease grant; no command started", file=sys.stderr)
                    return 3
                break
            if conn_task.done() and not outcome:
                print(
                    explain_silent_outcome(
                        name,
                        display_hub_uri(uri),
                        close_code=agent.last_close_code,
                        close_reason=agent.last_close_reason,
                        fallback=f"Could not acquire lock '{task_id}': hub connection closed",
                    )
                )
                return 1
            if wait_timeout <= 0 or loop.time() >= deadline:
                print(
                    explain_silent_outcome(
                        name,
                        display_hub_uri(uri),
                        close_code=agent.last_close_code,
                        close_reason=agent.last_close_reason,
                        fallback=(
                            f"Could not acquire lock '{task_id}': "
                            f"{outcome.get('denied', 'timed out')}"
                        ),
                    )
                )
                return 1
            await close_owner(agent, conn_task)
            conn_task = None
            await asyncio.sleep(retry_interval)
        # Task claims survive disconnection. Finish the listener's websocket
        # teardown before spawning the command, allowing its Git hooks to use
        # this same authenticated identity without a concurrent-name conflict.
        conn_task.cancel()
        await asyncio.gather(conn_task, return_exceptions=True)
        duration = outcome.get("duration")
        if duration is None:
            print("lock: malformed lease grant; no command started", file=sys.stderr)
            return 3
        child_code = await supervise_locked_command(
            command,
            runner,
            release,
            hub_id=agent.hub_id,
            session=session,
            duration=duration,
            acquired_at=claim_started,
        )
    finally:
        cleanup_code = await finish_lock(
            agent,
            conn_task,
            release if release.initial_epoch is not None else None,
            child_code,
        )
    assert child_code is not None
    return child_code or cleanup_code


def _cmd_lock(args: argparse.Namespace) -> int:
    """Dispatch the ``lock`` subcommand."""
    for path in args.paths or []:
        if "," in path and not Path(path).exists():
            print(
                f"lock: --paths takes one path per flag; {path!r} contains a comma "
                "and names no existing path. Repeat --paths for each path.",
                file=sys.stderr,
            )
            return 2
    return asyncio.run(
        _lock(
            uri=args.uri,
            name=args.name,
            task_id=args.task_id,
            command=args.command,
            paths=args.paths or [],
            wait_timeout=args.wait_timeout,
            token=args.token,
            ready_timeout=args.ready_timeout,
            release_timeout=args.release_timeout,
        )
    )


def _cmd_release(args: argparse.Namespace) -> int:
    """Dispatch the ``release`` subcommand: manually drop an owned claim."""
    return asyncio.run(
        _release(
            uri=args.uri,
            name=args.name,
            task_id=args.task_id,
            evidence=args.evidence,
            artifacts=args.artifacts,
            known_failures=args.known_failures,
            changed_files=args.changed_files,
            generated_artifacts=args.generated_artifacts,
            approvals=args.approvals,
            confidence=args.confidence,
            freshness_seconds=args.freshness_seconds,
            receipt=args.receipt,
            receipt_json=args.receipt_json,
            token=args.token,
            ready_timeout=args.ready_timeout,
            reply_timeout=getattr(args, "reply_timeout", 30.0),
            idem_key=getattr(args, "idem_key", None),
            request_digest=getattr(args, "request_digest", None),
            confirm_only=getattr(args, "confirm_only", False),
        )
    )


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``lock`` and ``release`` subparsers on the top-level CLI."""
    lock = subparsers.add_parser("lock", help="Supervise a command under a renewed, fenced lease.")
    lock.add_argument("task_id")
    lock.add_argument(
        "command", nargs="+", help="The command to start after lease acquisition (after --)."
    )
    lock.add_argument("--name", default="USER")
    lock.add_argument(
        "--paths", action="append", default=None, help="File-scope paths to lock (repeatable)."
    )
    lock.add_argument(
        "--wait-timeout",
        type=float,
        default=30.0,
        help="Seconds to keep retrying while another agent holds the lease; 0 fails fast.",
    )
    lock.add_argument(
        "--release-timeout",
        type=float,
        default=None,
        metavar="SECONDS",
        help=(
            "Seconds per bounded renewal, release or read-only confirmation exchange "
            "(default ~2s; 0 < seconds <= 300). Lease TTL remains the backstop. Distinct from "
            "--wait-timeout, which bounds ACQUIRING the lease."
        ),
    )
    lock.add_argument("--uri", default=default_hub_uri())
    lock.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    lock.add_argument(
        "--ready-timeout", type=float, default=5.0, help="Seconds to await hub readiness."
    )
    lock.set_defaults(func=_cmd_lock)

    release = subparsers.add_parser(
        "release", help="Manually drop a claim you own (e.g. an --auto-release-on manual claim)."
    )
    release.add_argument("task_id")
    release.add_argument(
        "--name", default="USER", help="The releasing identity; must own the claim."
    )
    release.add_argument("--uri", default=default_hub_uri())
    release.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    release.add_argument(
        "--evidence",
        action="append",
        default=[],
        help="Evidence line for the release receipt, such as a command and result.",
    )
    release.add_argument(
        "--artifact",
        dest="artifacts",
        action="append",
        default=[],
        help="Artifact path or URI attached to the release receipt.",
    )
    release.add_argument(
        "--known-failure",
        dest="known_failures",
        action="append",
        default=[],
        help="Known remaining failure or limitation attached to the release receipt.",
    )
    release.add_argument(
        "--changed-file",
        dest="changed_files",
        action="append",
        default=[],
        help="Changed file path attached to the release receipt.",
    )
    release.add_argument(
        "--generated-artifact",
        dest="generated_artifacts",
        action="append",
        default=[],
        help="Generated artifact path attached to the release receipt.",
    )
    release.add_argument(
        "--approval",
        dest="approvals",
        action="append",
        default=[],
        help="Approval or review reference attached to the release receipt.",
    )
    release.add_argument(
        "--confidence",
        default="",
        help="Caller-supplied confidence label for the release receipt.",
    )
    release.add_argument(
        "--freshness-seconds",
        type=float,
        default=None,
        help="Age in seconds of the newest evidence attached to the receipt.",
    )
    release.add_argument(
        "--receipt",
        default=None,
        help="Verified release receipt JSON produced by 'synapse verify-release'.",
    )
    release.add_argument(
        "--receipt-json",
        action="store_true",
        help="Print the release receipt as JSON after the hub confirms release.",
    )
    release.add_argument(
        "--ready-timeout", type=float, default=5.0, help="Seconds to await hub readiness."
    )
    release.add_argument(
        "--reply-timeout",
        type=float,
        default=30.0,
        help="Bound each complete send/reply exchange (0 < seconds <= 300).",
    )
    release.add_argument(
        "--idem-key", default=None, help="Unique release operation key; generated when omitted."
    )
    release.add_argument(
        "--request-digest",
        default=None,
        help="Original SHA-256 fingerprint; required with --confirm-only.",
    )
    release.add_argument(
        "--confirm-only",
        action="store_true",
        help="Read the exact durable result without sending another release.",
    )
    release.set_defaults(func=_cmd_release)
