# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — CLI for tmux-backed Codex wake transport
"""Command-line wiring for tmux-backed Codex wake transport."""

from __future__ import annotations

import argparse
import shlex
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from synapse_channel.client.agent import DEFAULT_HUB_URI
from synapse_channel.codex_tmux import (
    DEFAULT_SUBMIT_DELAY,
    CodexTmuxConfig,
    CodexTmuxStatus,
    CodexTmuxWakeResult,
    inject_wake,
    start_session,
    status,
    wait_and_wake,
)

Starter = Callable[[CodexTmuxConfig], CodexTmuxWakeResult]
Injector = Callable[[CodexTmuxConfig], CodexTmuxWakeResult]
StatusRunner = Callable[[CodexTmuxConfig], CodexTmuxStatus]


class Waiter(Protocol):
    """Callable compatible with the wait-and-wake runner."""

    def __call__(
        self,
        config: CodexTmuxConfig,
        *,
        max_wakes: int | None,
        max_wait_failures: int | None,
    ) -> int:
        """Run the wait loop for ``config``."""


def _config_from_args(args: argparse.Namespace) -> CodexTmuxConfig:
    """Build a tmux wake configuration from parsed CLI args."""
    return CodexTmuxConfig(
        identity=args.identity,
        session=args.session,
        cwd=args.cwd,
        codex_command=tuple(shlex.split(args.codex_command)),
        tmux_bin=args.tmux_bin,
        synapse_bin=args.synapse_bin,
        uri=args.uri,
        token=args.token,
        submit_delay=args.submit_delay,
    )


def _print_wake_result(result: CodexTmuxWakeResult) -> None:
    """Print a compact wake operation result."""
    print(result.detail or ("ok" if result.returncode == 0 else "failed"))


def _print_status(snapshot: CodexTmuxStatus) -> None:
    """Print a compact status snapshot."""
    print(f"identity: {snapshot.identity}")
    print(f"tmux session: {'online' if snapshot.session_exists else 'missing'}")
    command = snapshot.pane_command or "unknown"
    print(f"pane command: {command}")
    print(f"Codex pane: {'active' if snapshot.codex_active else 'inactive'}")


def _cmd_codex_tmux(
    args: argparse.Namespace,
    *,
    starter: Starter = start_session,
    injector: Injector = inject_wake,
    status_runner: StatusRunner = status,
    waiter: Waiter = wait_and_wake,
) -> int:
    """Dispatch ``synapse codex-tmux`` subcommands."""
    config = _config_from_args(args)
    if args.codex_tmux_command == "start":
        result = starter(config)
        _print_wake_result(result)
        return result.returncode
    if args.codex_tmux_command == "wake":
        result = injector(config)
        _print_wake_result(result)
        return result.returncode
    if args.codex_tmux_command == "status":
        snapshot = status_runner(config)
        _print_status(snapshot)
        return 0 if snapshot.session_exists and snapshot.codex_active else 1
    if args.codex_tmux_command == "wait":
        return waiter(config, max_wakes=args.max_wakes, max_wait_failures=args.max_wait_failures)
    print("codex-tmux requires a subcommand")
    return 2


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    """Add common target options to one ``codex-tmux`` subcommand."""
    parser.add_argument("--identity", required=True, help="Synapse identity to wake.")
    parser.add_argument("--session", required=True, help="tmux session target.")
    parser.add_argument("--cwd", type=Path, default=Path.cwd(), help="Codex working directory.")
    parser.add_argument(
        "--codex-command",
        default="codex",
        help="Shell-style command used when starting the tmux session; defaults to codex.",
    )
    parser.add_argument("--tmux-bin", default="tmux", help="tmux executable.")
    parser.add_argument("--synapse-bin", default="synapse", help="synapse executable.")
    parser.add_argument("--uri", default=DEFAULT_HUB_URI, help="Synapse hub URI.")
    parser.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    parser.add_argument(
        "--submit-delay",
        type=float,
        default=DEFAULT_SUBMIT_DELAY,
        help=(
            "Seconds to pause between typing the wake prompt and pressing Enter "
            "so the Codex UI commits the line before it is submitted."
        ),
    )


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``codex-tmux`` subparser group."""
    codex_tmux = subparsers.add_parser(
        "codex-tmux",
        help="Wake an existing Codex tmux session from Synapse messages.",
    )
    nested = codex_tmux.add_subparsers(dest="codex_tmux_command", required=True)

    start = nested.add_parser("start", help="Start or verify the Codex tmux session.")
    _add_common_args(start)
    start.set_defaults(func=_cmd_codex_tmux)

    wake = nested.add_parser("wake", help="Inject the fixed wake prompt into the tmux pane.")
    _add_common_args(wake)
    wake.set_defaults(func=_cmd_codex_tmux)

    status_parser = nested.add_parser("status", help="Report tmux and Codex pane health.")
    _add_common_args(status_parser)
    status_parser.set_defaults(func=_cmd_codex_tmux)

    wait = nested.add_parser("wait", help="Wait on Synapse, then wake the tmux Codex pane.")
    _add_common_args(wait)
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
    wait.set_defaults(func=_cmd_codex_tmux)
