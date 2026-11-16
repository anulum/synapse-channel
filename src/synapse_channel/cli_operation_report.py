# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — ``synapse operation report``: one operation's chain from the event store
"""Print one declared operation's chain from a hub event store, without the hub.

``synapse operation report DB --operation-id ID`` reads the store as
``synapse postmortem`` does, so it needs no hub connection and no grant: the
operator reads their own journal. ``--json`` prints the document of
:func:`synapse_channel.core.operation_report.operation_report_to_json`.
"""

from __future__ import annotations

import argparse
import json

from synapse_channel.core.operation_report import (
    operation_report_to_json,
    render_lines,
    run_operation_report,
)
from synapse_channel.terminal_text import terminal_text


def _cmd_report(args: argparse.Namespace) -> int:
    """Print the report; exit 0 recorded, 1 not recorded, 2 no such store."""
    try:
        report = run_operation_report(args.db, args.operation_id, key_file=args.db_key_file)
    except ValueError as exc:
        print(terminal_text(str(exc)))
        return 2
    if args.json:
        print(json.dumps(operation_report_to_json(report), sort_keys=True))
    else:
        for line in render_lines(report):
            print(terminal_text(line))
    return 1 if report.state == "not recorded" else 0


def add_report_parser(actions: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``synapse operation report``.

    Parameters
    ----------
    actions : argparse._SubParsersAction
        The ``synapse operation`` action group.
    """
    report = actions.add_parser(
        "report", help="Print one operation's chain from a hub event store, without the hub."
    )
    report.add_argument("db", help="Path to the hub event store, e.g. ~/synapse/hub.db.")
    report.add_argument("--operation-id", required=True, help="The declared operation.")
    report.add_argument(
        "--db-key-file", default=None, help="Owner-only SQLCipher key for an encrypted event store."
    )
    report.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    report.set_defaults(func=_cmd_report)
