# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — fail-closed JetBrains X11 window selection
"""Lock the top-level X11 parent invariant used by the real IDEA driver."""

from __future__ import annotations

from e2e.opencode_editors.jetbrains_client import _window_parentage, _xprop_window_id


def test_xwininfo_parentage_distinguishes_dialog_from_content_child() -> None:
    dialog = """
  Root window id: 0x1ff (the root window) (has no name)
  Parent window id: 0x1ff (the root window) (has no name)
     2 children:
"""
    content = """
  Root window id: 0x1ff (the root window) (has no name)
  Parent window id: 0x200051 "Data Sharing"
     0 children.
"""

    assert _window_parentage(dialog) == ("0x1ff", "0x1ff")
    assert _window_parentage(content) == ("0x1ff", "0x200051")


def test_xwininfo_parentage_fails_closed_on_missing_fields() -> None:
    assert _window_parentage("") == (None, None)
    assert _window_parentage("Root window id:") == (None, None)


def test_xprop_transient_parent_parser_accepts_only_a_window_id() -> None:
    result = "WM_TRANSIENT_FOR(WINDOW): window id # 0x40006e\n"

    assert _xprop_window_id(result) == 0x40006E
    assert _xprop_window_id("WM_TRANSIENT_FOR:  not found.\n") is None
    assert _xprop_window_id("WM_TRANSIENT_FOR(WINDOW): window id # invalid\n") is None
