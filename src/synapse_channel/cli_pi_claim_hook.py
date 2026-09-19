# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — native pi tool claim hook CLI
"""Emit a single bounded, fail-closed verdict for a pi tool_call event."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

from synapse_channel.claim_state import fetch_state_snapshot
from synapse_channel.cli_claim_hook_common import (
    add_claim_hook_arguments,
    normalise_ready_timeout,
)
from synapse_channel.file_claim_guard import GuardVerdict
from synapse_channel.pi_claim_guard import (
    MAX_PI_HOOK_BYTES,
    PiGuardContext,
    claim_epoch_from_snapshot,
    evaluate_pi_hook,
)


def _cmd_pi_claim_hook(args: argparse.Namespace) -> int:
    """Consume one event and print only an allow/deny JSON object."""
    try:
        encoded = sys.stdin.buffer.read(MAX_PI_HOOK_BYTES + 1)
        if len(encoded) > MAX_PI_HOOK_BYTES:
            raise ValueError("pi hook event exceeds its byte limit")
        context = PiGuardContext(
            identity=str(args.identity),
            project=str(args.project),
            repository=Path(args.repository),
            task_id=str(args.task_id),
            epoch=int(args.epoch),
            session_id=str(args.session_id),
        )
        verdict = asyncio.run(
            evaluate_pi_hook(
                encoded.decode("utf-8", errors="strict"),
                context=context,
                uri=str(args.uri),
                token=args.token,
                timeout=normalise_ready_timeout(float(args.ready_timeout)),
            )
        )
    except Exception:
        verdict = GuardVerdict(False, "Synapse pi claim verification failed closed")
    payload: dict[str, object] = {"allowed": verdict.allowed}
    if not verdict.allowed:
        payload["reason"] = verdict.reason
    print(json.dumps(payload, ensure_ascii=False))
    return 0


def add_pi_claim_hook_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Register the bounded native pi tool-call claim guard."""
    parser = subparsers.add_parser("pi-claim-hook", help="Guard pi write/edit tool calls.")
    add_claim_hook_arguments(parser)
    parser.add_argument("--project", required=True, help="Exact Synapse project.")
    parser.add_argument("--repository", required=True, help="Canonical Git worktree root.")
    parser.add_argument("--task-id", required=True, help="Claim task ID bound at launch.")
    parser.add_argument("--epoch", required=True, type=int, help="Claim epoch bound at launch.")
    parser.add_argument("--session-id", required=True, help="Exact pi session ID.")
    parser.set_defaults(func=_cmd_pi_claim_hook)


def _cmd_pi_claim_status(args: argparse.Namespace) -> int:
    """Report the current exact-task epoch needed to launch guarded pi."""
    try:
        snapshot = asyncio.run(
            fetch_state_snapshot(
                uri=str(args.uri),
                requester=f"{args.project}/pi-status-{uuid.uuid4().hex[:10]}",
                token=args.token,
                timeout=normalise_ready_timeout(float(args.ready_timeout)),
            )
        )
        epoch = claim_epoch_from_snapshot(
            snapshot,
            identity=str(args.identity),
            project=str(args.project),
            repository=Path(args.repository),
            task_id=str(args.task_id),
        )
    except Exception:
        epoch = None
    print(json.dumps({"eligible": epoch is not None, "epoch": epoch}))
    return 0 if epoch is not None else 1


def add_pi_claim_status_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Register a read-only claim epoch lookup for the pi launch recipe."""
    parser = subparsers.add_parser("pi-claim-status", help="Show an exact pi claim epoch.")
    add_claim_hook_arguments(parser)
    parser.add_argument("--project", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--task-id", required=True)
    parser.set_defaults(func=_cmd_pi_claim_status)
