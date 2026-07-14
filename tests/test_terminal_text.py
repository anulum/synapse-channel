# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — terminal-safe untrusted text rendering tests

from __future__ import annotations

import pytest

from synapse_channel.terminal_text import (
    shell_command_arg,
    shell_long_option,
    terminal_chat_line,
    terminal_text,
)


def test_terminal_text_preserves_plain_unicode() -> None:
    assert terminal_text("Arcane: bezpečné ✓") == "Arcane: bezpečné ✓"


def test_terminal_text_exposes_terminal_and_format_controls() -> None:
    hostile = "line1\nline2\r\t\x1b]52;c;YQ==\x07\u202etrick"

    rendered = terminal_text(hostile)

    assert rendered == r"line1\nline2\r\t\x1b]52;c;YQ==\x07\u202etrick"
    assert not any(
        character in rendered for character in ("\n", "\r", "\t", "\x1b", "\x07", "\u202e")
    )


def test_terminal_chat_line_neutralises_sender_and_payload() -> None:
    assert terminal_chat_line("peer\x1b", "one\ntwo") == r"peer\x1b: one\ntwo"


def test_shell_command_arg_neutralises_controls_and_shell_metacharacters() -> None:
    assert shell_command_arg("$(touch injected)\x1b]0;fake\x07") == (
        r"'$(touch injected)\x1b]0;fake\x07'"
    )


def test_shell_long_option_binds_leading_dash_and_empty_values() -> None:
    assert shell_long_option("--name", "--help") == "--name=--help"
    assert shell_long_option("--name", "") == "--name=''"


@pytest.mark.parametrize("name", ["name", "--", "--name=value", "--bad option", "--bad_option"])
def test_shell_long_option_rejects_non_bare_long_options(name: str) -> None:
    with pytest.raises(ValueError, match="bare GNU-style long option"):
        shell_long_option(name, "value")
