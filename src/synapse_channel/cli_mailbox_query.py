# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — read a hub store and its live roster to find orphaned mailboxes
"""Find orphaned mailboxes for ``synapse mailbox orphans`` and ``synapse mailbox drain``.

The store is read read-only. A mailbox is an orphan when it has pending directed
mail and no journalled activity for the chosen number of days; names given with
``--active``, and with ``--live`` every name the hub's roster shows connected
now, are never orphans.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

from synapse_channel.cli_doctor_mailbox import DoctorRoster, fetch_doctor_roster
from synapse_channel.client.agent import SynapseAgent, default_hub_uri
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

LIVE_PROBE_NAME = "mailbox-orphans"
"""Default name of the one-shot connection that reads the hub's live roster."""

RosterProbe = Callable[..., Coroutine[Any, Any, DoctorRoster | None]]
"""Reads a hub's live roster; ``None`` when the hub gave no roster."""

_MAX_EVENTS = 50_000_000
_MAX_PAYLOAD_BYTES = 64 * 1024**3


def positive_days(text: str) -> float:
    """Parse a positive, finite number of days for ``--dormant-after-days``."""
    try:
        value = float(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{text!r} is not a number of days") from exc
    if not value > 0 or value == float("inf"):
        raise argparse.ArgumentTypeError("the number of days must be positive and finite")
    return value


def read_mailbox_standings(
    db: Path, *, key_file: str | Path | None = None, row_mac_key: str | Path | None = None
) -> list[MailboxStanding]:
    """Read a hub journal read-only and compute every mailbox standing.

    Parameters
    ----------
    db : pathlib.Path
        The hub's event store (its ``--db``).
    key_file : str or pathlib.Path or None, optional
        Owner-only SQLCipher key of an encrypted store.
    row_mac_key : str or pathlib.Path or None, optional
        The store's row-authentication key; ``<db>.rowmac.key`` is used when
        it exists and none is named.

    Returns
    -------
    list[MailboxStanding]
        One standing per name the journal knows.

    Raises
    ------
    ValueError, OSError, RuntimeError, RowMacError
        When the store or its keys cannot be read.
    """
    store = Path(db).expanduser()
    if row_mac_key:
        row_key = load_row_mac_key(Path(row_mac_key).expanduser())
    elif row_mac_key_path(store).is_file():
        row_key = load_row_mac_key(row_mac_key_path(store))
    else:
        row_key = None
    rows = iter_record_source(
        store,
        key_file=key_file,
        row_mac=row_key,
        max_events=_MAX_EVENTS,
        max_payload_bytes=_MAX_PAYLOAD_BYTES,
    )
    try:
        return mailbox_standings(row.event for row in rows)
    finally:
        rows.close()


def live_names(args: argparse.Namespace, probe: RosterProbe) -> tuple[str, ...] | None:
    """Return the names the hub shows connected now, without the probe itself."""
    roster = asyncio.run(
        probe(
            uri=args.uri,
            name=args.name,
            token=args.token,
            agent_factory=SynapseAgent,
            ready_timeout=args.ready_timeout,
        )
    )
    if roster is None:
        return None
    return tuple(name for name in roster.agents if name != args.name)


def find_orphans(
    args: argparse.Namespace,
    *,
    now: float,
    command: str,
    roster_probe: RosterProbe = fetch_doctor_roster,
) -> tuple[list[MailboxStanding], tuple[str, ...] | None] | None:
    """Return the orphans the parsed arguments select, with the live roster read.

    Parameters
    ----------
    args : argparse.Namespace
        Arguments registered by :func:`add_query_arguments`.
    now : float
        The reference wall-clock time.
    command : str
        The command name used in error messages.
    roster_probe : RosterProbe, optional
        Reads the hub's live roster for ``--live``.

    Returns
    -------
    tuple or None
        The orphans (most pending mail first) and the live roster (``None``
        without ``--live``), or ``None`` after printing why the store or the
        roster could not be read.
    """
    try:
        standings = read_mailbox_standings(
            Path(args.db), key_file=args.db_key_file, row_mac_key=args.row_mac_key
        )
    except (ValueError, OSError, RuntimeError, RowMacError) as exc:
        print(f"synapse mailbox {command}: {exc}", file=sys.stderr)
        return None
    live: tuple[str, ...] | None = None
    if args.live:
        live = live_names(args, roster_probe)
        if live is None:
            print(
                f"synapse mailbox {command}: no live roster from {terminal_text(args.uri)}",
                file=sys.stderr,
            )
            return None
    orphans = orphaned_mailboxes(
        standings,
        now=now,
        dormant_after_days=args.dormant_after_days,
        active=(*(args.active or ()), *(live or ())),
        project=args.project,
    )
    return orphans, live


def add_query_arguments(
    parser: argparse.ArgumentParser, *, name_default: str | None, name_help: str
) -> None:
    """Register the store, dormancy, roster and key arguments of an orphan query.

    Parameters
    ----------
    parser : argparse.ArgumentParser
        The action's parser.
    name_default : str or None
        Default of ``--name``; ``None`` makes ``--name`` required.
    name_help : str
        Help text of ``--name``.
    """
    parser.add_argument("db", help="Path to the hub event store (the hub's --db).")
    parser.add_argument(
        "--dormant-after-days",
        type=positive_days,
        default=DEFAULT_DORMANT_DAYS,
        help=f"Days without activity before a name counts (default {DEFAULT_DORMANT_DAYS:g}).",
    )
    parser.add_argument(
        "--active",
        action="append",
        metavar="NAME",
        help="A name known to be connected now; never listed. Repeatable.",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Also read the hub's live roster; a name connected now is never listed.",
    )
    parser.add_argument("--uri", default=default_hub_uri(), help="The live hub.")
    if name_default is None:
        parser.add_argument("--name", required=True, help=name_help)
    else:
        parser.add_argument("--name", default=name_default, help=name_help)
    parser.add_argument("--token", default=None, help="Shared-secret token for the live hub.")
    parser.add_argument(
        "--ready-timeout", type=float, default=5.0, help="Seconds to await the live hub."
    )
    parser.add_argument("--json", action="store_true", help="Print a JSON document.")
    parser.add_argument(
        "--db-key-file", default=None, help="Owner-only SQLCipher key for an encrypted store."
    )
    parser.add_argument(
        "--row-mac-key",
        default=None,
        help="The store's row-authentication key (default: <db>.rowmac.key when present).",
    )
