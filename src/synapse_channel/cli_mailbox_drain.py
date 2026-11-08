# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — hand over or close every orphaned mailbox of one project
"""``synapse mailbox drain``: a custodian's drain of a project's orphaned mailboxes.

The orphans are found as ``synapse mailbox orphans`` finds them, for one
``--project``. Without ``--execute`` the command prints the plan and changes
nothing. With ``--execute`` it connects once as ``--name`` and, for each orphan,
sends a handover to ``--to`` or a close, repeating while the hub says more mail
remains. The hub still decides every request: only the name itself, its
custodian or an operator granted ``mailbox-handover`` may act, so a refusal is
reported for that mailbox and the drain goes on with the next one. Nothing is
deleted; every request is journalled with its reason.

Exit codes: 0 the plan was printed or every orphan was drained, 1 the store
could not be read or a request was refused, 2 the hub gave no verdict, 3 a
mailbox still had mail after the request limit.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass

from synapse_channel.cli_doctor_mailbox import fetch_doctor_roster
from synapse_channel.cli_mailbox_handover import (
    EXIT_APPLIED,
    EXIT_MORE_REMAINS,
    EXIT_NO_VERDICT,
    EXIT_REFUSED,
    HandoverConnection,
    handover_connection,
    render_verdict,
)
from synapse_channel.cli_mailbox_query import RosterProbe, add_query_arguments, find_orphans
from synapse_channel.cli_messaging_types import AgentFactory
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.mailbox_orphans import MailboxStanding
from synapse_channel.terminal_text import terminal_text

MAX_REQUESTS_PER_MAILBOX = 100
"""Requests one drain sends for a single mailbox before it reports mail remaining."""

_EXIT_ORDER = (EXIT_APPLIED, EXIT_MORE_REMAINS, EXIT_REFUSED, EXIT_NO_VERDICT)


@dataclass(frozen=True)
class DrainOutcome:
    """What a drain did to one orphaned mailbox.

    Attributes
    ----------
    identity : str
        The mailbox.
    pending : int
        Pending messages the store showed before the drain.
    status : str
        ``drained``, ``remaining`` (the request limit was reached), ``refused``
        or ``no_verdict``.
    reason : str
        The hub's refusal reason, or an empty string.
    detail : str
        The hub's last one-line summary.
    forwarded : int
        Messages handed to the successor (0 for a close).
    through_seq : int or None
        The last journal sequence the drain covered.
    requests : int
        Requests sent for this mailbox.
    """

    identity: str
    pending: int
    status: str
    reason: str = ""
    detail: str = ""
    forwarded: int = 0
    through_seq: int | None = None
    requests: int = 0


@dataclass
class _Progress:
    """What the requests for one mailbox have covered so far."""

    forwarded: int = 0
    through_seq: int | None = None
    detail: str = ""

    def outcome(
        self, standing: MailboxStanding, status: str, requests: int, *, reason: str = ""
    ) -> DrainOutcome:
        """Return the mailbox's outcome with this progress."""
        return DrainOutcome(
            identity=standing.identity,
            pending=standing.pending,
            status=status,
            reason=reason,
            detail=self.detail,
            forwarded=self.forwarded,
            through_seq=self.through_seq,
            requests=requests,
        )


async def drain_mailbox(
    connection: HandoverConnection,
    standing: MailboxStanding,
    *,
    successor: str | None,
    reason: str,
    timeout: float,
    max_requests: int = MAX_REQUESTS_PER_MAILBOX,
) -> DrainOutcome:
    """Hand over or close one mailbox, repeating while the hub says more remains."""
    fields: dict[str, object] = {"from": standing.identity, "reason": reason}
    if successor is not None:
        fields["to"] = successor
    progress = _Progress()
    limit = max(1, max_requests)
    for count in range(1, limit + 1):
        result = await connection.request(dict(fields), timeout=timeout)
        if result is None:
            return progress.outcome(standing, "no_verdict", count)
        verdict = render_verdict(result, dict(fields))
        progress.detail = str(verdict["detail"])
        if verdict["status"] == "refused":
            return progress.outcome(standing, "refused", count, reason=str(verdict["reason"]))
        progress.forwarded += int(verdict.get("forwarded") or 0)
        through = verdict.get("through_seq")
        if isinstance(through, int) and not isinstance(through, bool):
            progress.through_seq = through
        if not verdict.get("remaining"):
            return progress.outcome(standing, "drained", count)
    return progress.outcome(standing, "remaining", limit)


async def drain_mailboxes(
    orphans: Sequence[MailboxStanding],
    *,
    uri: str,
    actor: str,
    token: str | None,
    ready_timeout: float,
    successor: str | None,
    reason: str,
    timeout: float,
    agent_factory: AgentFactory = SynapseAgent,
) -> list[DrainOutcome] | None:
    """Drain each orphan over one connection; ``None`` when the hub could not be reached.

    A mailbox whose request got no verdict ends the drain, because the next
    request on the same connection would have no better chance.
    """
    outcomes: list[DrainOutcome] = []
    async with handover_connection(
        uri=uri, actor=actor, token=token, ready_timeout=ready_timeout, agent_factory=agent_factory
    ) as connection:
        if connection is None:
            return None
        for standing in orphans:
            outcome = await drain_mailbox(
                connection, standing, successor=successor, reason=reason, timeout=timeout
            )
            outcomes.append(outcome)
            if outcome.status == "no_verdict":
                break
    return outcomes


