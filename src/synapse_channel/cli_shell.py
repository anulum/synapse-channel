# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — shell hook CLI commands
"""CLI commands for provider-neutral shell integration."""

from __future__ import annotations

import argparse
from collections.abc import Callable

from synapse_channel.shell_integration import (
    DEFAULT_PROVIDER_COMMANDS,
    install_shell_hook,
    render_shell_hook,
)

ShellHookRenderer = Callable[..., str]
ShellHookInstaller = Callable[..., list[str]]


def _cmd_shell_hook(
    args: argparse.Namespace,
    *,
    renderer: ShellHookRenderer = render_shell_hook,
) -> int:
    """Print shell code for automatic terminal arming and provider wrappers."""
    providers = tuple(args.provider) if args.provider else DEFAULT_PROVIDER_COMMANDS
    print(renderer(shell=args.shell, provider_commands=providers), end="")
    return 0


def _cmd_install_shell_hook(
    args: argparse.Namespace,
    *,
    installer: ShellHookInstaller = install_shell_hook,
) -> int:
    """Install the shell integration startup block."""
    try:
        lines = installer(shell=args.shell, synapse_bin=args.synapse_bin)
    except ValueError as exc:
        print(str(exc))
        return 2
    for line in lines:
        print(line)
    return 0


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register shell integration subcommands."""
    hook = subparsers.add_parser(
        "shell-hook",
        help="Print shell code that auto-arms terminals and wraps provider commands.",
    )
    hook.add_argument("--shell", default="bash", choices=("bash", "zsh"))
    hook.add_argument(
        "--provider",
        action="append",
        default=None,
        help=(
            "Provider command to wrap; repeatable. Defaults cover codex, claude, "
            "gemini, agent, ask, ollama."
        ),
    )
    hook.set_defaults(func=_cmd_shell_hook)

    install = subparsers.add_parser(
        "install-shell-hook",
        help="Install auto-arming shell integration into ~/.bashrc or ~/.zshrc.",
    )
    install.add_argument("--shell", default="auto", help="bash, zsh, or auto from $SHELL.")
    install.add_argument(
        "--synapse-bin",
        default="synapse",
        help="Synapse executable used by the startup block; defaults to PATH lookup.",
    )
    install.set_defaults(func=_cmd_install_shell_hook)
