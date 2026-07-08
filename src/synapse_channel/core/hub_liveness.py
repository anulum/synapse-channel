# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — liveness query view: combine the reaction store with the live roster
"""Liveness query view over the reaction store and the live roster.

:class:`RecipientLiveness` (in :mod:`synapse_channel.core.agent_liveness`) is the raw
reaction *store* — when each agent last did something that is not a keepalive. This
module is the *policy* layer that combines that store with the two other liveness
signals the hub already has — the presence of an armed ``-rx`` waiter sidecar and the
freshness of its keepalives (``state.last_seen``) — to answer the questions the
sender warning and the ``/who`` roster ask: is this present agent reachable in
practice, and which agents have no live waiter?

It reads the live client registry and last-seen map through injected references
rather than holding a back-reference to the hub, the same callback-injection the
hub's other collaborators (broadcaster, ingress, identity gate) use, so the routing
core does not grow another responsibility and this policy is unit-testable in
isolation.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from synapse_channel.core.agent_liveness import WAITER_SUFFIX, RecipientLiveness


class HubLivenessView:
    """Answer reachability questions from the reaction store plus the live roster.

    Parameters
    ----------
    reactions : RecipientLiveness
        The reaction store the hub already writes on registration and every genuine
        (non-heartbeat) frame. Read here; never written.
    enabled : bool
        Whether the stale-recipient warning is on. When off, the two aggregate
        queries return empty so the open hub carries no liveness surface.
    waiter_window_seconds : float
        How long a waiter's ``-rx`` sidecar may go without a keepalive before it stops
        counting as live. Clamped non-negative.
    online_agents : Callable[[], list[str]]
        Returns the current sorted roster of registered agent names, read fresh.
    agent_sockets : Mapping[str, Any]
        The live name→socket registry, read fresh to test ``-rx`` sidecar presence.
    last_seen : Mapping[str, float]
        The hub's per-identity wall-clock last-activity map (``state.last_seen``),
        refreshed on every frame including a waiter's keepalive.
    clock : Callable[[], float]
        The hub's monotonic clock, used for reaction staleness. Waiter freshness is a
        separate axis read against wall-clock ``time.time()`` (the clock ``last_seen``
        is stamped with).
    """

    def __init__(
        self,
        reactions: RecipientLiveness,
        *,
        enabled: bool,
        waiter_window_seconds: float,
        online_agents: Callable[[], list[str]],
        agent_sockets: Mapping[str, Any],
        last_seen: Mapping[str, float],
        clock: Callable[[], float],
    ) -> None:
        self._reactions = reactions
        self._enabled = bool(enabled)
        self._waiter_window = max(float(waiter_window_seconds), 0.0)
        self._online_agents = online_agents
        self._agent_sockets = agent_sockets
        self._last_seen = last_seen
        self._clock = clock

    def has_live_waiter(self, name: str) -> bool:
        """Return whether a *live* ``-rx`` waiter sidecar is armed for ``name``.

        Presence of the ``<name>-rx`` socket is not enough: a hung or exiting waiter
        can hold the socket for the ~ping-reap window after its loop stopped, falsely
        vouching for its agent. A live waiter refreshes the hub's last-seen for its
        socket on every keepalive, so this requires the sidecar to be both connected
        *and* seen within the waiter window. ``last_seen`` is wall-clock, so it is
        read against ``time.time()`` — a separate axis from the monotonic reaction
        clock.
        """
        rx = f"{name}{WAITER_SUFFIX}"
        if rx not in self._agent_sockets:
            return False
        seen = self._last_seen.get(rx)
        return seen is not None and time.time() - seen <= self._waiter_window

    def recipients_without_live_waiter(self, recipients: Iterable[str]) -> tuple[str, ...]:
        """Return present recipients that are neither waiter-armed nor recently reacting.

        A directed message reaches only online agents, but online is not the same as
        reachable-in-practice. This returns the subset of ``recipients`` a sender
        should be warned about — each is present but has *no independent proof of
        liveness*: no live ``-rx`` waiter (see :meth:`has_live_waiter`) is armed for
        it, and it has not produced a genuine reaction within the reaction window. An
        agent with either proof is omitted, so an armed-but-idle agent (reachable,
        just quiet) and an actively-reacting one are never flagged. Empty when the
        warning is disabled.
        """
        if not self._enabled:
            return ()
        now = self._clock()
        return tuple(
            name
            for name in recipients
            if not self.has_live_waiter(name) and self._reactions.is_stale(name, now)
        )

    def roster_liveness(self) -> dict[str, dict[str, Any]]:
        """Return a per-agent liveness annotation for the ``/who`` roster.

        For each online *agent* (a ``-rx`` waiter sidecar is presence plumbing, not an
        agent, and is skipped) this reports whether it is *proven live* — a live
        ``-rx`` waiter is armed for it, or it reacted within the reaction window —
        whether it has that live waiter, and how long ago it last reacted (``None`` if
        never). A present agent that is not proven live is the "online but deaf" case
        a roster should surface distinctly. Empty when the warning is disabled.
        """
        if not self._enabled:
            return {}
        now = self._clock()
        annotation: dict[str, dict[str, Any]] = {}
        for name in self._online_agents():
            if name.endswith(WAITER_SUFFIX):
                continue
            live_waiter = self.has_live_waiter(name)
            last = self._reactions.last_reaction_at(name)
            annotation[name] = {
                "proven_live": live_waiter or not self._reactions.is_stale(name, now),
                "has_live_waiter": live_waiter,
                "last_reaction_age": None if last is None else max(0.0, now - last),
            }
        return annotation
