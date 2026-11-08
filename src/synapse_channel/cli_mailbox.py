# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — mailbox views read from a hub journal
"""``synapse mailbox orphans``: directed mail waiting for names that went quiet.

The ``orphans`` action reads a hub's event store read-only and changes nothing;
``handover`` and ``close`` (:mod:`synapse_channel.cli_mailbox_handover`) act
through the live hub, and ``drain`` (:mod:`synapse_channel.cli_mailbox_drain`)
applies one of them to every orphan of a project. A name is an orphan when it
has pending directed mail and no journalled activity for the chosen number of
days. Names given with ``--active``, and with ``--live`` every name the hub's
roster shows connected now, are never orphans.
"""

from __future__ import annotations

import argparse
import json
import time
from collections.abc import Callable

from synapse_channel.cli_doctor_mailbox import fetch_doctor_roster
from synapse_channel.cli_mailbox_drain import add_drain_parser
from synapse_channel.cli_mailbox_handover import add_handover_parsers
from synapse_channel.cli_mailbox_query import (
    LIVE_PROBE_NAME,
    RosterProbe,
    add_query_arguments,
    find_orphans,
)
from synapse_channel.core.mailbox_orphans import MailboxStanding
from synapse_channel.terminal_text import terminal_text

DEFAULT_LIMIT = 20
"""Orphans shown in text output unless ``--all`` is given."""

_DAY = 86_400.0


def _day(moment: float | None) -> str:
    """Format an optional instant as a UTC date."""
    if moment is None:
        return "never"
    return time.strftime("%Y-%m-%d", time.gmtime(moment))


def orphan_row(standing: MailboxStanding, now: float) -> dict[str, object]:
    """Return the JSON row of one orphan."""
    quiet = standing.dormant_for(now)
    return {
        "identity": standing.identity,
        "project": standing.project,
        "pending": standing.pending,
        "watermark": standing.watermark,
        "last_activity": _day(standing.last_activity_ts),
        "quiet_days": None if quiet is None else round(quiet / _DAY, 1),
        "oldest_pending": _day(standing.oldest_pending_ts),
    }


def orphan_line(standing: MailboxStanding) -> str:
    """Return the text line of one orphan."""
    return (
        f"{standing.pending:7d}  {terminal_text(standing.identity)}  "
        f"last active {_day(standing.last_activity_ts)}  "
        f"oldest pending {_day(standing.oldest_pending_ts)}"
    )


def _cmd_orphans(
    args: argparse.Namespace,
    *,
    clock: Callable[[], float] = time.time,
    write: Callable[[str], None] = print,
    roster_probe: RosterProbe = fetch_doctor_roster,
) -> int:
    """List orphaned mailboxes, most pending mail first."""
    now = clock()
    found = find_orphans(args, now=now, command="orphans", roster_probe=roster_probe)
    if found is None:
        return 1
    orphans, live = found
    total = sum(item.pending for item in orphans)
    if args.json:
        document = {
            "dormant_after_days": args.dormant_after_days,
            "project": args.project,
            "live_roster": None if live is None else len(live),
            "orphans": [orphan_row(item, now) for item in orphans],
            "orphaned_names": len(orphans),
            "orphaned_messages": total,
            "roles": "not recorded in the journal",
        }
        write(json.dumps(document, indent=2))
        return 0
    if not orphans:
        write(f"no orphaned mailboxes (quiet for {args.dormant_after_days:g} days or more)")
        return 0
    shown = orphans if args.all else orphans[:DEFAULT_LIMIT]
    for item in shown:
        write(orphan_line(item))
    hidden = len(orphans) - len(shown)
    suffix = f"; {hidden} more with --all" if hidden else ""
    write(
        f"{total} pending messages in {len(orphans)} orphaned mailboxes "
        f"(quiet for {args.dormant_after_days:g} days or more){suffix}"
    )
    return 0


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``synapse mailbox`` and its actions."""
    mailbox = subparsers.add_parser(
        "mailbox",
        help="Find orphaned mailboxes in a hub store; hand them over or close them via the hub.",
    )
    actions = mailbox.add_subparsers(dest="mailbox_command", required=True)
    orphans = actions.add_parser(
        "orphans", help="List pending mail for names with no activity for a number of days."
    )
    add_query_arguments(
        orphans,
        name_default=LIVE_PROBE_NAME,
        name_help="Name of the --live roster query connection.",
    )
    orphans.add_argument("--project", help="Only names of this project.")
    orphans.add_argument("--all", action="store_true", help="Show every orphan.")
    orphans.set_defaults(func=_cmd_orphans)
    add_handover_parsers(actions)
    add_drain_parser(actions)
