# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — event-query CLI command
"""CLI wrapper for temporal event-log queries."""

from __future__ import annotations

import argparse
import json
import sys

from synapse_channel.core.event_query import render_human, result_to_json, run_query
from synapse_channel.native_query import render_human as render_native
from synapse_channel.native_query import result_to_json as native_to_json
from synapse_channel.native_query import run_native_query, selector_of


def _cmd_event_query(args: argparse.Namespace) -> int:
    """Run one temporal event-log query and print the result."""
    key_file = getattr(args, "db_key_file", None)
    try:
        if selector_of(args.query) is not None:
            # Native-message records have their own reader: the temporal query
            # parser and executor are at their size limits.
            native = run_native_query(args.db, args.query, key_file=key_file)
            if args.json:
                data = native_to_json(native, limit=args.limit)
                print(json.dumps(data, indent=2, sort_keys=True))
            else:
                print(render_native(native, limit=args.limit))
            return 0
        result = run_query(
            args.db,
            args.query,
            limit=args.limit,
            key_file=key_file,
        )
    except (ValueError, OSError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(result_to_json(result), indent=2, sort_keys=True))
    else:
        print(render_human(result))
    return 0


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``event-query`` subparser."""
    parser = subparsers.add_parser(
        "event-query",
        help="Query a hub SQLite event store for temporal task and conflict evidence.",
    )
    parser.add_argument("db", help="Path to the hub event store, e.g. ~/synapse/hub.db.")
    parser.add_argument(
        "--db-key-file",
        default=None,
        help="Owner-only SQLCipher key for an encrypted event store.",
    )
    parser.add_argument(
        "query",
        help=(
            "Query string: 'task T timeline', 'task T at seq N', "
            "'task T at time SECONDS', 'path PATH between START END', or "
            "'conflicts at seq N', 'receipts AGENT', "
            "'universal-receipts all', or 'native-messages SEAT' "
            "('native-messages all'). Prototype aliases include "
            "'timeline(\"T\").' "
            "and 'MATCH (task:TASK {id:\"T\"}) RETURN timeline'."
        ),
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cap output to the most recent N records (and conflict pairs).",
    )
    parser.set_defaults(func=_cmd_event_query)
