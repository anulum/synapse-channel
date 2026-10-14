# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — CLI entry point for exact native wake relay bindings
"""Expose the bound wake relay through the existing waker command group."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from synapse_channel.wake_adapters import ACCEPTED, WakeTarget
from synapse_channel.wake_relay_service import relay_bound_wake


def _command(args: argparse.Namespace) -> int:
    """Report one stable JSON receipt without echoing policy or message content."""
    try:
        result = relay_bound_wake(
            args.binding_file,
            WakeTarget(args.identity, args.native_session, args.execution_host),
            args.wake_id,
            deadline_seconds=args.deadline,
        )
    except (OSError, ValueError) as exc:
        print(f"waker relay: {type(exc).__name__}", file=sys.stderr)
        return 2
    print(json.dumps(asdict(result), sort_keys=True))
    return 0 if result.state == ACCEPTED else 1


def add_parser(nested: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register an explicit one-message relay with no session discovery fallback."""
    parser = nested.add_parser(
        "relay", help="Relay one journal message using an owner-only native binding."
    )
    parser.add_argument(
        "--binding-file", required=True, type=Path, help="Owner-only single-link JSON binding."
    )
    parser.add_argument("--identity", required=True, help="Exact logical project/seat.")
    parser.add_argument(
        "--native-session", required=True, help="Exact canonical native session UUID."
    )
    parser.add_argument("--execution-host", required=True, help="Exact host name in the binding.")
    parser.add_argument(
        "--wake-id", required=True, type=int, help="Actual hub journal message sequence."
    )
    parser.add_argument(
        "--deadline", default=30.0, type=float, help="Finite non-negative seconds per adapter."
    )
    parser.set_defaults(func=_command)
