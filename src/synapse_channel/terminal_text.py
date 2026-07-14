# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — compatibility facade for kernel terminal rendering
"""Compatibility facade for :mod:`synapse_channel.core.terminal_text`."""

from __future__ import annotations

from synapse_channel.core.terminal_text import (
    shell_command_arg,
    shell_long_option,
    terminal_chat_line,
    terminal_text,
)

__all__ = [
    "shell_command_arg",
    "shell_long_option",
    "terminal_chat_line",
    "terminal_text",
]
