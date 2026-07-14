# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — terminal-safe untrusted text rendering tests

from __future__ import annotations

from synapse_channel.terminal_text import terminal_chat_line, terminal_text


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
