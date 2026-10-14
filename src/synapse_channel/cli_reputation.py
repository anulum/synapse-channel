# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — opt-in local advisory ranking command
"""Expose the explicit advisory ranking without changing the default trust graph."""

from __future__ import annotations

import argparse
import json
import sys

from synapse_channel.core.record_keeping_source import DEFAULT_MAX_EVENTS, DEFAULT_MAX_PAYLOAD_BYTES
from synapse_channel.core.reputation import render_reputation, reputation_to_json, run_reputation


def _cmd_reputation(args: argparse.Namespace) -> int:
    try:
        report = run_reputation(
            args.db,
            as_of=args.as_of,
            agent=args.agent,
            since=args.since,
            key_file=args.db_key_file,
            row_mac_key_file=args.row_mac_key_file,
            through_seq=args.through_seq,
            max_events=args.max_events,
            max_payload_bytes=args.max_payload_bytes,
        )
        print(
            json.dumps(reputation_to_json(report), indent=2, sort_keys=True, allow_nan=False)
            if args.json
            else render_reputation(report)
        )
        return 0
    except (ValueError, OSError, RuntimeError, RecursionError):
        print(
            "Advisory ranking refused; check the journal, keys, clocks and budgets.",
            file=sys.stderr,
        )
        return 2


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the local ranking, filters and complete-read budgets.

    Parameters
    ----------
    subparsers : argparse._SubParsersAction
        Root CLI command registrar.
    """
    parser = subparsers.add_parser(
        "reputation", help="Rank recorded evidence for human triage only."
    )
    parser.add_argument("db", help="Existing SQLite or SQLCipher journal.")
    parser.add_argument("--db-key-file", help="Existing owner-only SQLCipher key.")
    parser.add_argument("--row-mac-key-file", help="Existing trusted enrolled row MAC key.")
    parser.add_argument("--agent", help="Keep evidence involving this agent.")
    parser.add_argument("--since", type=float, help="Inclusive finite evidence timestamp.")
    parser.add_argument("--as-of", type=float, help="Finite reference clock for stale signals.")
    parser.add_argument(
        "--through-seq", type=int, help="Inclusive sequence ceiling; zero is empty."
    )
    parser.add_argument("--max-events", type=int, default=DEFAULT_MAX_EVENTS)
    parser.add_argument("--max-payload-bytes", type=int, default=DEFAULT_MAX_PAYLOAD_BYTES)
    parser.add_argument("--json", action="store_true", help="Emit ranking counts and provenance.")
    parser.set_defaults(func=_cmd_reputation)
