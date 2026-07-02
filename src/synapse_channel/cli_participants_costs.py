# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — session cost/telemetry reporting subcommand of the participant CLI
"""``synapse participant costs`` — read session spend and telemetry back from a hub store.

Participant sessions that opt into telemetry (see
:mod:`synapse_channel.participants.session_metric_emit`) leave durable
``session_metric`` progress notes in the hub event log. This subcommand is the
operator surface over :mod:`synapse_channel.participants.session_metric_report`:
it reads a hub SQLite event store offline — no hub connection, mirroring
``synapse accounting report`` — and prints the latest snapshot per
``(agent, session)`` plus fleet totals: turns, errors, abstentions, token
pressure, metered spend, latency, and the highest rate-limit utilisation seen.

Where ``accounting report`` answers *what models cost*, this answers *how
participant sessions are going and what they spent*. Both are descriptive
evidence, never an enforcement gate. Exit codes: ``0`` for a produced report
(including one with no recorded telemetry), ``2`` when the store is missing.
"""

from __future__ import annotations

import argparse
import json
import sys

from synapse_channel.participants.session_metric_report import (
    render_session_metric_report,
    run_session_metric_report,
    session_metric_report_to_json,
)


def _cmd_costs(args: argparse.Namespace) -> int:
    """Aggregate session telemetry from an event store and print it."""
    try:
        report = run_session_metric_report(args.db)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(session_metric_report_to_json(report), indent=2, sort_keys=True))
    else:
        print(render_session_metric_report(report))
    return 0


def add_parsers(group: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``participant costs`` on the ``participant`` command group."""
    costs = group.add_parser(
        "costs",
        help="Report per-session spend and telemetry from a hub SQLite event store.",
    )
    costs.add_argument("db", help="Path to the hub event store, e.g. ~/synapse/hub.db.")
    costs.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    costs.set_defaults(func=_cmd_costs)
