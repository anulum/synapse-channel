# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — `synapse ledger`: show the session ledger, read the interim v0 files
"""``synapse ledger``: show this host's session ledger, and read the interim tool's v0 files."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from synapse_channel.session_ledger import (
    LedgerError,
    Row,
    fold,
    ledger_dir,
    read_events,
    read_v0,
)

USAGE_EXIT_CODE = 2
"""Exit code for a ledger that cannot be read here."""


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``ledger`` with ``show`` and ``read-v0``."""
    parser = subparsers.add_parser("ledger", help="Show this host's session ledger.")
    nested = parser.add_subparsers(dest="ledger_command", required=True)
    show = nested.add_parser("show", help="One line per row: open rows, or every row with --all.")
    show.add_argument("--all", action="store_true", help="Show ended rows too.")
    show.add_argument("--boot", default=None, help="Only rows of this boot id.")
    show.add_argument("--json", action="store_true", help="Print the rows as JSON.")
    show.set_defaults(func=_cmd_show)
    v0 = nested.add_parser(
        "read-v0", help="Read an interim v0 event file as v1 events; writes nothing."
    )
    v0.add_argument("path", type=Path, help="The v0 events file (events-YYYY-MM.jsonl).")
    v0.set_defaults(func=_cmd_read_v0)


def _selected(args: argparse.Namespace, directory: Path) -> list[Row]:
    """Return the rows ``show`` prints: open ones, or all, of one boot if asked."""
    events = read_events(directory) if directory.is_dir() else iter(())
    boot = f"{args.boot}:" if args.boot else ""
    return [
        row
        for row in fold(events).values()
        if (args.all or row.open) and row.row_id.startswith(boot)
    ]


def _line(row: Row) -> str:
    """Return one row as a line: identity, vendor, launcher, state, native id, row id."""
    start, end = row.start, row.end or {}
    state = "open" if row.open else str(end.get("end_kind") or end.get("event"))
    native = row.observed.get("native_session_id") or start.get("native_session_id") or "-"
    return (
        f"{start.get('identity') or '-'}  {start.get('vendor') or '-'}  "
        f"{start.get('launcher') or '-'}  {state}  {native}  {row.row_id}"
    )


def _cmd_show(args: argparse.Namespace) -> int:
    """Print the ledger's rows."""
    try:
        directory = ledger_dir()
    except LedgerError as exc:
        print(f"synapse ledger: {exc}")
        return USAGE_EXIT_CODE
    rows = _selected(args, directory)
    if args.json:
        print(json.dumps([row.__dict__ for row in rows], indent=1, sort_keys=True))
    else:
        for row in rows:
            print(_line(row))
    return 0


def _cmd_read_v0(args: argparse.Namespace) -> int:
    """Print a v0 file's events as v1 events, one JSON object per line."""
    try:
        events = list(read_v0(args.path))
    except OSError as exc:
        print(f"synapse ledger: cannot read {args.path}: {exc.strerror or exc}")
        return USAGE_EXIT_CODE
    for event in events:
        print(json.dumps(event, sort_keys=True, ensure_ascii=False))
    return 0
