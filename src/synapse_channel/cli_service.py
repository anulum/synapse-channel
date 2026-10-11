# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — command-line lifecycle for the local user services
"""Command-line lifecycle for the local Synapse user services.

``synapse service hub-restart`` prints what a hub restart would touch. It
restarts the hub only when given the exact current hub process id, and then
with one service-manager job.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Callable
from functools import partial
from pathlib import Path

from websockets.exceptions import ConnectionClosed

from synapse_channel.cli_doctor_mailbox import fetch_doctor_roster
from synapse_channel.client.agent import SynapseAgent, default_hub_uri
from synapse_channel.hub_restart import (
    DEFAULT_ANSWER_TIMEOUT,
    DEFAULT_SETTLE_TIMEOUT,
    HubRestartReport,
    default_lock_path,
    plan_hub_restart,
    render_plan,
    restart_hub,
)
from synapse_channel.service_dependencies import HUB_UNIT, CommandRunner, ServiceQueryError
from synapse_channel.waker_commands import DEFAULT_COMMAND_TIMEOUT, run_waker_command

PROBE_NAME = "service-hub-restart-probe"
"""Connection name of the one-shot question that checks the hub answers."""

HubRestarter = Callable[..., HubRestartReport]


def hub_answers(*, uri: str, token: str | None, ready_timeout: float = 5.0) -> bool:
    """Return whether the hub at ``uri`` answers a roster question.

    A hub that is absent, closes the connection or refuses the probe's name
    does not answer; none of these raises.
    """
    try:
        roster = asyncio.run(
            fetch_doctor_roster(
                uri=uri,
                name=PROBE_NAME,
                token=token,
                agent_factory=SynapseAgent,
                ready_timeout=ready_timeout,
            )
        )
    except (OSError, ConnectionClosed):
        return False
    return roster is not None


def _cmd_hub_restart(
    args: argparse.Namespace,
    *,
    runner: CommandRunner | None = None,
    restarter: HubRestarter = restart_hub,
) -> int:
    """Dispatch ``synapse service hub-restart``.

    Returns 0 for a printed plan or a verified restart, 1 for a restart that
    left something not running or a failed job, and 2 for a refusal or a
    service manager that did not answer.
    """
    bounded = runner or partial(run_waker_command, timeout=args.command_timeout)
    try:
        if args.authorized_hub_pid is None:
            plan = plan_hub_restart(runner=bounded, hub_unit=args.hub_unit)
            for line in render_plan(plan):
                print(line)
            print(
                "no restart requested; to restart, pass the hub pid shown above: "
                f"--authorized-hub-pid {plan.hub.main_pid}"
            )
            return 0
        report = restarter(
            authorized_hub_pid=args.authorized_hub_pid,
            runner=bounded,
            hub_answers=partial(hub_answers, uri=args.uri, token=args.token),
            lock_path=Path(args.lock_file) if args.lock_file else default_lock_path(),
            allow_dependent_restart=args.allow_dependent_restart,
            hub_unit=args.hub_unit,
            answer_timeout=args.answer_timeout,
            settle_timeout=args.settle_timeout,
        )
    except ServiceQueryError as exc:
        print(f"synapse service hub-restart: {exc}")
        return 2
    for line in report.lines:
        print(line)
    if report.ok:
        return 0
    return 1 if report.restarted else 2


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``service`` command group."""
    group = subparsers.add_parser(
        "service",
        help="Inspect and operate the local Synapse user services.",
    )
    nested = group.add_subparsers(dest="service_command", required=True)

    restart = nested.add_parser(
        "hub-restart",
        help="Show what a hub restart touches; restart it as one job when authorised.",
    )
    restart.add_argument(
        "--authorized-hub-pid",
        type=int,
        default=None,
        help="Exact current hub process id whose disruption is authorised. "
        "Without it the command only prints the plan.",
    )
    restart.add_argument(
        "--allow-dependent-restart",
        action="store_true",
        help="Proceed although systemd would stop or restart other units with the hub.",
    )
    restart.add_argument("--hub-unit", default=HUB_UNIT, help="Exact hub user unit name.")
    restart.add_argument("--uri", default=default_hub_uri(), help="Hub URI to check afterwards.")
    restart.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    restart.add_argument(
        "--token-file",
        default=None,
        help="Read the shared-secret token from this file instead of --token.",
    )
    restart.add_argument(
        "--lock-file",
        default=None,
        help="Host-local lock file; defaults to the redeploy lock under XDG_RUNTIME_DIR.",
    )
    restart.add_argument(
        "--answer-timeout",
        type=float,
        default=DEFAULT_ANSWER_TIMEOUT,
        help="Seconds to wait for the restarted hub to answer.",
    )
    restart.add_argument(
        "--settle-timeout",
        type=float,
        default=DEFAULT_SETTLE_TIMEOUT,
        help="Seconds to wait for dependents that were running to run again.",
    )
    restart.add_argument(
        "--command-timeout",
        type=float,
        default=DEFAULT_COMMAND_TIMEOUT,
        help="Seconds to wait for each service-manager command.",
    )
    restart.set_defaults(func=_cmd_hub_restart)
