# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — lease-serialising CLI commands (lock, release)
"""The lease-oriented ``synapse`` subcommands.

``lock`` holds a hub lease while running a wrapped command so several agents on
one repo take turns instead of clobbering each other, and ``release`` manually
drops a claim the caller owns (the escape hatch for an ``--auto-release-on
manual`` claim). Both open their own short-lived client and watch for the hub's
grant/deny verdict rather than sharing the read-side query plumbing, so they live
here apart from the hub-query verbs; :func:`add_parsers` registers their
subparsers on the top-level CLI.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from synapse_channel.client.agent import DEFAULT_HUB_URI, SynapseAgent
from synapse_channel.connect_failures import describe_connect_failure, explain_silent_outcome
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.receipts import build_release_receipt

AgentFactory = Callable[..., SynapseAgent]
LockRunner = Callable[[list[str]], Awaitable[int]]


def _load_release_receipt(path: str | Path) -> dict[str, Any]:
    """Load and validate a release receipt JSON object from ``path``."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("receipt must be a JSON object")
    return payload


def _receipt_list(
    payload: dict[str, Any],
    key: str,
    fallback: list[str] | None,
) -> list[str]:
    """Merge a repeated receipt field with explicit CLI values."""
    items: list[str] = []
    raw = payload.get(key)
    if raw is not None:
        if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
            raise ValueError(f"receipt field '{key}' must be a list of strings")
        items.extend(raw)
    items.extend(fallback or [])
    return items


def _receipt_freshness(payload: dict[str, Any], fallback: float | None) -> float | None:
    """Return explicit freshness when supplied, otherwise receipt freshness."""
    if fallback is not None:
        return fallback
    raw = payload.get("freshness_seconds")
    if raw is None:
        return None
    if not isinstance(raw, int | float):
        raise ValueError("receipt field 'freshness_seconds' must be a number")
    return float(raw)


def _validate_release_receipt_identity(
    payload: dict[str, Any],
    *,
    task_id: str,
    name: str,
) -> None:
    """Reject receipts whose task or owner would release the wrong claim."""
    receipt_task = payload.get("task_id")
    receipt_owner = payload.get("owner")
    if receipt_task is not None and receipt_task != task_id:
        raise ValueError(f"receipt task_id {receipt_task!r} does not match {task_id!r}")
    if receipt_owner is not None and receipt_owner != name:
        raise ValueError(f"receipt owner {receipt_owner!r} does not match {name!r}")


async def _run_subprocess(command: list[str]) -> int:
    """Run ``command`` and return its exit code (the default lock runner)."""
    proc = await asyncio.create_subprocess_exec(*command)
    return await proc.wait()


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
) -> int:
    """Hold a lease on ``task_id`` while running ``command``, serialising it across agents.

    The hub grants only one live lease per task id, so wrapping a commit in
    ``synapse lock <project>:git -- git push`` lets several agents on one repo take
    turns instead of clobbering each other. A lock with no explicit ``paths`` is a
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
        The command to run while the lease is held.
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

    Returns
    -------
    int
        The command's exit code, or ``1`` when the hub was unreachable or the lease
        could not be acquired within ``wait_timeout``.
    """
    outcome: dict[str, Any] = {}

    async def collect(data: dict[str, Any]) -> None:
        if data.get("task_id") != task_id:
            return
        if data.get("type") == MessageType.CLAIM_GRANTED and data.get("owner") == name:
            outcome["granted"] = True
        elif data.get("type") == MessageType.CLAIM_DENIED:
            outcome["denied"] = str(data.get("payload") or "held by another agent")

    agent = agent_factory(name, collect, uri=uri, verbose=False, token=token)
    conn_task = asyncio.create_task(agent.connect())
    try:
        if not await agent.wait_until_ready(timeout=ready_timeout):
            print(
                describe_connect_failure(
                    name,
                    uri,
                    close_code=agent.last_close_code,
                    close_reason=agent.last_close_reason,
                )
            )
            return 1
        loop = asyncio.get_event_loop()
        deadline = loop.time() + wait_timeout
        # A keyless lock (no explicit --paths) is a pure named mutex: scope its
        # claim to its own task-id namespace so two different locks never contend
        # for the hub's shared default worktree (a `<repo>:git` push-lock must not
        # block an unrelated repo's lock or claim). With explicit paths the caller
        # wants real file-scope overlap, so the claim stays in the shared tree
        # where declared paths are compared.
        lock_worktree = "" if paths else task_id
        while True:
            outcome.clear()
            await agent.claim(task_id, worktree=lock_worktree, paths=paths)
            for _ in range(attempts):
                if outcome or conn_task.done():
                    break
                await asyncio.sleep(poll_interval)
            if outcome.get("granted"):
                break
            if conn_task.done() and not outcome:
                print(
                    explain_silent_outcome(
                        name,
                        uri,
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
                        uri,
                        close_code=agent.last_close_code,
                        close_reason=agent.last_close_reason,
                        fallback=(
                            f"Could not acquire lock '{task_id}': "
                            f"{outcome.get('denied', 'timed out')}"
                        ),
                    )
                )
                return 1
            await asyncio.sleep(retry_interval)
        return await runner(command)
    finally:
        with contextlib.suppress(Exception):
            await agent.release(task_id)
        agent.running = False
        conn_task.cancel()


def _cmd_lock(args: argparse.Namespace) -> int:
    """Dispatch the ``lock`` subcommand."""
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
        )
    )


