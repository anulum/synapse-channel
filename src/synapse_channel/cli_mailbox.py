# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — mailbox views read from a hub journal
"""``synapse mailbox orphans``: directed mail waiting for names that went quiet.

The command reads a hub's event store read-only and changes nothing. A name is
an orphan when it has pending directed mail and no journalled activity for the
chosen number of days. Names given with ``--active`` are never orphans.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable
from pathlib import Path

from synapse_channel.core.event_row_mac import RowMacError, load_row_mac_key, row_mac_key_path
from synapse_channel.core.mailbox_orphans import (
    MailboxStanding,
    mailbox_standings,
    orphaned_mailboxes,
)
from synapse_channel.core.record_keeping_source import iter_record_source
from synapse_channel.terminal_text import terminal_text

DEFAULT_DORMANT_DAYS = 7.0
"""Days without activity after which a name with pending mail is an orphan."""

DEFAULT_LIMIT = 20
"""Orphans shown in text output unless ``--all`` is given."""

_MAX_EVENTS = 50_000_000
_MAX_PAYLOAD_BYTES = 64 * 1024**3
_DAY = 86_400.0


def _positive_days(text: str) -> float:
    """Parse a positive, finite number of days."""
    try:
        value = float(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{text!r} is not a number of days") from exc
    if not value > 0 or value == float("inf"):
        raise argparse.ArgumentTypeError("the number of days must be positive and finite")
    return value


def _day(moment: float | None) -> str:
    """Format an optional instant as a UTC date."""
    if moment is None:
        return "never"
    return time.strftime("%Y-%m-%d", time.gmtime(moment))


def _row(standing: MailboxStanding, now: float) -> dict[str, object]:
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


def _read_standings(args: argparse.Namespace) -> list[MailboxStanding]:
    """Read the journal snapshot read-only and compute every mailbox standing."""
    db = Path(args.db).expanduser()
    if args.row_mac_key:
        row_key = load_row_mac_key(Path(args.row_mac_key).expanduser())
    elif row_mac_key_path(db).is_file():
        row_key = load_row_mac_key(row_mac_key_path(db))
    else:
        row_key = None
    rows = iter_record_source(
        db,
        key_file=args.db_key_file,
        row_mac=row_key,
        max_events=_MAX_EVENTS,
        max_payload_bytes=_MAX_PAYLOAD_BYTES,
    )
    try:
        return mailbox_standings(row.event for row in rows)
    finally:
        rows.close()


def _cmd_orphans(
    args: argparse.Namespace,
    *,
    clock: Callable[[], float] = time.time,
    write: Callable[[str], None] = print,
) -> int:
    """List orphaned mailboxes, most pending mail first."""
    try:
        standings = _read_standings(args)
    except (ValueError, OSError, RuntimeError, RowMacError) as exc:
        print(f"synapse mailbox orphans: {exc}", file=sys.stderr)
        return 1
    now = clock()
    orphans = orphaned_mailboxes(
        standings,
        now=now,
        dormant_after_days=args.dormant_after_days,
        active=args.active or (),
        project=args.project,
    )
    total = sum(item.pending for item in orphans)
    if args.json:
        document = {
            "dormant_after_days": args.dormant_after_days,
            "project": args.project,
            "orphans": [_row(item, now) for item in orphans],
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
        write(
            f"{item.pending:7d}  {terminal_text(item.identity)}  "
            f"last active {_day(item.last_activity_ts)}  "
            f"oldest pending {_day(item.oldest_pending_ts)}"
        )
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
        "mailbox", help="Inspect mailboxes from a hub's event store (read-only)."
    )
    actions = mailbox.add_subparsers(dest="mailbox_command", required=True)
    orphans = actions.add_parser(
        "orphans", help="List pending mail for names with no activity for a number of days."
    )
    orphans.add_argument("db", help="Path to the hub event store (the hub's --db).")
    orphans.add_argument(
        "--dormant-after-days",
        type=_positive_days,
        default=DEFAULT_DORMANT_DAYS,
        help=f"Days without activity before a name counts (default {DEFAULT_DORMANT_DAYS:g}).",
    )
    orphans.add_argument("--project", help="Only names of this project.")
    orphans.add_argument(
        "--active",
        action="append",
        metavar="NAME",
        help="A name known to be connected now; never listed. Repeatable.",
    )
    orphans.add_argument("--all", action="store_true", help="Show every orphan.")
    orphans.add_argument("--json", action="store_true", help="Print a JSON document.")
    orphans.add_argument(
        "--db-key-file", default=None, help="Owner-only SQLCipher key for an encrypted store."
    )
    orphans.add_argument(
        "--row-mac-key",
        default=None,
        help="The store's row-authentication key (default: <db>.rowmac.key when present).",
    )
    orphans.set_defaults(func=_cmd_orphans)
