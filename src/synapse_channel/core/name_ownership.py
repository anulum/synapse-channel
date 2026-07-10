# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — hub-authoritative name-ownership lease table
"""Hub-authoritative ownership leases for agent names.

A name on the bus has exactly one owner. Before this table, the hub knew only
which *socket* currently held a name: the moment that socket dropped — a re-arm
gap, a hub-side eviction, a network blip — the name was free and the next
connection to claim it became it, silently. That is the squatting half of the
2026-07-10 identity P0: an accidental claimant inherited a name's directed
traffic simply by connecting first.

:class:`NameOwnership` makes the hub the authority on who a name belongs to
*across* connections. The first opt-in claimant of a free name is granted an
opaque lease token (returned once, stored only as a SHA-256 digest); while the
lease is live, any claim on that name must present the token or it is refused —
whether the owner is currently connected or not. The lease survives the owner's
disconnect for a bounded ``offline_ttl`` window, so a re-arming waiter re-takes
its own name and a stranger cannot squat it in the gap; a name whose owner
stays away past the window lapses back to first-come-first-owned, so a lost
token file degrades to today's behaviour instead of bricking the name.

The table is in-memory by design: it protects names across *reconnects*, the
window the P0 exploited. Durability across hub restarts is the trust-on-first-
use key pinning layer (`identity_binding`), which binds a name to a machine
keypair rather than a bearer token.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from collections.abc import Callable

from synapse_channel.core.numeric_coercion import safe_float

DEFAULT_LEASE_OFFLINE_TTL = 3600.0
"""Seconds a name's ownership lease outlives its holder's disconnect.

Long enough to cover a waiter's re-arm gap and an agent's long working turn;
short enough that a genuinely lost lease token self-heals within the hour
instead of locking the name until a hub restart.
"""


def _digest(token: str) -> str:
    """Return the hex SHA-256 digest of a lease token.

    Parameters
    ----------
    token : str
        The plaintext lease token.

    Returns
    -------
    str
        The token's SHA-256 hex digest, the only form the table stores.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class NameOwnership:
    """Own the hub's name→lease table and its offline-expiry policy.

    Parameters
    ----------
    clock : Callable[[], float]
        Monotonic time source shared with the hub, read when a holder goes
        offline and when a lease's lapse is evaluated. Injected so expiry is
        deterministic under test.
    offline_ttl : float, optional
        Seconds a lease survives after its holder disconnects. While the
        holder is connected the lease never lapses. ``0`` collapses the lease
        to the connection's lifetime — ownership protection only while online.
        Negative values are clamped to ``0``. Defaults to
        :data:`DEFAULT_LEASE_OFFLINE_TTL`.

    Notes
    -----
    Tokens are minted with :func:`secrets.token_urlsafe` and returned to the
    caller exactly once, from :meth:`grant`; the table keeps only SHA-256
    digests and compares them with :func:`hmac.compare_digest`, so neither a
    log line, a debugger ``repr``, nor a timing side channel recovers a live
    token from the hub.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], float],
        offline_ttl: float = DEFAULT_LEASE_OFFLINE_TTL,
    ) -> None:
        self.offline_ttl = max(safe_float(offline_ttl, default=DEFAULT_LEASE_OFFLINE_TTL), 0.0)
        self._clock = clock
        self._token_digests: dict[str, str] = {}
        self._offline_since: dict[str, float] = {}

    def grant(self, name: str) -> str:
        """Mint a fresh lease for ``name`` and return its token — the only time.

        Any previous lease on the name is replaced, so a post-lapse re-grant
        invalidates a stale token a former owner might still hold. The new
        owner counts as online until :meth:`mark_offline` says otherwise.

        Parameters
        ----------
        name : str
            The agent name the lease binds.

        Returns
        -------
        str
            The plaintext lease token. The table keeps only its digest; a
            caller that loses this value cannot recover it and waits out the
            offline window instead.
        """
        token = secrets.token_urlsafe(32)
        self._token_digests[name] = _digest(token)
        self._offline_since.pop(name, None)
        return token

    def matches(self, name: str, token: str) -> bool:
        """Return whether ``token`` is the live lease for ``name``.

        Parameters
        ----------
        name : str
            The claimed agent name.
        token : str
            The presented lease token; an empty string never matches.

        Returns
        -------
        bool
            ``True`` only when the name holds a live (non-lapsed) lease and
            the presented token's digest equals the stored one. Compared with
            :func:`hmac.compare_digest`, so the check leaks no timing signal.
        """
        if not token or not self.is_leased(name):
            return False
        return hmac.compare_digest(_digest(token), self._token_digests[name])

    def is_leased(self, name: str) -> bool:
        """Return whether ``name`` holds a live ownership lease.

        Lapse is evaluated lazily here rather than by a background sweep: a
        lease whose holder has been offline for at least ``offline_ttl``
        seconds is released on the spot and reported unleased, so the table
        needs no timer task and never grows past the set of names claimed
        since the last lapse.

        Parameters
        ----------
        name : str
            The agent name to look up.

        Returns
        -------
        bool
            ``True`` while the lease is live — its holder is connected, or
            disconnected for less than ``offline_ttl`` seconds.
        """
        if name not in self._token_digests:
            return False
        since = self._offline_since.get(name)
        if since is not None and self._clock() - since >= self.offline_ttl:
            self.release(name)
            return False
        return True

    def mark_online(self, name: str) -> None:
        """Record that ``name``'s holder is connected, freezing lease expiry.

        Parameters
        ----------
        name : str
            The agent name that just bound a socket. A name with no lease is
            a no-op, so the call is safe on every successful bind.
        """
        self._offline_since.pop(name, None)

    def mark_offline(self, name: str) -> None:
        """Start ``name``'s offline-expiry window at the current clock.

        Parameters
        ----------
        name : str
            The agent name whose active socket just dropped. A name with no
            lease is a no-op. A repeated call without an intervening
            :meth:`mark_online` keeps the *earliest* stamp — the expiry
            window runs from the first disconnect, never restarts.
        """
        if name in self._token_digests:
            self._offline_since.setdefault(name, self._clock())

    def release(self, name: str) -> None:
        """Drop ``name``'s lease entirely, returning it to first-come-first-owned.

        Parameters
        ----------
        name : str
            The agent name to release. Unknown names are a no-op.
        """
        self._token_digests.pop(name, None)
        self._offline_since.pop(name, None)
