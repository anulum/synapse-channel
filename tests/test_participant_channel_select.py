# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for provider channel selection
"""Tests for :mod:`synapse_channel.participants.channel_select`.

The selector encodes the ``MCP > HEADLESS > PTY`` order. The suite asserts each rung wins when it
is the best available, that headless only counts when its binary resolves, and that a provider
with no usable channel selects ``None``.
"""

from __future__ import annotations

from synapse_channel.participants.channel_select import (
    ProviderCapabilities,
    select_channel,
)
from synapse_channel.participants.participant import ParticipantChannel


def _present(name: str) -> str | None:
    return f"/usr/bin/{name}"


def _absent(name: str) -> str | None:
    return None


def test_mcp_wins_over_every_other_channel() -> None:
    caps = ProviderCapabilities(mcp_reachable=True, headless_binary="claude", pty_session=True)
    assert select_channel(caps, which=_present) is ParticipantChannel.MCP


def test_headless_chosen_when_binary_resolves_and_no_mcp() -> None:
    caps = ProviderCapabilities(headless_binary="ollama", pty_session=True)
    assert select_channel(caps, which=_present) is ParticipantChannel.HEADLESS


def test_headless_skipped_when_binary_absent_falls_through_to_pty() -> None:
    caps = ProviderCapabilities(headless_binary="ghost", pty_session=True)
    assert select_channel(caps, which=_absent) is ParticipantChannel.PTY


def test_empty_headless_binary_is_not_headless() -> None:
    # An empty binary name must not be passed to the resolver as a usable rung.
    caps = ProviderCapabilities(headless_binary="", pty_session=True)
    assert select_channel(caps, which=_present) is ParticipantChannel.PTY


def test_pty_chosen_when_only_a_session_is_configured() -> None:
    caps = ProviderCapabilities(pty_session=True)
    assert select_channel(caps, which=_absent) is ParticipantChannel.PTY


def test_no_channel_available_returns_none() -> None:
    caps = ProviderCapabilities(headless_binary="ghost")
    assert select_channel(caps, which=_absent) is None


def test_defaults_describe_an_undrivable_provider() -> None:
    assert select_channel(ProviderCapabilities(), which=_absent) is None
