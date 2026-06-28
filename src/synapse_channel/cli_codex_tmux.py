# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Codex-named alias of the generic agent-tmux wake CLI
"""Codex-named ``synapse codex-tmux`` alias of the generic agent-tmux wake CLI.

The wake transport CLI was generalised to any terminal coding agent in
:mod:`synapse_channel.cli_agent_tmux`. This module keeps the original
``codex-tmux`` subcommand (and its ``--codex-command`` flag) working by
registering the same command handler with Codex naming and defaults.
"""

from __future__ import annotations

import argparse

from synapse_channel.cli_agent_tmux import _cmd_agent_tmux, register_parsers

__all__ = ["_cmd_agent_tmux", "add_parsers"]


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``codex-tmux`` subparser group (Codex-named alias)."""
    register_parsers(
        subparsers,
        command_name="codex-tmux",
        command_help="Wake an existing Codex tmux session from Synapse messages.",
        command_flag="--codex-command",
        command_default="codex",
        command_flag_help=(
            "Shell-style command used when starting the tmux session; defaults to codex."
        ),
    )
