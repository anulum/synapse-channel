# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — compatibility imports for read-only query CLI commands
"""Compatibility import surface for read-only hub query CLI commands.

The implementation lives in focused modules:
``cli_query_transport`` for connect/request/poll cleanup,
``cli_query_rendering`` for stdout formatting, ``cli_query_commands`` for the
async command flows, and ``cli_query_parsers`` for argparse registration.
Existing imports from ``synapse_channel.cli_queries`` remain supported.
"""

from __future__ import annotations

from synapse_channel.cli_query_commands import (
    _board,
    _cmd_board,
    _cmd_dead_letters,
    _cmd_health,
    _cmd_manifest,
    _cmd_state,
    _cmd_who,
    _dead_letters,
    _health,
    _manifest,
    _state,
    _who,
)
from synapse_channel.cli_query_parsers import add_parsers
from synapse_channel.cli_query_rendering import (
    _print_board,
    _print_manifest,
    _render_dead_letters,
    _render_state,
    _render_who,
    _render_who_me,
)
from synapse_channel.cli_query_transport import (
    AgentFactory,
    _drop_message,
    _identity,
    _query_hub,
)

__all__ = [
    "AgentFactory",
    "_board",
    "_cmd_board",
    "_cmd_dead_letters",
    "_cmd_health",
    "_cmd_manifest",
    "_cmd_state",
    "_cmd_who",
    "_dead_letters",
    "_drop_message",
    "_health",
    "_identity",
    "_manifest",
    "_print_board",
    "_print_manifest",
    "_query_hub",
    "_render_dead_letters",
    "_render_state",
    "_render_who",
    "_render_who_me",
    "_state",
    "_who",
    "add_parsers",
]
