# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — setup and worker-session CLI commands
"""Service setup and provider-neutral worker-session CLI commands."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from pathlib import Path

from synapse_channel.client.agent import default_hub_uri
from synapse_channel.service_setup import (
    install_user_services,
    service_suggestions,
    validate_systemd_executable,
)
from synapse_channel.worker_session import run_worker_session

ServiceInstaller = Callable[..., list[str]]
SuggestionBuilder = Callable[..., list[str]]
ProjectResolver = Callable[[], str]
WorkerSessionRunner = Callable[..., int]


def _default_project() -> str:
    """Return the current directory name as a last-resort setup project."""
    return Path.cwd().name


def _cmd_init(
    args: argparse.Namespace,
    *,
    service_installer: ServiceInstaller = install_user_services,
    suggestion_builder: SuggestionBuilder = service_suggestions,
    project_resolver: ProjectResolver = _default_project,
) -> int:
    """Dispatch ``synapse init`` for local user-service setup."""
    project = args.project or project_resolver()
    identity = args.identity or project
    try:
        if args.install_user_services or args.start_user_services:
            if args.synapse_bin is not None:
                validate_systemd_executable(args.synapse_bin)
            lines = service_installer(
                project=project,
                identity=identity,
                synapse_bin=args.synapse_bin,
                start=args.start_user_services,
            )
        else:
            lines = [
                "User services are not installed automatically unless requested.",
                "To install/start the local hub, project presence, and wake listener:",
                *suggestion_builder(
                    project=project, identity=identity, synapse_bin=args.synapse_bin
                ),
            ]
    except ValueError as exc:
        print(f"synapse init: {exc}")
        return 2
    for line in lines:
        print(line)
    return 0


def _cmd_worker_session(
    args: argparse.Namespace,
    *,
    session_runner: WorkerSessionRunner = run_worker_session,
) -> int:
    """Dispatch ``synapse worker-session``."""
    command: Sequence[str] = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        print("worker-session requires a provider command after --")
        return 2
    try:
        return session_runner(
            identity=args.identity,
            command=command,
            project=args.project,
            uri=args.uri,
            syn_bin=args.syn_bin,
            token=args.token,
            token_file=getattr(args, "token_file", None),
            arm=not args.no_arm,
            terminal_tmux=args.terminal_tmux,
            tmux_bin=args.tmux_bin,
            synapse_bin=args.synapse_bin,
            tmux_session=args.tmux_session,
        )
    except ValueError as exc:
        print(str(exc))
        return 2


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register setup and worker-session subcommands."""
    init = subparsers.add_parser(
        "init",
        help="Print or install local user services for hub, presence, and wake arming.",
    )
    init.add_argument("--project", default=None, help="Project identity for presence and wake.")
    init.add_argument(
        "--identity", default=None, help="Worker identity to arm; defaults to project."
    )
    init.add_argument(
        "--install-user-services",
        action="store_true",
        help="Write systemd user units for hub, presence, and wake arming.",
    )
    init.add_argument(
        "--start-user-services",
        action="store_true",
        help="Install units, daemon-reload, and enable/start hub, presence, and wake arming.",
    )
    init.add_argument(
        "--synapse-bin",
        default=None,
        help="Synapse executable path baked into generated units; defaults to PATH lookup.",
    )
    init.set_defaults(func=_cmd_init)

    worker = subparsers.add_parser(
        "worker-session",
        help="Run a provider command with SYN_PROJECT/SYN_IDENTITY and a cheap wake sidecar.",
    )
    worker.add_argument("--identity", required=True, help="Worker identity, e.g. PROJECT/ux.")
    worker.add_argument(
        "--project", default=None, help="Project override; defaults to identity prefix."
    )
    worker.add_argument("--uri", default=default_hub_uri())
    worker.add_argument(
        "--syn-bin", default="syn", help="Syn ergonomic command used for the sidecar."
    )
    worker.add_argument("--no-arm", action="store_true", help="Do not start the wake sidecar.")
    worker.add_argument(
        "--terminal-tmux",
        choices=("auto", "on", "off"),
        default="auto",
        help="Run interactive terminal providers through persistent tmux; default auto.",
    )
    worker.add_argument("--tmux-bin", default="tmux", help="tmux executable for terminal mode.")
    worker.add_argument(
        "--synapse-bin",
        default="synapse",
        help="Synapse executable used by persistent tmux waiters.",
    )
    worker.add_argument(
        "--tmux-session",
        default=None,
        help="Explicit tmux session name for terminal mode; defaults to the identity.",
    )
    worker.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    worker.add_argument("command", nargs=argparse.REMAINDER, help="Provider command after --.")
    worker.set_defaults(func=_cmd_worker_session)
