# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Codex-named compatibility surface over the generic agent waker
"""Codex-named compatibility surface over :mod:`synapse_channel.agent_tmux`.

The tmux wake transport started Codex-specific and was generalised to any
terminal coding agent (Codex, Kimi K2, Claude Code, …) in
:mod:`synapse_channel.agent_tmux`. This module preserves the original
``CodexTmux*`` names and Codex defaults so existing importers keep working; new
code should use the generic ``AgentTmux*`` symbols directly.
"""

from __future__ import annotations

from synapse_channel.agent_tmux import (
    DEFAULT_AGENT_PANE_COMMANDS,
    DEFAULT_SUBMIT_DELAY,
    DEFAULT_WAIT_RETRY_BASE,
    DEFAULT_WAIT_RETRY_CAP,
    CommandRunner,
    RegistryRecord,
    Sleeper,
    _backoff_delay,
    agent_binary,
    build_wake_prompt,
    inject_wake,
    registry_path,
    start_session,
    status,
    wait_and_wake,
)
from synapse_channel.agent_tmux import (
    AgentTmuxConfig as CodexTmuxConfig,
)
from synapse_channel.agent_tmux import (
    AgentTmuxStatus as CodexTmuxStatus,
)
from synapse_channel.agent_tmux import (
    AgentTmuxWakeResult as CodexTmuxWakeResult,
)

CODEX_PANE_COMMANDS = DEFAULT_AGENT_PANE_COMMANDS
"""Backwards-compatible alias for :data:`agent_tmux.DEFAULT_AGENT_PANE_COMMANDS`."""

__all__ = [
    "CODEX_PANE_COMMANDS",
    "DEFAULT_SUBMIT_DELAY",
    "DEFAULT_WAIT_RETRY_BASE",
    "DEFAULT_WAIT_RETRY_CAP",
    "CodexTmuxConfig",
    "CodexTmuxStatus",
    "CodexTmuxWakeResult",
    "CommandRunner",
    "RegistryRecord",
    "Sleeper",
    "_backoff_delay",
    "agent_binary",
    "build_wake_prompt",
    "inject_wake",
    "registry_path",
    "start_session",
    "status",
    "wait_and_wake",
]
