# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — messaging CLI compatibility surface
"""Compatibility exports for the ``send``, ``wait``, and ``listen`` commands."""

from __future__ import annotations

from synapse_channel.cli_messaging_listen import _cmd_listen, _listen
from synapse_channel.cli_messaging_parsers import add_parsers
from synapse_channel.cli_messaging_send import _cmd_send, _send
from synapse_channel.cli_messaging_types import (
    AgentFactory,
    AsyncRunner,
    JitterFunction,
    ListenRunner,
)
from synapse_channel.cli_messaging_wait import _cmd_wait, _wait

__all__ = [
    "AgentFactory",
    "AsyncRunner",
    "JitterFunction",
    "ListenRunner",
    "_cmd_listen",
    "_cmd_send",
    "_cmd_wait",
    "_listen",
    "_send",
    "_wait",
    "add_parsers",
]
