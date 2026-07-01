# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — CLI for the generic tmux-backed agent wake transport
"""Command-line wiring for the tmux-backed terminal-agent wake transport.

Registers ``synapse agent-tmux`` and, through :func:`register_parsers`, the
Codex-named ``synapse codex-tmux`` alias. Both share one command handler and one
configuration builder; they differ only in the subcommand name and the
``--agent-command``/``--codex-command`` flag spelling, which both resolve to the
same ``agent_command`` destination.
"""

from __future__ import annotations

import argparse
import shlex
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from synapse_channel.agent_tmux import (
    DEFAULT_SUBMIT_DELAY,
    AgentTmuxConfig,
    AgentTmuxStatus,
    AgentTmuxWakeResult,
    inject_wake,
    start_session,
    status,
    wait_and_wake,
)
from synapse_channel.client.agent import default_hub_uri

Starter = Callable[[AgentTmuxConfig], AgentTmuxWakeResult]
Injector = Callable[[AgentTmuxConfig], AgentTmuxWakeResult]
StatusRunner = Callable[[AgentTmuxConfig], AgentTmuxStatus]


class Waiter(Protocol):
    """Callable compatible with the wait-and-wake runner."""

    def __call__(
        self,
        config: AgentTmuxConfig,
        *,
        max_wakes: int | None,
        max_wait_failures: int | None,
    ) -> int:
        """Run the wait loop for ``config``."""


def _config_from_args(args: argparse.Namespace) -> AgentTmuxConfig:
    """Build a tmux wake configuration from parsed CLI args."""
    return AgentTmuxConfig(
        identity=args.identity,
        session=args.session,
        cwd=args.cwd,
        agent_command=tuple(shlex.split(args.agent_command)),
        tmux_bin=args.tmux_bin,
        synapse_bin=args.synapse_bin,
        uri=args.uri,
        token=args.token,
        submit_delay=args.submit_delay,
    )


def _print_wake_result(result: AgentTmuxWakeResult) -> None:
    """Print a compact wake operation result."""
    print(result.detail or ("ok" if result.returncode == 0 else "failed"))


def _print_status(snapshot: AgentTmuxStatus) -> None:
    """Print a compact status snapshot."""
    print(f"identity: {snapshot.identity}")
    print(f"tmux session: {'online' if snapshot.session_exists else 'missing'}")
    command = snapshot.pane_command or "unknown"
    print(f"pane command: {command}")
    print(f"agent pane: {'active' if snapshot.agent_active else 'inactive'}")


def _cmd_agent_tmux(
    args: argparse.Namespace,
    *,
    starter: Starter = start_session,
    injector: Injector = inject_wake,
    status_runner: StatusRunner = status,
    waiter: Waiter = wait_and_wake,
) -> int:
    """Dispatch ``synapse agent-tmux`` / ``codex-tmux`` subcommands."""
    config = _config_from_args(args)
    if args.agent_tmux_command == "start":
        result = starter(config)
        _print_wake_result(result)
        return result.returncode
    if args.agent_tmux_command == "wake":
        result = injector(config)
        _print_wake_result(result)
        return result.returncode
    if args.agent_tmux_command == "status":
        snapshot = status_runner(config)
        _print_status(snapshot)
        return 0 if snapshot.session_exists and snapshot.agent_active else 1
    if args.agent_tmux_command == "wait":
        return waiter(config, max_wakes=args.max_wakes, max_wait_failures=args.max_wait_failures)
    print("agent-tmux requires a subcommand")
    return 2


def _add_common_args(
    parser: argparse.ArgumentParser, *, command_flag: str, command_default: str, command_help: str
) -> None:
    """Add common target options to one wake subcommand."""
    parser.add_argument("--identity", required=True, help="Synapse identity to wake.")
    parser.add_argument("--session", required=True, help="tmux session target.")
    parser.add_argument("--cwd", type=Path, default=Path.cwd(), help="Agent working directory.")
    parser.add_argument(
        command_flag,
        dest="agent_command",
        default=command_default,
        help=command_help,
    )
    parser.add_argument("--tmux-bin", default="tmux", help="tmux executable.")
    parser.add_argument("--synapse-bin", default="synapse", help="synapse executable.")
    parser.add_argument("--uri", default=default_hub_uri(), help="Synapse hub URI.")
    parser.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    parser.add_argument(
        "--submit-delay",
        type=float,
        default=DEFAULT_SUBMIT_DELAY,
        help=(
            "Seconds to pause between typing the wake prompt and pressing Enter "
            "so the agent UI commits the line before it is submitted."
        ),
    )


def register_parsers(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    *,
    command_name: str,
    command_help: str,
    command_flag: str,
    command_default: str,
    command_flag_help: str,
) -> None:
    """Register one wake subparser group (``agent-tmux`` or ``codex-tmux``).

    Parameters
    ----------
    subparsers : argparse subparsers action
        The top-level ``synapse`` subparser registry.
    command_name : str
        Subcommand name to expose (e.g. ``agent-tmux`` or ``codex-tmux``).
    command_help : str
        Group help text for the subcommand.
    command_flag : str
        Launch-command flag spelling (e.g. ``--agent-command``).
    command_default : str
        Default launch command when the flag is omitted.
    command_flag_help : str
        Help text for the launch-command flag.
    """
    group = subparsers.add_parser(command_name, help=command_help)
    nested = group.add_subparsers(dest="agent_tmux_command", required=True)

    def _common(parser: argparse.ArgumentParser) -> None:
        _add_common_args(
            parser,
            command_flag=command_flag,
            command_default=command_default,
            command_help=command_flag_help,
        )

    start = nested.add_parser("start", help="Start or verify the agent tmux session.")
    _common(start)
    start.set_defaults(func=_cmd_agent_tmux)

    wake = nested.add_parser("wake", help="Inject the fixed wake prompt into the tmux pane.")
    _common(wake)
    wake.set_defaults(func=_cmd_agent_tmux)

    status_parser = nested.add_parser("status", help="Report tmux and agent pane health.")
    _common(status_parser)
    status_parser.set_defaults(func=_cmd_agent_tmux)

    wait = nested.add_parser("wait", help="Wait on Synapse, then wake the tmux agent pane.")
    _common(wait)
    wait.add_argument(
        "--max-wakes",
        type=int,
        default=None,
        help="Stop after N wakes; omit to run until interrupted.",
    )
    wait.add_argument(
        "--max-wait-failures",
        type=int,
        default=None,
        help=(
            "Give up after N consecutive failed waits; omit to retry the hub "
            "indefinitely with backoff (the counter resets on every wake)."
        ),
    )
    wait.set_defaults(func=_cmd_agent_tmux)


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the generic ``agent-tmux`` subparser group."""
    register_parsers(
        subparsers,
        command_name="agent-tmux",
        command_help="Wake an existing terminal-agent tmux session from Synapse messages.",
        command_flag="--agent-command",
        command_default="codex",
        command_flag_help=(
            "Shell-style command used when starting the tmux session "
            "(e.g. codex or kimi); defaults to codex."
        ),
    )
