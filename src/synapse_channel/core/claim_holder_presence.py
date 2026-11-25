# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — claims of disconnected holders: visible, then released after the lease window
"""What happens to a claim when its holder disconnects.

A claim survives its holder's disconnect: a crash, a reboot or a long reconnect gap must
not lose the work it protects. It must not survive forever either, or a holder that never
comes back leaves an orphan lock until the lease TTL. The hub therefore applies the same
window it already uses for name ownership (:data:`~synapse_channel.core.name_ownership.
DEFAULT_LEASE_OFFLINE_TTL`, ``lease_offline_ttl``):

* while the holder is offline for less than the window, its claims stand and state views
  show them as held by an offline holder, with how long it has been away;
* once the holder has been offline for the whole window, the next claim attempt on the hub
  releases every claim that holder still has, journals each release like an ordinary one
  (so replay after a restart agrees), and announces it; the waiting claimant then competes
  normally.

A claim forwarded by a peer hub is held by a claimant connected to that peer, never to this
hub, so its holder here is the forwarding peer (:attr:`~synapse_channel.core.state_models.
TaskClaim.via_peer`, see :func:`liveness_subject`): the claim stands while the peer is
connected, and the window runs from the peer's departure.

The lease TTL stays the upper bound either way. Time is measured with the hub's monotonic
clock, so after a hub restart a restored claim's holder counts as offline from the moment
the hub started.
"""

from __future__ import annotations

from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from synapse_channel.core.journal import record_release
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.state import SynapseState
from synapse_channel.core.state_models import TaskClaim

if TYPE_CHECKING:
    from typing import Protocol

    from synapse_channel.core.handler_context import HandlerContext

    class ClaimHolderContext(HandlerContext, Protocol):
        """Capabilities consumed by claim holder presence handlers and their callees."""

        @property
        def claim_holders(self) -> ClaimHolderPresence:
            """Return the claim holders used by this handler family."""
            ...


HOLDER_OFFLINE_ACTOR = "hub:holder-offline"
"""Identity a release of an abandoned claim is attributed to."""


def liveness_subject(claim: TaskClaim) -> str:
    """Return the name whose connection keeps ``claim`` alive on this hub.

    Parameters
    ----------
    claim : TaskClaim
        A claim held on this hub.

    Returns
    -------
    str
        The forwarding peer for a claim a peer hub forwarded, whose claimant is never
        connected here; otherwise the claim's owner.
    """
    return claim.via_peer or claim.owner


@dataclass
class ClaimHolderPresence:
    """When each claim holder left, measured on the hub's monotonic clock.

    Parameters
    ----------
    clock : Callable[[], float]
        The hub's monotonic clock.
    started_at : float
        The clock value at hub start; a holder never seen leaving counts from here.
    window : float
        Seconds a holder may stay offline before its claims are released.
    """

    clock: Callable[[], float]
    started_at: float
    window: float
    _offline_since: dict[str, float] = field(default_factory=dict)

    def left(self, name: str) -> None:
        """Record that ``name`` disconnected; an earlier stamp is kept."""
        self._offline_since.setdefault(name, self.clock())

    def returned(self, name: str) -> None:
        """Record that ``name`` is connected again."""
        self._offline_since.pop(name, None)

    def left_if_holding(self, name: str, claims: Collection[TaskClaim]) -> None:
        """Record that ``name`` disconnected when a claim still lives by its connection.

        Parameters
        ----------
        name : str
            The name whose connection closed.
        claims : Collection[TaskClaim]
            The hub's current claims. A claim lives by ``name`` when ``name`` holds it, or when
            the peer hub ``name`` forwarded it (:func:`liveness_subject`).
        """
        if any(liveness_subject(claim) == name for claim in claims):
            self.left(name)

    def offline_seconds(self, name: str, *, online: bool, now: float) -> float | None:
        """Return how long ``name`` has been offline, or ``None`` while it is connected.

        Parameters
        ----------
        name : str
            A claim holder.
        online : bool
            Whether the holder currently has a bound socket.
        now : float
            The current monotonic clock value.

        Returns
        -------
        float or None
            Seconds offline (from the recorded departure, or from hub start when none was
            recorded), or ``None`` for a connected holder.
        """
        if online:
            return None
        return max(0.0, now - self._offline_since.get(name, self.started_at))


@dataclass(frozen=True)
class AbandonedRelease:
    """One claim released because its holder stayed offline for the whole window."""

    task_id: str
    owner: str
    offline_seconds: float
    via_peer: str = ""


def abandoned_claims(
    state: SynapseState,
    presence: ClaimHolderPresence,
    *,
    online: Collection[str],
    now: float,
) -> list[AbandonedRelease]:
    """Return the claims in ``state`` whose holders have been offline for the whole window.

    A forwarded claim's holder is its forwarding peer (:func:`liveness_subject`).

    Parameters
    ----------
    state : SynapseState
        The lease state to inspect.
    presence : ClaimHolderPresence
        Departure times of claim holders.
    online : Collection[str]
        Names with a bound socket.
    now : float
        The current monotonic clock value.

    Returns
    -------
    list[AbandonedRelease]
        Abandoned claims, ordered by task id.
    """
    found: list[AbandonedRelease] = []
    for task_id, claim in sorted(state.claims.items()):
        subject = liveness_subject(claim)
        away = presence.offline_seconds(subject, online=subject in online, now=now)
        if away is not None and away >= presence.window:
            found.append(
                AbandonedRelease(
                    task_id=task_id,
                    owner=claim.owner,
                    offline_seconds=away,
                    via_peer=claim.via_peer,
                )
            )
    return found


async def release_abandoned_claims(hub: ClaimHolderContext) -> list[AbandonedRelease]:
    """Release, journal and announce every claim of a holder offline past the window.

    Runs through the hub's serialized state actor, like any release, and selects the
    claims inside it, so it never races a concurrent claim or release. Called before a
    claim is decided; it returns at once when nothing is abandoned.

    Parameters
    ----------
    hub : ClaimHolderContext
        The hub whose claims are checked.

    Returns
    -------
    list[AbandonedRelease]
        The claims released by this call.
    """
    presence = hub.claim_holders
    online = set(hub.clients.agent_sockets)
    now = presence.clock()
    if not abandoned_claims(hub.state, presence, online=online, now=now):
        return []
    journal = hub.journal

    def mutate(state: SynapseState) -> list[AbandonedRelease]:
        released = abandoned_claims(state, presence, online=online, now=now)
        for item in released:
            state.force_release(item.task_id, by=HOLDER_OFFLINE_ACTOR)
        return released

    persist: Callable[[list[AbandonedRelease]], None] | None = None
    if journal is not None:
        store = journal

        def persist_releases(released: list[AbandonedRelease]) -> None:
            for item in released:
                record_release(store, item.task_id)

        persist = persist_releases

    released: list[AbandonedRelease] = await hub.state_mutations.run(
        hub.state, mutate, persist=persist
    )
    for item in released:
        hub.counters.claims_released_abandoned += 1
        holder = (
            f"holder {item.owner}, forwarded by peer hub {item.via_peer},"
            if item.via_peer
            else f"holder {item.owner}"
        )
        await hub.broadcast(
            hub.system(
                f"Claim '{item.task_id}' released: {holder} offline for "
                f"{item.offline_seconds:.0f}s, past the {presence.window:.0f}s lease window.",
                msg_type=MessageType.RELEASE_GRANTED,
                task_id=item.task_id,
                owner=item.owner,
                released_by=HOLDER_OFFLINE_ACTOR,
                holder_offline_seconds=round(item.offline_seconds, 3),
            )
        )
    return released
