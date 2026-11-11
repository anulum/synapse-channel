# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — `synapse verdict`: declare evidence, record verdicts and corrections
"""Record evidence, verdicts and corrections on a hub, and report their state offline.

``synapse verdict evidence|record|correct`` post one canonical record as a
progress note and wait for the hub's answer: the record is either appended
(exit 0) or refused with the hub's reason (exit 1). ``synapse verdict status``
reads a hub event store directly and prints every verdict's state, the gate per
subject, stale notices, refused records, gate bypasses and approvals granted
before their subject turned stale.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import sys
from pathlib import Path
from typing import Any

from synapse_channel.client.agent import SynapseAgent, default_hub_uri
from synapse_channel.connect_failures import closed_after_ready, describe_connect_failure
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.verdict_hub import VerdictHubPolicy, parse_verdict_authority
from synapse_channel.core.verdict_records import (
    CORRECTION_NOTE_KIND,
    EVIDENCE_NOTE_KIND,
    VERDICT_NOTE_KIND,
    CorrectionRecord,
    EvidenceRecord,
    VerdictRecord,
    VerdictRecordError,
    format_correction,
    format_evidence,
    format_verdict,
    validate_record_body,
)
from synapse_channel.core.verdict_report import (
    VERDICT_REPORT_EVENT_KINDS,
    build_verdict_report,
    render_verdict_report,
)
from synapse_channel.waiter_identity import waiter_owner


async def post_record(
    *,
    uri: str,
    name: str,
    task_id: str,
    kind: str,
    text: str,
    token: str | None,
    ready_timeout: float,
    answer_timeout: float,
) -> int:
    """Post one record and wait for the hub to append or refuse it."""
    sender = waiter_owner(name)
    answer: asyncio.Future[tuple[bool, str]] = asyncio.get_running_loop().create_future()

    async def collect(data: dict[str, Any]) -> None:
        outcome: tuple[bool, str] | None = None
        kind_seen = data.get("type")
        if kind_seen == "ledger_progress_posted" and data.get("note", {}).get("text") == text:
            outcome = (True, "recorded")
        elif kind_seen == "error":
            outcome = (False, str(data.get("payload") or "refused"))
        if outcome is not None and not answer.done():
            answer.set_result(outcome)

    agent = SynapseAgent(sender, collect, uri=uri, verbose=False, token=token)
    conn_task = asyncio.create_task(agent.connect())
    try:
        if not await agent.wait_until_ready(timeout=ready_timeout) or await closed_after_ready(
            agent, timeout=ready_timeout
        ):
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
        await agent.post_progress(task_id, text, kind=kind)
        try:
            accepted, detail = await asyncio.wait_for(answer, timeout=answer_timeout)
        except TimeoutError:
            print(f"no answer from the hub within {answer_timeout:g} s", file=sys.stderr)
            return 1
        print(detail, file=sys.stdout if accepted else sys.stderr)
        return 0 if accepted else 1
    finally:
        agent.running = False
        conn_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await conn_task


def _members(path: str) -> tuple[str, ...]:
    if not path:
        return ()
    lines = Path(path).read_text(encoding="utf-8").split()
    return tuple(sorted(set(lines)))


def _body(args: argparse.Namespace) -> tuple[str, str]:
    """Build the record body for the chosen subcommand."""
    if args.verdict_command == "evidence":
        record = EvidenceRecord(args.sha256, args.namespace, _members(args.members_file))
        return EVIDENCE_NOTE_KIND, format_evidence(record)
    if args.verdict_command == "record":
        verdict = VerdictRecord(
            namespace=args.namespace,
            subject_sha256=args.subject,
            outcome=args.outcome,
            inputs=tuple(sorted(set(args.input))),
            acknowledged_corrections=tuple(sorted(set(args.acknowledge))),
            author=args.author,
            reviewer_sequence=args.sequence,
        )
        return VERDICT_NOTE_KIND, format_verdict(verdict)
    correction = CorrectionRecord(args.namespace, args.corrects, args.replacement, args.reason)
    return CORRECTION_NOTE_KIND, format_correction(correction)


def _cmd_post(args: argparse.Namespace) -> int:
    """Validate locally, then post the record to the hub."""
    kind, text = _body(args)
    try:
        validate_record_body(kind, text)
    except VerdictRecordError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return asyncio.run(
        post_record(
            uri=args.uri,
            name=args.name,
            task_id=args.task,
            kind=kind,
            text=text,
            token=args.token,
            ready_timeout=args.ready_timeout,
            answer_timeout=args.answer_timeout,
        )
    )


def _cmd_status(args: argparse.Namespace) -> int:
    """Print the verdict report read from a hub event store."""
    path = Path(args.db)
    if not path.exists():
        print(f"missing event store: {path}", file=sys.stderr)
        return 2
    policy = verdict_policy_from_args(args)
    store = EventStore(path, key_file=args.db_key_file)
    try:
        events = tuple(store.iter_events(kinds=VERDICT_REPORT_EVENT_KINDS))
    finally:
        store.close()
    report = build_verdict_report(events, policy, subject=args.subject or None)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_verdict_report(report))
    return 0


def _authority_entry(value: str) -> str:
    """Accept one ``NAMESPACE=SEAT`` entry or fail argument parsing."""
    try:
        parse_verdict_authority([value])
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    return value


def verdict_policy_from_args(args: argparse.Namespace) -> VerdictHubPolicy:
    """Build the verdict policy from the shared hub and status options."""
    return VerdictHubPolicy(
        verdict_authority=parse_verdict_authority(args.verdict_authority),
        gate_enabled=args.verdict_gate == "on",
        allow_unrecorded_inputs=args.allow_unrecorded_verdict_inputs,
    )


def add_policy_arguments(parser: argparse.ArgumentParser) -> None:
    """Add the verdict-authority and gate options shared by hub and status."""
    parser.add_argument(
        "--verdict-authority",
        action="append",
        default=[],
        type=_authority_entry,
        metavar="NAMESPACE=SEAT",
        help="Seat allowed to declare and correct evidence in NAMESPACE beyond its own "
        "project seats (repeatable).",
    )
    parser.add_argument(
        "--verdict-gate",
        choices=("on", "off"),
        default="on",
        help="Refuse approving a SHA-256 subject without a fresh accepted verdict from "
        "every reviewer (default on). With off, each such approval is journalled as a "
        "gate bypass.",
    )
    parser.add_argument(
        "--allow-unrecorded-verdict-inputs",
        action="store_true",
        help="Let an accepted verdict that cites no input pass the gate.",
    )


def _add_post_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--uri", default=default_hub_uri(), help="Hub URI.")
    parser.add_argument("--name", required=True, help="Acting seat identity.")
    parser.add_argument("--task", required=True, help="Task the record belongs to.")
    parser.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    parser.add_argument("--ready-timeout", type=float, default=5.0, help="Seconds to connect.")
    parser.add_argument(
        "--answer-timeout", type=float, default=10.0, help="Seconds to await the hub's answer."
    )
    parser.set_defaults(func=_cmd_post)


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``verdict`` command group."""
    parser = subparsers.add_parser(
        "verdict",
        help="Declare evidence, record verdicts and corrections, and report stale verdicts.",
    )
    group = parser.add_subparsers(dest="verdict_command", required=True)

    evidence = group.add_parser("evidence", help="Declare one recorded object by SHA-256.")
    evidence.add_argument("--sha256", required=True, help="SHA-256 of the object.")
    evidence.add_argument("--namespace", required=True, help="Project namespace it belongs to.")
    evidence.add_argument(
        "--members-file", default="", help="File of member SHA-256 lines for a manifest."
    )
    _add_post_arguments(evidence)

    record = group.add_parser("record", help="Record a verdict and the inputs it read.")
    record.add_argument("--namespace", required=True, help="Project namespace of the subject.")
    record.add_argument("--subject", required=True, help="SHA-256 of the reviewed object.")
    record.add_argument(
        "--outcome", required=True, choices=("accepted", "rejected", "changes-required")
    )
    record.add_argument("--input", action="append", default=[], help="Input SHA-256 read.")
    record.add_argument(
        "--acknowledge", action="append", default=[], help="Correction id already considered."
    )
    record.add_argument("--author", required=True, help="Seat that produced the subject.")
    record.add_argument(
        "--sequence", type=int, required=True, help="This reviewer's verdict number on it."
    )
    _add_post_arguments(record)

    correct = group.add_parser("correct", help="Declare that one object replaces another.")
    correct.add_argument("--namespace", required=True, help="Namespace of the object.")
    correct.add_argument("--corrects", required=True, help="SHA-256 of the superseded object.")
    correct.add_argument("--replacement", required=True, help="SHA-256 of its replacement.")
    correct.add_argument("--reason", required=True, help="Why the object was corrected.")
    _add_post_arguments(correct)

    status = group.add_parser("status", help="Report verdict state from a hub event store.")
    status.add_argument("db", help="Path to the hub event store.")
    status.add_argument("--db-key-file", default=None, help="SQLCipher key for the store.")
    status.add_argument("--subject", default="", help="Show only this subject SHA-256.")
    status.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    add_policy_arguments(status)
    status.set_defaults(func=_cmd_status)