def _exit_code(outcomes: Sequence[DrainOutcome]) -> int:
    """Return the most serious exit code of a drain."""
    codes = {
        "drained": EXIT_APPLIED,
        "remaining": EXIT_MORE_REMAINS,
        "refused": EXIT_REFUSED,
        "no_verdict": EXIT_NO_VERDICT,
    }
    worst = EXIT_APPLIED
    for outcome in outcomes:
        code = codes[outcome.status]
        if _EXIT_ORDER.index(code) > _EXIT_ORDER.index(worst):
            worst = code
    return worst


def _action(args: argparse.Namespace) -> str:
    """Return the action the drain applies, in words."""
    return "close" if args.close else f"hand over to {args.to}"


def _plan_document(
    args: argparse.Namespace, orphans: Sequence[MailboxStanding]
) -> dict[str, object]:
    """Return the JSON document of a plan or a drain."""
    return {
        "project": args.project,
        "action": "close" if args.close else "handover",
        "successor": None if args.close else args.to,
        "dormant_after_days": args.dormant_after_days,
        "execute": bool(args.execute),
        "orphaned_names": len(orphans),
        "orphaned_messages": sum(item.pending for item in orphans),
    }


def _write_plan(
    args: argparse.Namespace, orphans: Sequence[MailboxStanding], write: Callable[[str], None]
) -> None:
    """Print what an ``--execute`` run would do."""
    if args.json:
        document = _plan_document(args, orphans)
        document["mailboxes"] = [
            {"identity": item.identity, "pending": item.pending} for item in orphans
        ]
        write(json.dumps(document, indent=2))
        return
    for item in orphans:
        write(f"{item.pending:7d}  {terminal_text(item.identity)}")
    write(
        f"would {terminal_text(_action(args))} {sum(item.pending for item in orphans)} pending "
        f"messages in {len(orphans)} orphaned mailboxes of project "
        f"{terminal_text(args.project)}; run again with --execute to apply"
    )


def _write_outcomes(
    args: argparse.Namespace,
    orphans: Sequence[MailboxStanding],
    outcomes: Sequence[DrainOutcome],
    write: Callable[[str], None],
) -> None:
    """Print what the drain did, one line per mailbox and a summary."""
    if args.json:
        document = _plan_document(args, orphans)
        document["mailboxes"] = [asdict(outcome) for outcome in outcomes]
        write(json.dumps(document, indent=2))
        return
    for outcome in outcomes:
        reason = f" ({outcome.reason})" if outcome.reason else ""
        write(
            f"{outcome.status}{reason}  {terminal_text(outcome.identity)}  "
            f"{terminal_text(outcome.detail)}"
        )
    drained = sum(1 for outcome in outcomes if outcome.status == "drained")
    write(
        f"drained {drained} of {len(orphans)} orphaned mailboxes ({terminal_text(_action(args))})"
    )


def _cmd_drain(
    args: argparse.Namespace,
    *,
    clock: Callable[[], float] = time.time,
    write: Callable[[str], None] = print,
    roster_probe: RosterProbe = fetch_doctor_roster,
    agent_factory: AgentFactory = SynapseAgent,
) -> int:
    """Print the drain plan, or apply it with ``--execute``."""
    found = find_orphans(args, now=clock(), command="drain", roster_probe=roster_probe)
    if found is None:
        return EXIT_REFUSED
    orphans = [item for item in found[0] if args.close or item.identity != args.to]
    if not orphans:
        write(f"no orphaned mailboxes in project {terminal_text(args.project)}")
        return EXIT_APPLIED
    if not args.execute:
        _write_plan(args, orphans, write)
        return EXIT_APPLIED
    outcomes = asyncio.run(
        drain_mailboxes(
            orphans,
            uri=args.uri,
            actor=args.name,
            token=args.token,
            ready_timeout=args.ready_timeout,
            successor=None if args.close else args.to,
            reason=args.reason,
            timeout=args.timeout,
            agent_factory=agent_factory,
        )
    )
    if outcomes is None:
        return EXIT_NO_VERDICT
    _write_outcomes(args, orphans, outcomes, write)
    return _exit_code(outcomes)


def add_drain_parser(actions: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``mailbox drain``."""
    drain = actions.add_parser(
        "drain",
        help="Hand over or close every orphaned mailbox of one project (a plan unless --execute).",
    )
    add_query_arguments(
        drain,
        name_default=None,
        name_help="Identity to act as: the custodian of the project's names or a granted operator.",
    )
    drain.add_argument("--project", required=True, help="The project whose orphans to drain.")
    drain.add_argument("--reason", required=True, help="Reason written to the journal.")
    target = drain.add_mutually_exclusive_group(required=True)
    target.add_argument("--to", help="Hand each orphan's mail to this successor (a name or role).")
    target.add_argument("--close", action="store_true", help="Close each orphan's mail instead.")
    drain.add_argument(
        "--execute", action="store_true", help="Apply the plan through the live hub."
    )
    drain.add_argument("--timeout", type=float, default=10.0, help="Seconds to await each verdict.")
    drain.set_defaults(func=_cmd_drain)
