# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — bounded existing-journal observation CLI
"""Expose record export, calendar retention and labelled reports locally."""

from __future__ import annotations

import argparse
import json
import sys
import time

from synapse_channel.core.compliance import (
    FRAMEWORKS,
    assess_retention,
    build_export,
    build_report,
    export_to_json,
    render_human,
    render_report,
    render_retention,
    report_to_json,
    retention_to_json,
)
from synapse_channel.core.record_keeping_source import DEFAULT_MAX_EVENTS, DEFAULT_MAX_PAYLOAD_BYTES


def _cmd_compliance(args: argparse.Namespace) -> int:
    try:
        clock = getattr(args, "as_of", None)
        now = time.time() if clock is None else float(clock)
        if args.compliance_action == "report":
            report = build_report(
                args.db,
                framework=args.framework,
                now=now,
                through_seq=args.through_seq,
                key_file=args.db_key_file,
                row_mac_key_file=args.row_mac_key_file,
                max_events=args.max_events,
                max_payload_bytes=args.max_payload_bytes,
            )
            data, human = report_to_json(report), render_report(report)
        else:
            export = build_export(
                args.db,
                through_seq=args.through_seq,
                key_file=args.db_key_file,
                row_mac_key_file=args.row_mac_key_file,
                max_events=args.max_events,
                max_payload_bytes=args.max_payload_bytes,
            )
            if args.compliance_action == "retention":
                posture = assess_retention(export, now=now)
                data, human = retention_to_json(posture), render_retention(posture)
            else:
                data, human = export_to_json(export), render_human(export)
        print(json.dumps(data, indent=2, sort_keys=True, allow_nan=False) if args.json else human)
        return 0
    except (ValueError, OSError, RuntimeError, RecursionError):
        print(
            "Record observation refused; check the journal, keys, clock and export budgets.",
            file=sys.stderr,
        )
        return 2


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register all local record-observation verbs and their explicit budgets.

    Parameters
    ----------
    subparsers : argparse._SubParsersAction
        Root CLI registrar; no hub mutation or remote command is added.
    """
    group = subparsers.add_parser(
        "compliance",
        help="Read retained-log evidence; no compliance verdict.",
    )
    actions = group.add_subparsers(dest="compliance_action", required=True)
    export = actions.add_parser(
        "export", help="Commit one existing snapshot and export payload-free metadata."
    )
    retention = actions.add_parser(
        "retention", help="Observe the oldest event's six-calendar-month anniversary."
    )
    report = actions.add_parser(
        "report", help="Combine snapshot and retention observations under a supported label."
    )
    for parser in (export, retention, report):
        parser.add_argument("db", help="Existing SQLite or SQLCipher event journal.")
        parser.add_argument("--db-key-file", help="Existing owner-only SQLCipher key.")
        parser.add_argument("--row-mac-key-file", help="Existing trusted enrolled row MAC key.")
        parser.add_argument(
            "--through-seq", type=int, help="Inclusive ceiling; zero selects an empty prefix."
        )
        parser.add_argument(
            "--max-events",
            type=int,
            default=DEFAULT_MAX_EVENTS,
            help="Event budget; exhaustion refuses the whole result.",
        )
        parser.add_argument(
            "--max-payload-bytes",
            type=int,
            default=DEFAULT_MAX_PAYLOAD_BYTES,
            help="Total encoded-payload byte budget; exhaustion refuses.",
        )
        parser.add_argument("--json", action="store_true", help="Emit complete metadata as JSON.")
        parser.set_defaults(func=_cmd_compliance)
    for parser in (retention, report):
        parser.add_argument(
            "--as-of", type=float, help="Reference Unix clock; default current wall time."
        )
    report.add_argument("--framework", choices=FRAMEWORKS, default="eu-ai-act")
