# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — human-in-the-loop approval gate CLI commands
"""CLI wrappers for human-in-the-loop approval gates.

``synapse approval request`` and ``synapse approval decide`` post approval
workflow notes onto the shared progress ledger; ``synapse approval status`` reads
a hub SQLite event store back and prints the replayed decision state per subject.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import sys
from typing import Any

from synapse_channel.client.agent import SynapseAgent, default_hub_uri
from synapse_channel.connect_failures import closed_after_ready, describe_connect_failure
from synapse_channel.core.approvals import (
    APPROVAL_NOTE_KIND,
    STATE_APPROVED,
    STATE_REJECTED,
    STATE_REQUESTED,
    ApprovalReport,
    approvals_to_json,
    format_approval_note,
    render_human,
    run_approval_report,
)
from synapse_channel.waiter_identity import waiter_owner


async def _emit_approval(
    *,
    uri: str,
    name: str,
    subject: str,
    state: str,
    reason: str,
    token: str | None,
    ready_timeout: float,
) -> int:
    """Connect to the hub and post one approval workflow note."""
    sender = waiter_owner(name)

    async def collect(_data: dict[str, Any]) -> None:
        return None

    agent = SynapseAgent(sender, collect, uri=uri, verbose=False, token=token)
    conn_task = asyncio.create_task(agent.connect())
    try:
        if not await agent.wait_until_ready(timeout=ready_timeout):
            print(
                describe_connect_failure(
                    sender,
                    uri,
                    close_code=agent.last_close_code,
                    close_reason=agent.last_close_reason,
                ),
                file=sys.stderr,
            )
            return 1
        if await closed_after_ready(agent):
            print(
                describe_connect_failure(
                    sender,
                    uri,
                    close_code=agent.last_close_code,
                    close_reason=agent.last_close_reason,
                ),
                file=sys.stderr,
            )
            return 1
        note = format_approval_note(subject=subject, state=state, reason=reason)
        await agent.post_progress(subject, note, kind=APPROVAL_NOTE_KIND)
        return 0
    finally:
        agent.running = False
        conn_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await conn_task


def _emit(args: argparse.Namespace, *, state: str, reason: str) -> int:
    """Validate and post one approval note, returning a process exit code."""
    try:
        format_approval_note(subject=args.subject, state=state, reason=reason)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return asyncio.run(
        _emit_approval(
            uri=args.uri,
            name=args.name,
            subject=args.subject,
            state=state,
            reason=reason,
            token=args.token,
            ready_timeout=args.ready_timeout,
        )
    )


def _cmd_request(args: argparse.Namespace) -> int:
    """Post an approval request (``awaiting_approval``)."""
    return _emit(args, state=STATE_REQUESTED, reason=args.reason)


def _cmd_decide(args: argparse.Namespace) -> int:
    """Post an approve or reject decision."""
    state = STATE_APPROVED if args.approve else STATE_REJECTED
    return _emit(args, state=state, reason=args.reason)


def _cmd_status(args: argparse.Namespace) -> int:
    """Print replayed approval state for one or all subjects."""
    try:
        report = run_approval_report(
            args.db, key_file=getattr(args, "db_key_file", None)
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    statuses = report.pending if args.pending else report.statuses
    if args.subject:
        statuses = tuple(status for status in statuses if status.subject == args.subject)
    filtered = ApprovalReport(
        generated_from_seq=report.generated_from_seq,
        as_of=report.as_of,
        statuses=statuses,
    )
    if args.json:
        print(json.dumps(approvals_to_json(filtered), indent=2, sort_keys=True))
    elif not statuses:
        print("No matching approval subjects.")
    else:
        print(render_human(filtered))
    return 0


def _add_emit_arguments(parser: argparse.ArgumentParser) -> None:
    """Add the shared hub-connection and identity arguments to an emit parser."""
    parser.add_argument("--uri", default=default_hub_uri(), help="Hub URI.")
    parser.add_argument("--name", required=True, help="Acting agent identity.")
    parser.add_argument("--subject", required=True, help="Gated subject id (task or release/gate).")
    parser.add_argument("--reason", default="", help="Optional free-text reason.")
    parser.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    parser.add_argument(
        "--ready-timeout", type=float, default=5.0, help="Seconds to await hub readiness."
    )


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``approval`` command group."""
    parser = subparsers.add_parser(
        "approval",
        help="Request, decide, and report human-in-the-loop approval gates.",
    )
    group = parser.add_subparsers(dest="approval_command", required=True)

    request = group.add_parser("request", help="Post an approval request (awaiting_approval).")
    _add_emit_arguments(request)
    request.set_defaults(func=_cmd_request)

    decide = group.add_parser("decide", help="Approve or reject a requested subject.")
    _add_emit_arguments(decide)
    verdict = decide.add_mutually_exclusive_group(required=True)
    verdict.add_argument("--approve", action="store_true", help="Record an approval.")
    verdict.add_argument("--reject", action="store_true", help="Record a rejection.")
    decide.set_defaults(func=_cmd_decide)

    status = group.add_parser(
        "status",
        help="Replay approval state from a hub SQLite event store, e.g. ~/synapse/hub.db.",
    )
    status.add_argument("db", help="Path to the hub event store.")
    status.add_argument(
        "--db-key-file",
        default=None,
        help="Owner-only SQLCipher key for an encrypted event store.",
    )
    status.add_argument("--subject", default="", help="Show only this subject.")
    status.add_argument("--pending", action="store_true", help="Show only awaiting subjects.")
    status.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    status.set_defaults(func=_cmd_status)
