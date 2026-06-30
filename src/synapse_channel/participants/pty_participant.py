# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — PTY-channel participant that wakes a tmux-paned peer over the bus
"""Present a tmux-paned terminal agent as a uniform bus participant.

The ``PTY`` channel is the last-resort driver: the peer is a long-lived terminal coding agent
(Codex, Kimi, Claude Code) reading from a tmux pane, which does not re-engage on a bus message
by itself. This participant bridges that gap by composing two existing pieces — it relays the
turn to the peer over the bus with :func:`~synapse_channel.participants.turn_relay.relay_turn`,
and supplies the relay's wake hook by injecting the **fixed, payload-free** wake prompt into the
peer's pane with :mod:`synapse_channel.agent_tmux`. The task itself travels on the bus as typed
data; only the routing nudge touches the terminal, so a remote sender can never inject keystrokes
— the anti-injection invariant the tmux transport already enforces.

A peer running the turn responder answers with a typed result; a peer without one still answers
through the relay's degraded free-text fallback. The seat fronts exactly the peer named by
:attr:`~synapse_channel.agent_tmux.AgentTmuxConfig.identity`, so its
:attr:`PtyParticipant.identity` is that peer's bus identity and the relay both addresses the
request to it and matches the reply by it. The relay connects under a separate ``sender_identity``
(it cannot listen as the peer it is talking to). As with every participant, an unreachable hub or
a silent peer becomes an error result rather than a raised exception.
"""

from __future__ import annotations

import asyncio
import shutil

# The tmux wake is driven through agent_tmux's injected subprocess runner; this module never
# spawns a process directly.
import subprocess  # nosec B404
from typing import TYPE_CHECKING

from synapse_channel.agent_tmux import (
    CommandRunner,
    agent_binary,
    inject_wake,
    start_session,
)
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.participants.participant import (
    ParticipantChannel,
    ParticipantHealth,
)
from synapse_channel.participants.turn_relay import RelaySettings, relay_turn

if TYPE_CHECKING:
    from synapse_channel.agent_tmux import AgentTmuxConfig
    from synapse_channel.cli_messaging_types import AgentFactory
    from synapse_channel.participants.envelope import TurnRequest, TurnResult


class PtyParticipant:
    """A tmux-paned terminal agent driven over the bus as a uniform participant.

    Parameters
    ----------
    config : AgentTmuxConfig
        The wake target: the peer's bus identity, its tmux session, and the binaries used to
        start and probe it. ``config.identity`` is the peer this seat fronts and the relay's
        target.
    sender_identity : str
        Bus identity the relay connects under to publish the turn and listen for the reply. It
        must differ from ``config.identity``, since the relay cannot listen as the peer it
        addresses.
    settings : RelaySettings or None, optional
        Relay connection and timing knobs; defaults to :class:`RelaySettings`.
    agent_factory : AgentFactory, optional
        Factory for the relay's bus client; injectable so tests drive a turn without a hub.
    tmux_runner : CommandRunner, optional
        Subprocess runner for the tmux wake; injectable so tests assert the pane injection
        without a real tmux.
    ensure_session : bool, optional
        When true (the default), the wake hook starts the tmux session if it is missing before
        injecting the prompt, so a peer whose pane has not been launched yet is brought up.
    """

    def __init__(
        self,
        *,
        config: AgentTmuxConfig,
        sender_identity: str,
        settings: RelaySettings | None = None,
        agent_factory: AgentFactory = SynapseAgent,
        tmux_runner: CommandRunner = subprocess.run,
        ensure_session: bool = True,
    ) -> None:
        self._config = config
        self._sender_identity = sender_identity
        self._settings = settings or RelaySettings()
        self._agent_factory = agent_factory
        self._tmux_runner = tmux_runner
        self._ensure_session = ensure_session

    @property
    def identity(self) -> str:
        """Return the bus identity of the peer this seat fronts (the relay target)."""
        return self._config.identity

    @property
    def channel(self) -> ParticipantChannel:
        """Return :attr:`ParticipantChannel.PTY`."""
        return ParticipantChannel.PTY

    def health(self) -> ParticipantHealth:
        """Report whether the tmux and agent binaries resolve on ``PATH``.

        Returns
        -------
        ParticipantHealth
            ``available`` is true when both the configured ``tmux`` binary and the peer's launch
            binary are found. This probes only that the wake transport could run; whether the
            peer's pane is live and answering surfaces as an error turn instead.
        """
        tmux_path = shutil.which(self._config.tmux_bin)
        binary = agent_binary(self._config)
        agent_path = shutil.which(binary) if binary else None
        available = tmux_path is not None and agent_path is not None
        if available:
            detail = f"tmux at {tmux_path}, agent {binary!r} at {agent_path}"
        elif tmux_path is None:
            detail = f"tmux binary {self._config.tmux_bin!r} not found on PATH"
        else:
            detail = f"agent binary {binary!r} not found on PATH"
        return ParticipantHealth(
            identity=self.identity,
            channel=ParticipantChannel.PTY,
            available=available,
            detail=detail,
        )

    async def take_turn(self, request: TurnRequest) -> TurnResult:
        """Relay one turn to the paned peer, waking it via tmux injection.

        Parameters
        ----------
        request : TurnRequest
            The turn to relay; its ``topic_id`` correlates the peer's reply.

        Returns
        -------
        TurnResult
            The peer's structured result, a degraded free-text result, or an error result when
            the hub is unreachable or the peer never replies. A provider failure is never raised.
        """

        async def wake() -> None:
            # Bring the pane up first when asked, then nudge it. Both calls are blocking tmux
            # subprocess work, so they run off the event loop in a worker thread.
            if self._ensure_session:
                await asyncio.to_thread(start_session, self._config, runner=self._tmux_runner)
            await asyncio.to_thread(inject_wake, self._config, runner=self._tmux_runner)

        return await relay_turn(
            request,
            target=self._config.identity,
            participant=self._config.identity,
            channel=ParticipantChannel.PTY,
            sender_identity=self._sender_identity,
            wake=wake,
            settings=self._settings,
            agent_factory=self._agent_factory,
        )
