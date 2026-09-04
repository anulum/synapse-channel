# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — active-waker lifecycle command
"""Command-line lifecycle for systemd-supervised terminal-agent wakers."""

from __future__ import annotations

import argparse
import shlex
import sys
from pathlib import Path

from synapse_channel.agent_tmux import DEFAULT_PANE_PROBE_INTERVAL, DEFAULT_SUBMIT_DELAY
from synapse_channel.client.agent import default_hub_uri
from synapse_channel.waker_service import inhibit_waker, inspect_waker, install_waker, resume_waker
from synapse_channel.waker_supervisor import run_waker


def _print_lines(lines: tuple[str, ...]) -> None:
    """Print lifecycle evidence in stable order."""
    for line in lines:
        print(line)


def _cmd_waker(args: argparse.Namespace) -> int:
    """Dispatch one active-waker lifecycle operation."""
    try:
        if args.waker_command == "install":
            command = tuple(shlex.split(args.agent_command))
            result = install_waker(
                identity=args.identity,
                session=args.session,
                cwd=args.cwd,
                agent_command=command,
                tmux_bin=args.tmux_bin,
                synapse_bin=args.synapse_bin,
                uri=args.uri,
                token_file=Path(args.token_file) if args.token_file else None,
                submit_delay=args.submit_delay,
                pane_probe_interval=args.pane_probe_interval,
                start=args.start,
            )
            _print_lines(result.lines)
            return 0 if result.ok else 1
        if args.waker_command == "stop":
            result = inhibit_waker(
                args.identity,
                reason=args.reason,
                expected_generation=args.expect_generation,
            )
            _print_lines(result.lines)
            return 0 if result.ok else 1
        if args.waker_command == "resume":
            result = resume_waker(
                args.identity,
                expected_generation=args.expect_generation,
            )
            _print_lines(result.lines)
            return 0 if result.ok else 1
        if args.waker_command == "status":
            snapshot = inspect_waker(args.identity)
            print(f"identity: {snapshot.identity}")
            print(f"desired state: {snapshot.desired_state}")
            print(f"generation: {snapshot.generation}")
            print(f"unit: {snapshot.unit}")
            print(f"service: {snapshot.service_active}/{snapshot.service_substate}")
            print(
                "restarts: "
                + (str(snapshot.restart_count) if snapshot.restart_count is not None else "unknown")
            )
            print(
                "main status: "
                + (str(snapshot.main_status) if snapshot.main_status is not None else "unknown")
            )
            print(
                "provider: "
                + (
                    "active"
                    if snapshot.provider.session_exists
                    and snapshot.provider.binding_valid
                    and snapshot.provider.agent_active
                    else "unavailable"
                )
            )
            print(f"pending wake: {'yes' if snapshot.provider.pending_wake else 'no'}")
            if snapshot.inhibit_reason is not None:
                print(f"inhibit reason: {snapshot.inhibit_reason}")
            return 0 if snapshot.ready else 1
        if args.waker_command == "run":
            return run_waker(args.identity)
    except (OSError, ValueError) as exc:
        print(f"waker {args.waker_command}: {exc}", file=sys.stderr)
        return 2
    return 2


def _identity_argument(parser: argparse.ArgumentParser) -> None:
    """Add the exact logical-seat argument shared by all lifecycle verbs."""
    parser.add_argument("--identity", required=True, help="Exact logical seat, e.g. repo/codex-1.")


def _generation_argument(parser: argparse.ArgumentParser) -> None:
    """Add the optional compare-and-swap generation guard."""
    parser.add_argument(
        "--expect-generation",
        type=int,
        default=None,
        help="Refuse the mutation if the durable configuration generation changed.",
    )


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``synapse waker`` active-execution lifecycle."""
    group = subparsers.add_parser(
        "waker",
        help="Install and control an automatically restarted terminal-agent wake bridge.",
    )
    nested = group.add_subparsers(dest="waker_command", required=True)

    install = nested.add_parser("install", help="Write one owner-only seat config and user unit.")
    _identity_argument(install)
    install.add_argument("--session", required=True, help="tmux session containing the provider.")
    install.add_argument("--cwd", type=Path, default=Path.cwd(), help="Provider working directory.")
    install.add_argument(
        "--agent-command", default="codex", help="Shell-style provider launch command."
    )
    install.add_argument("--tmux-bin", default="tmux", help="tmux executable.")
    install.add_argument(
        "--synapse-bin",
        default=None,
        help="Synapse executable stored in the unit and provider bridge.",
    )
    install.add_argument("--uri", default=default_hub_uri(), help="Synapse hub URI.")
    install.add_argument(
        "--token-file", default=None, help="Owner-only shared-secret token file path."
    )
    install.add_argument(
        "--submit-delay", type=float, default=DEFAULT_SUBMIT_DELAY, help="Safe submit delay."
    )
    install.add_argument(
        "--pane-probe-interval",
        type=float,
        default=DEFAULT_PANE_PROBE_INTERVAL,
        help="Maximum seconds between provider liveness probes.",
    )
    install.add_argument("--start", action="store_true", help="Enable and start the exact unit.")
    install.set_defaults(func=_cmd_waker)

    stop = nested.add_parser("stop", help="Persistently inhibit and stop one exact waker.")
    _identity_argument(stop)
    stop.add_argument("--reason", required=True, help="Recorded malfunction or operator reason.")
    _generation_argument(stop)
    stop.set_defaults(func=_cmd_waker)

    resume = nested.add_parser("resume", help="Explicitly clear inhibit and start one exact waker.")
    _identity_argument(resume)
    _generation_argument(resume)
    resume.set_defaults(func=_cmd_waker)

    status_parser = nested.add_parser(
        "status", help="Report desired, systemd, provider, and pending-wake state."
    )
    _identity_argument(status_parser)
    status_parser.set_defaults(func=_cmd_waker)

    run = nested.add_parser("run", help="Run the configured bridge (service-manager entry point).")
    _identity_argument(run)
    run.set_defaults(func=_cmd_waker)
