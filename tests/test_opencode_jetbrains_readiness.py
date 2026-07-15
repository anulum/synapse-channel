# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — JetBrains ACP readiness event regressions
"""Exercise both valid pinned IDEA readiness completion orders."""

from __future__ import annotations

import pytest

from e2e.opencode_editors.jetbrains_readiness import prerequisite_then_all

_PREREQUISITE = "plugins ready"
_COMPLETIONS = ("session started", "commands available")


@pytest.mark.parametrize(
    "contents",
    (
        "plugins ready\nsession started\ncommands available\n",
        "plugins ready\ncommands available\nsession started\n",
    ),
)
def test_readiness_accepts_both_completion_orders(contents: str) -> None:
    """Accept independently scheduled completions after the plugin gate."""
    assert prerequisite_then_all(contents, _PREREQUISITE, _COMPLETIONS)


@pytest.mark.parametrize(
    "contents",
    (
        "",
        "session started\ncommands available\n",
        "plugins ready\nsession started\n",
        "commands available\nplugins ready\nsession started\n",
    ),
)
def test_readiness_refuses_missing_or_early_events(contents: str) -> None:
    """Refuse absent events and completions seen only before the gate."""
    assert not prerequisite_then_all(contents, _PREREQUISITE, _COMPLETIONS)


@pytest.mark.parametrize(
    ("prerequisite", "completions", "message"),
    (
        ("", ("done",), "prerequisite"),
        ("ready", (), "completions"),
        ("ready", ("",), "completions"),
        ("ready", ("ready",), "distinct"),
        ("ready", ("done", "done"), "distinct"),
    ),
)
def test_readiness_refuses_ambiguous_contracts(
    prerequisite: str,
    completions: tuple[str, ...],
    message: str,
) -> None:
    """Reject contracts whose event identities cannot be distinguished."""
    with pytest.raises(ValueError, match=message):
        prerequisite_then_all("ready\ndone\n", prerequisite, completions)
