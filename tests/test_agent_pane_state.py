# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — provider pane classification contracts
"""Preserve provider state and staged-hint decisions through public classifiers."""

from __future__ import annotations

import pytest

from synapse_channel.agent_pane_state import (
    pane_state,
    screen_is_safe_for_submit,
    staged_wake_was_consumed,
)
from synapse_channel.agent_tmux import build_wake_prompt

_HINT = build_wake_prompt("E2E/pane-classification")
_UPDATE = "Update available! 0.1 -> 0.2\nSkip until next version\nPress enter to continue\n› "


@pytest.mark.parametrize(
    ("provider", "capture", "expected"),
    [
        ("codex", "› ", "idle"),
        ("claude", "❯ ", "idle"),
        ("kimi", "> Ask", "idle"),
        ("grok", "› Type", "idle"),
        ("gemini", "Ask Gemini", "idle"),
        ("opencode", "Ask anything...", "idle"),
        ("codex", _UPDATE, "update-required"),
        ("codex", "thinking\n› ", "blocked"),
        ("claude", "Allow this tool?\n❯ ", "blocked"),
        ("codex", "1. Continue\n› ", "blocked"),
        ("codex", "session banner", "unknown"),
    ],
)
def test_provider_state_preserves_precedence(provider: str, capture: str, expected: str) -> None:
    """Busy, permission and update observations retain precedence over idle."""
    assert pane_state(provider, capture) == expected


@pytest.mark.parametrize(
    ("provider", "capture", "required", "expected"),
    [
        ("codex", _UPDATE, None, False),
        ("claude", "permission\n❯ ", _HINT, False),
        ("codex", "session banner", None, False),
        ("opencode", "BUILD model ctrl+p cmd", None, False),
        ("opencode", "BUILD model ctrl+p cmd", _HINT, False),
        ("opencode", _HINT + "\nBUILD model ctrl+o cmd", _HINT, False),
        ("opencode", _HINT + "\nBUILD model ctrl+p cmd", _HINT, True),
        ("codex", "› ", None, True),
        ("codex", "› owner's draft", _HINT, False),
        ("codex", "› " + _HINT, _HINT, True),
        ("codex", "› " + _HINT.replace(" ", "\n"), _HINT, True),
    ],
)
def test_submit_readiness_preserves_exact_staged_hint(
    provider: str, capture: str, required: str | None, expected: bool
) -> None:
    """Readiness keeps payload-free reasons and requires the entire hint."""
    ready, reason = screen_is_safe_for_submit(provider, capture, required_text=required)
    assert ready is expected
    assert reason
    assert _HINT not in reason
    assert "owner's draft" not in reason


@pytest.mark.parametrize(
    ("provider", "capture", "expected"),
    [
        ("codex", "› ", True),
        ("codex", "› " + _HINT, False),
        ("codex", "› " + _HINT + "\n› ", True),
        ("claude", "❯ " + _HINT + "\n❯ ", True),
        ("opencode", _HINT + "\nBUILD model ctrl+p cmd", False),
        ("opencode", "› " + _HINT + "\nBUILD esc interrupt ctrl+p cmd", True),
        ("opencode", "draft " + _HINT + "\nBUILD esc interrupt ctrl+p cmd", False),
        ("opencode", "› " + _HINT + "\nBUILD model ctrl+p cmd", False),
        ("opencode", "› " + _HINT + "\nAsk anything...", True),
        (
            "opencode",
            _HINT + "\nAsk anything...\n" + _HINT + "\nBUILD model ctrl+p cmd",
            False,
        ),
    ],
)
def test_staged_hint_observation_uses_last_rendered_occurrence(
    provider: str, capture: str, expected: bool
) -> None:
    """A previous idle marker cannot settle a newer still-staged hint."""
    assert staged_wake_was_consumed(provider, capture, _HINT) is expected


def test_unsupported_provider_keeps_existing_lookup_error() -> None:
    """An unsupported provider is not silently assigned a supported profile."""
    with pytest.raises(KeyError, match="unsupported"):
        pane_state("unsupported", "session banner")
    with pytest.raises(KeyError, match="unsupported"):
        screen_is_safe_for_submit("unsupported", "session banner")
    with pytest.raises(KeyError, match="unsupported"):
        staged_wake_was_consumed("unsupported", _HINT, _HINT)
