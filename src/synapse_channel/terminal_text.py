# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — terminal-safe rendering for untrusted protocol text
"""Render untrusted protocol fields without terminal control sequences."""

from __future__ import annotations

import re
import shlex
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


def shell_command_arg(value: object) -> str:
    """Return one terminal-safe, POSIX-shell-safe command argument.

    This helper prevents shell metacharacter expansion in copyable operator
    commands. Callers must still close the receiving command's option parser:
    use :func:`shell_long_option` for long-option values or an explicit ``--``
    before positional arguments that may start with a dash.
    """
    return shlex.quote(terminal_text(value))


def shell_long_option(name: str, value: object) -> str:
    """Return a copyable ``--long-option=value`` shell word.

    Binding the value with ``=`` prevents a leading dash in the value from
    being reinterpreted as a separate option by the receiving CLI.

    Raises
    ------
    ValueError
        If ``name`` is not a bare GNU-style long option.
    """
    if re.fullmatch(r"--[A-Za-z0-9][A-Za-z0-9-]*", name) is None:
        raise ValueError("name must be a bare GNU-style long option")
    return f"{name}={shell_command_arg(value)}"
