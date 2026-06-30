# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — choose the best available channel to drive a provider
"""Choose the most robust channel available for driving one provider.

Each provider can be reached through more than one channel, and they are not equally reliable:
an in-session MCP tool call is the most dependable, a bus-owned headless invocation with
structured output is the robust default, and a tmux pane is the last resort. The selection order
is therefore ``MCP > HEADLESS > PTY`` — the ranking already declared on
:class:`~synapse_channel.participants.participant.ParticipantChannel`.

:func:`select_channel` makes that choice from a small :class:`ProviderCapabilities` descriptor:
whether the peer is reachable over MCP, the name of its headless binary (if any), and whether a
tmux session is configured for it. The headless rung is real only when its binary actually
resolves on ``PATH``; the resolver is injected so the decision is deterministic in tests. When no
channel is available the function returns ``None``, so a caller can report a provider as
undrivable rather than guess.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass

from synapse_channel.participants.participant import ParticipantChannel

PathResolver = Callable[[str], str | None]
"""Resolves a binary name to its path, or ``None`` when absent — :func:`shutil.which`'s shape."""


@dataclass(frozen=True)
class ProviderCapabilities:
    """What channels a provider can be driven through.

    Attributes
    ----------
    mcp_reachable : bool
        Whether the peer runs with the Synapse MCP tools and is already listening on the bus, so
        it can answer a relayed turn with no external nudge.
    headless_binary : str
        Name (or path) of the provider's headless CLI, e.g. ``"claude"`` or ``"ollama"``; empty
        when the provider has no headless driver. The headless channel counts only when this
        resolves on ``PATH``.
    pty_session : bool
        Whether a tmux session is configured for the provider, making the PTY fallback usable.
    """

    mcp_reachable: bool = False
    headless_binary: str = ""
    pty_session: bool = False


def select_channel(
    capabilities: ProviderCapabilities,
    *,
    which: PathResolver = shutil.which,
) -> ParticipantChannel | None:
    """Return the most robust available channel for a provider, or ``None``.

    The channels are tried in the ``MCP > HEADLESS > PTY`` order: an MCP-reachable peer wins; else
    a headless binary that resolves on ``PATH``; else a configured tmux session. When none apply
    the provider cannot be driven and ``None`` is returned.

    Parameters
    ----------
    capabilities : ProviderCapabilities
        The provider's reachable channels.
    which : PathResolver, optional
        Resolver used to confirm the headless binary is present; defaults to
        :func:`shutil.which` and is injected in tests for a deterministic decision.

    Returns
    -------
    ParticipantChannel or None
        The selected channel, or ``None`` when the provider exposes no usable channel.
    """
    if capabilities.mcp_reachable:
        return ParticipantChannel.MCP
    if capabilities.headless_binary and which(capabilities.headless_binary) is not None:
        return ParticipantChannel.HEADLESS
    if capabilities.pty_session:
        return ParticipantChannel.PTY
    return None
