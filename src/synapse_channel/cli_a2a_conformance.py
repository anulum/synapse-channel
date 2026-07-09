# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — A2A conformance CLI command
"""CLI command for the Agent2Agent conformance matrix."""

from __future__ import annotations

import argparse
import json

from synapse_channel.a2a_conformance import (
    ConformanceStatus,
    conformance_report,
    render_conformance_markdown,
)

_STATUS_CHOICES: tuple[ConformanceStatus, ...] = (
    "supported",
    "partial",
    "unsupported",
    "external",
)


def _cmd_a2a_conformance(args: argparse.Namespace) -> int:
    """Print the local A2A conformance matrix."""
    status = _status_filter(args.status)
    if args.json:
        print(json.dumps(conformance_report(status=status), indent=2, sort_keys=True))
    else:
        print(render_conformance_markdown(status=status))
    return 0


def _status_filter(value: str | None) -> ConformanceStatus | None:
    """Return a typed status filter from argparse input."""
    if value is None:
        return None
    if value not in _STATUS_CHOICES:
        raise ValueError(f"unsupported A2A conformance status: {value}")
    return value


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the A2A conformance subcommand."""
    parser = subparsers.add_parser(
        "a2a-conformance",
        help="Print the local A2A 1.0.0 conformance matrix.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of Markdown.",
    )
    parser.add_argument(
        "--status",
        choices=_STATUS_CHOICES,
        default=None,
        help="Show only rows with this status.",
    )
    parser.set_defaults(func=_cmd_a2a_conformance)
