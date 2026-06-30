# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for conversation modes and selection
"""Tests for :mod:`synapse_channel.participants.modes`."""

from __future__ import annotations

from synapse_channel.participants.modes import (
    MODE_POLICIES,
    ConversationMode,
    policy_for,
    select_mode,
)


def test_mode_values_are_stable_strings() -> None:
    assert ConversationMode.COLLOQUY.value == "colloquy"
    assert ConversationMode.ROUNDTABLE.value == "roundtable"
    assert ConversationMode.SYMPOSIUM.value == "symposium"


def test_every_mode_has_a_policy() -> None:
    for mode in ConversationMode:
        assert mode in MODE_POLICIES
        assert policy_for(mode) is MODE_POLICIES[mode]


def test_policies_distinguish_the_modes() -> None:
    colloquy = policy_for(ConversationMode.COLLOQUY)
    roundtable = policy_for(ConversationMode.ROUNDTABLE)
    symposium = policy_for(ConversationMode.SYMPOSIUM)
    # Colloquy goes deeper; only the symposium uses a moderator.
    assert colloquy.critique_rounds > roundtable.critique_rounds
    assert colloquy.uses_moderator is False
    assert roundtable.uses_moderator is False
    assert symposium.uses_moderator is True


def test_small_panel_selects_colloquy() -> None:
    assert select_mode(1) is ConversationMode.COLLOQUY
    assert select_mode(2) is ConversationMode.COLLOQUY


def test_medium_panel_without_moderator_selects_roundtable() -> None:
    assert select_mode(3) is ConversationMode.ROUNDTABLE
    assert select_mode(4) is ConversationMode.ROUNDTABLE


def test_medium_panel_with_moderator_selects_symposium() -> None:
    assert select_mode(3, moderator_available=True) is ConversationMode.SYMPOSIUM
    assert select_mode(4, moderator_available=True) is ConversationMode.SYMPOSIUM


def test_large_panel_selects_symposium_even_without_moderator() -> None:
    assert select_mode(5) is ConversationMode.SYMPOSIUM
    assert select_mode(9) is ConversationMode.SYMPOSIUM


def test_small_panel_ignores_moderator_availability() -> None:
    # A one- or two-party session is a colloquy regardless of a chair being on hand.
    assert select_mode(2, moderator_available=True) is ConversationMode.COLLOQUY
