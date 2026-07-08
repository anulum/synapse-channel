# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — recipient liveness: distinguish a reacting agent from a deaf one
"""Recipient liveness — tell a *reacting* agent apart from a *connected-but-deaf* one.

Presence is not liveness. A socket can stay in the roster — keepalive heartbeats
flowing, ``/who`` showing it online — while the agent behind it never reacts to a
directed message, because the terminal driving it is not being woken. The sender
sees a reachable recipient and waits on a reply that never comes.

:class:`RecipientLiveness` records the last moment an agent produced a *genuine
reaction* — any frame that is not a keepalive heartbeat — and its registration
moment as a grace seed, so a freshly connected agent is given a window to prove
itself before it is judged. An agent whose last reaction is older than that window
is *stale*: present, but not proven wake-capable. A hub pairs this with a second,
independent proof — a live ``-rx`` waiter sidecar — before it warns a sender, so an
armed-but-idle agent (which is genuinely reachable, just quiet) is not flagged.

The store is opt-in from the hub's side: it is only written when the stale-recipient
warning is enabled, so the default open hub carries no extra per-frame state.
"""

from __future__ import annotations

DEFAULT_RECIPIENT_LIVENESS_WINDOW = 90.0
"""Seconds an agent may go without a genuine reaction before it is judged stale.

Long enough that a just-connected agent, or one legitimately quiet between turns,
is not flagged the instant it registers; short enough that an agent deaf for
minutes is surfaced to the next sender rather than discovered by hand hours later.
"""

WAITER_SUFFIX = "-rx"
"""Suffix of the receive-only wake-listener sidecar an armed waiter connects under.

A live ``<identity>-rx`` socket is independent evidence that ``<identity>`` is armed
to be woken, so a hub treats its presence as liveness even when the identity itself
has not reacted within the window. The kernel cannot import the feature-layer
``waiter_identity`` module (the package boundary keeps ``core`` from reaching up), so
the convention is restated here; it mirrors ``waiter_identity.WAITER_SUFFIX``, the
single definition the non-core layers share.
"""


class RecipientLiveness:
    """Track the last genuine reaction of each agent, to spot a present-but-deaf one.

    Parameters
    ----------
    window_seconds : float
        How long after its last reaction an agent stays judged live. Clamped to a
        non-negative value; a window of ``0`` makes every agent stale the instant
        after it reacts, which is only useful in tests.
    """

    def __init__(self, *, window_seconds: float = DEFAULT_RECIPIENT_LIVENESS_WINDOW) -> None:
        self._window = max(float(window_seconds), 0.0)
        self._last_reaction: dict[str, float] = {}

    @property
    def window_seconds(self) -> float:
        """The staleness window in seconds (non-negative)."""
        return self._window

    def touch(self, name: str, now: float) -> None:
        """Record that ``name`` produced a genuine reaction (or registered) at ``now``.

        Called both when an agent first registers — seeding the grace window so a
        fresh connection is not immediately judged deaf — and on every subsequent
        non-heartbeat frame, which is a genuine reaction that proves the agent acted.
        A keepalive heartbeat is deliberately never passed here: it proves the socket
        is open, not that the agent behind it is awake.
        """
        self._last_reaction[name] = now

    def forget(self, name: str) -> None:
        """Drop ``name``'s reaction record when its socket disconnects.

        Keeps the store bounded to currently connected identities; a stale entry for
        a departed name would never cause a false warning (only online recipients are
        ever checked) but is dropped for hygiene, the way the hub forgets an agent's
        capabilities and rate-limit bucket on the same disconnect.
        """
        self._last_reaction.pop(name, None)

    def last_reaction_at(self, name: str) -> float | None:
        """Return when ``name`` last reacted, or ``None`` if it has no record."""
        return self._last_reaction.get(name)

    def is_stale(self, name: str, now: float) -> bool:
        """Return whether ``name`` has not reacted within the window as of ``now``.

        An agent with no record at all is stale (unknown is not proven live). An
        agent whose last reaction is within the window is live; one older than the
        window is stale. A clock that appears to move backwards (``now`` before the
        recorded reaction) yields a non-positive age, which is never stale.
        """
        last = self._last_reaction.get(name)
        if last is None:
            return True
        return now - last > self._window