async def _release(
    *,
    uri: str,
    name: str,
    task_id: str,
    evidence: list[str] | None = None,
    artifacts: list[str] | None = None,
    known_failures: list[str] | None = None,
    changed_files: list[str] | None = None,
    generated_artifacts: list[str] | None = None,
    approvals: list[str] | None = None,
    confidence: str = "",
    freshness_seconds: float | None = None,
    receipt: str | Path | None = None,
    receipt_json: bool = False,
    agent_factory: AgentFactory = SynapseAgent,
    token: str | None = None,
    ready_timeout: float = 5.0,
    attempts: int = 40,
    poll_interval: float = 0.05,
) -> int:
    """Drop a claim the caller owns, printing the hub's verdict and receipt.

    The manual escape hatch for a claim that no automatic trigger will release —
    a ``git-claim --auto-release-on manual``, or any lease whose holder simply
    wants to let go. The hub only honours a release from the claim's owner, so
    ``--name`` must match the owner recorded on the claim. Optional receipt
    fields travel through the real release envelope and are echoed by the hub;
    ``receipt_json`` prints that echo as machine-readable JSON.

    Parameters
    ----------
    uri, name : str
        Hub URI and the releasing identity; must equal the claim's owner.
    task_id : str
        Identifier of the claim to release.
    evidence, artifacts, known_failures, changed_files : list[str] or None, optional
        Repeated closeout evidence fields attached to the release receipt.
    generated_artifacts, approvals : list[str] or None, optional
        Additional repeated artifact/review fields attached to the receipt.
    confidence : str, optional
        Optional caller-supplied confidence label.
    freshness_seconds : float or None, optional
        Age, in seconds, of the newest evidence.
    receipt : str or pathlib.Path or None, optional
        Verified release receipt JSON to seed the repeated release receipt fields.
    receipt_json : bool, optional
        Print the release receipt as JSON instead of the legacy one-line text.
    agent_factory : AgentFactory, optional
        Factory for the hub client; injectable for testing.
    token : str or None, optional
        Shared-secret token for a secured hub.
    ready_timeout : float, optional
        Seconds to wait for the hub connection readiness event.
    attempts : int, optional
        Release verdict polling attempts.
    poll_interval : float, optional
        Seconds to wait between verdict polls.

    Returns
    -------
    int
        ``0`` when the hub confirms the release; ``1`` when the hub is unreachable,
        denies the release (not the owner, or no such claim), or stays silent.
    """
    try:
        receipt_payload = _load_release_receipt(receipt) if receipt is not None else {}
        _validate_release_receipt_identity(receipt_payload, task_id=task_id, name=name)
        release_evidence = _receipt_list(receipt_payload, "evidence", evidence)
        release_artifacts = _receipt_list(receipt_payload, "artifacts", artifacts)
        release_known_failures = _receipt_list(
            receipt_payload,
            "known_failures",
            known_failures,
        )
        release_changed_files = _receipt_list(receipt_payload, "changed_files", changed_files)
        release_generated_artifacts = _receipt_list(
            receipt_payload,
            "generated_artifacts",
            generated_artifacts,
        )
        release_approvals = _receipt_list(receipt_payload, "approvals", approvals)
        release_confidence = confidence or str(receipt_payload.get("confidence") or "")
        release_freshness_seconds = _receipt_freshness(receipt_payload, freshness_seconds)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"invalid release receipt for '{task_id}': {exc}")
        return 1

    outcome: dict[str, Any] = {}

    async def collect(data: dict[str, Any]) -> None:
        if str(data.get("task_id")) != task_id:
            return
        if data.get("type") == MessageType.RELEASE_GRANTED and data.get("owner") == name:
            outcome["released"] = True
            if isinstance(data.get("receipt"), dict):
                outcome["receipt"] = data["receipt"]
        elif data.get("type") == MessageType.RELEASE_DENIED:
            outcome["denied"] = str(data.get("payload") or "release denied")

    agent = agent_factory(name, collect, uri=uri, verbose=False, token=token)
    conn_task = asyncio.create_task(agent.connect())
    try:
        if not await agent.wait_until_ready(timeout=ready_timeout):
            print(
                describe_connect_failure(
                    name,
                    uri,
                    close_code=agent.last_close_code,
                    close_reason=agent.last_close_reason,
                )
            )
            return 1
        await agent.release(
            task_id,
            evidence=release_evidence,
            artifacts=release_artifacts,
            known_failures=release_known_failures,
            changed_files=release_changed_files,
            generated_artifacts=release_generated_artifacts,
            approvals=release_approvals,
            confidence=release_confidence,
            freshness_seconds=release_freshness_seconds,
        )
        for _ in range(attempts):
            if outcome or conn_task.done():
                break
            await asyncio.sleep(poll_interval)
        if outcome.get("released"):
            if receipt_json:
                receipt_output = outcome.get("receipt")
                if not isinstance(receipt_output, dict):
                    receipt_output = build_release_receipt(task_id=task_id, owner=name)
                print(json.dumps(receipt_output, sort_keys=True))
            else:
                print(f"released '{task_id}'")
            return 0
        denied = outcome.get("denied")
        if denied:
            print(f"release refused for '{task_id}': {denied}")
        else:
            print(
                explain_silent_outcome(
                    name,
                    uri,
                    close_code=agent.last_close_code,
                    close_reason=agent.last_close_reason,
                    fallback=f"release refused for '{task_id}': no response from hub",
                )
            )
        return 1
    finally:
        agent.running = False
        conn_task.cancel()


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
        )
    )


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``lock`` and ``release`` subparsers on the top-level CLI."""
    lock = subparsers.add_parser(
        "lock", help="Hold a lease while running a command (serialise e.g. commits)."
    )
    lock.add_argument("task_id")
    lock.add_argument(
        "command", nargs="+", help="The command to run while holding the lease (after --)."
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
    lock.add_argument("--uri", default=DEFAULT_HUB_URI)
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
    release.add_argument("--uri", default=DEFAULT_HUB_URI)
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
    release.set_defaults(func=_cmd_release)
