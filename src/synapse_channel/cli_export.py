# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — export a hub store to portable files and plain SQLite
"""``synapse export``: take the journal out of Synapse, read-only.

* ``create`` writes a new export directory from a hub store, in one read
  transaction, while a hub may keep running.
* ``verify`` checks an export's files against its manifest (consistency, not
  authenticity).
* ``to-sqlite`` verifies an export and copies exactly those rows into a new plain
  SQLite file that any SQLite tool can read; it is not a hub store.

Exit codes: ``0`` on success, ``1`` when an export is inconsistent, ``2`` when an
input cannot be read or an output already exists.
"""

from __future__ import annotations

import argparse
import json
import sys

from synapse_channel.core.event_row_mac import RowMacError, load_row_mac_key
from synapse_channel.core.journal_export import (
    ExportError,
    export_store,
    export_to_sqlite,
    verify_export,
)


def _cmd_create(args: argparse.Namespace) -> int:
    try:
        row_key = load_row_mac_key(args.row_mac_key) if args.row_mac_key else None
        manifest = export_store(args.db, args.out, key_file=args.db_key_file, row_key=row_key)
    except (ExportError, RowMacError, OSError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(
        f"exported {manifest['event_count']} events and {manifest['aef_receipt_count']} "
        f"AEF receipts to {args.out}"
    )
    failed = manifest["row_mac_counts"].get("failed", 0)
    if failed or manifest["quarantined_count"]:
        print(
            f"warning: {failed} rows failed the row MAC and {manifest['quarantined_count']} "
            "are quarantined; they are exported as stored and flagged",
            file=sys.stderr,
        )
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    try:
        verified = verify_export(args.export)
    except ExportError as exc:
        print(f"EXPORT_INCONSISTENT: {exc}")
        return 1
    if args.json:
        print(json.dumps({"verdict": "EXPORT_CONSISTENT", "manifest": verified.manifest}))
    else:
        print(
            f"EXPORT_CONSISTENT: {len(verified.events)} events, "
            f"{len(verified.aef_lines)} AEF receipts (consistency, not authenticity)"
        )
    return 0


def _cmd_to_sqlite(args: argparse.Namespace) -> int:
    try:
        verified = export_to_sqlite(args.export, args.sqlite)
    except ExportError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2 if "refusing to replace" in str(exc) else 1
    print(f"wrote {len(verified.events)} events to {args.sqlite} (plain SQLite, not a hub store)")
    return 0


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``synapse export`` and its actions."""
    export = subparsers.add_parser(
        "export", help="Export a hub store to portable files and plain SQLite (read-only)."
    )
    actions = export.add_subparsers(dest="export_command", required=True)
    create = actions.add_parser("create", help="Write a new export directory from a hub store.")
    create.add_argument("db", help="Path to the hub event store (the hub's --db).")
    create.add_argument("--out", required=True, help="New export directory.")
    create.add_argument(
        "--db-key-file", default=None, help="Owner-only SQLCipher key for an encrypted store."
    )
    create.add_argument(
        "--row-mac-key",
        default=None,
        help="The store's row-authentication key; each row's row_mac is then verified or failed.",
    )
    create.set_defaults(func=_cmd_create)
    verify = actions.add_parser(
        "verify", help="Check an export's files against its manifest (consistency only)."
    )
    verify.add_argument("export", help="Export directory.")
    verify.add_argument("--json", action="store_true", help="Emit the verdict and manifest.")
    verify.set_defaults(func=_cmd_verify)
    to_sqlite = actions.add_parser(
        "to-sqlite", help="Verify an export and copy it into a new plain SQLite file."
    )
    to_sqlite.add_argument("export", help="Export directory.")
    to_sqlite.add_argument("sqlite", help="New SQLite file to write.")
    to_sqlite.set_defaults(func=_cmd_to_sqlite)
