# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — terminal-safe rendering for untrusted protocol text
"""Render untrusted protocol fields without terminal control sequences."""

from __future__ import annotations

import unicodedata


def terminal_text(value: object) -> str:
    """Return ``value`` as one line with every control made visible.

    Chat payloads, identities, and hub result details cross an untrusted wire
    boundary. Printing them verbatim would let ANSI, OSC, carriage-return, bidi,
    or newline controls alter terminal state or impersonate adjacent output.
    Normal text remains unchanged; controls and Unicode format/surrogate code
    points render as explicit escape notation.
    """
    rendered: list[str] = []
    for character in str(value):
        codepoint = ord(character)
        category = unicodedata.category(character)
        if character == "\n":
            rendered.append(r"\n")
        elif character == "\r":
            rendered.append(r"\r")
        elif character == "\t":
            rendered.append(r"\t")
        elif category in {"Cc", "Cf", "Cs"}:
            width = 2 if codepoint <= 0xFF else (4 if codepoint <= 0xFFFF else 8)
            prefix = "x" if width == 2 else ("u" if width == 4 else "U")
            rendered.append(f"\\{prefix}{codepoint:0{width}x}")
        else:
            rendered.append(character)
    return "".join(rendered)


def terminal_chat_line(sender: object, payload: object) -> str:
    """Return one terminal-safe ``sender: payload`` line."""
    return f"{terminal_text(sender)}: {terminal_text(payload)}"
