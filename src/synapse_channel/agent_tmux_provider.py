# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — provider resolution for the tmux wake transport
"""Which provider a pane command names, and the command Synapse launches."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from synapse_channel.agent_pane_state import (
    PROVIDER_IDLE_PATTERNS as _PROVIDER_IDLE_PATTERNS,
)
from synapse_channel.agent_tmux_types import AgentTmuxConfig

CODEX_MANAGED_UPDATE_CONFIG = "check_for_update_on_startup=false"
"""Codex override used when Synapse centrally manages provider launches."""

SEAT_HOLD_SUBCOMMAND = "seat-hold"
"""The ``synapse`` subcommand that runs an agent behind its seat holder."""


def provider_command(command: Sequence[str]) -> tuple[str, ...]:
    """Return the agent's own command inside a pane command.

    A seat restored behind its holder runs ``synapse seat-hold <options> --
    <agent>``, after an ``env -i NAME=value …`` prefix that gives it a clean
    environment. Its agent is what follows ``--``; the prefix's values and the
    holder's option values come first and must never decide the provider (an
    identity such as ``ops/claude-…``, or a path, can name any agent). Every
    other command is its own.

    Parameters
    ----------
    command : Sequence[str]
        The pane's command, as ``AgentTmuxConfig.agent_command`` holds it.

    Returns
    -------
    tuple[str, ...]
        The agent's argument vector.
    """
    parts = without_clean_environment(tuple(command))
    if parts[1:2] == (SEAT_HOLD_SUBCOMMAND,) and "--" in parts[2:]:
        return parts[parts.index("--", 2) + 1 :]
    return tuple(command)


def without_clean_environment(parts: tuple[str, ...]) -> tuple[str, ...]:
    """Return ``parts`` without a leading ``env -i NAME=value …`` prefix, if it has one."""
    if parts[:2] != ("env", "-i"):
        return parts
    index = 2
    while index < len(parts) and "=" in parts[index] and not parts[index].startswith("-"):
        index += 1
    return parts[index:]


def agent_binary(config: AgentTmuxConfig) -> str:
    """Return the agent binary name probed for in the pane's start command.

    Parameters
    ----------
    config : AgentTmuxConfig
        Wake target whose ``agent_command`` names the launch binary.

    Returns
    -------
    str
        The basename of the first launch token (e.g. ``codex`` or ``kimi``), or
        an empty string when ``agent_command`` is empty.
    """
    command = provider_command(config.agent_command)
    if not command:
        return ""
    return Path(command[0]).name


def _provider_family(config: AgentTmuxConfig) -> str | None:
    """Return the supported terminal provider named by the agent's own command."""
    return provider_family_of(config.agent_command)


def provider_family_of(command: Sequence[str]) -> str | None:
    """Return the supported provider named by the agent's own command in ``command``."""
    for token in provider_command(command):
        if token.startswith("-"):
            continue
        binary = Path(token).name.lower()
        for suffix in (".js", ".mjs", ".cjs", ".py"):
            if binary.endswith(suffix):
                binary = binary[: -len(suffix)]
        for provider in _PROVIDER_IDLE_PATTERNS:
            if binary == provider or binary.startswith(f"{provider}-"):
                return provider
    return None


def _has_codex_update_override(command: Sequence[str]) -> bool:
    """Return whether ``command`` explicitly configures Codex update checks."""
    return any("check_for_update_on_startup" in token.replace(" ", "") for token in command)


def _managed_agent_command(config: AgentTmuxConfig) -> tuple[str, ...]:
    """Return the provider command with centrally managed Codex update checks.

    Existing sessions are never rewritten or restarted. The override applies
    only when Synapse creates a new Codex tmux session, and an explicit owner
    override in ``agent_command`` always wins. Behind a seat holder the agent's
    own command is the tail after ``--``, so the override lands there, as the
    agent's argument.
    """
    command = config.agent_command
    agent = provider_command(command)
    if _provider_family(config) != "codex" or _has_codex_update_override(agent):
        return command
    return (*command, "--config", CODEX_MANAGED_UPDATE_CONFIG)
