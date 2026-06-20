# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — bounded idempotency cache for at-most-once mutations
"""Bounded idempotency cache that makes retried mutations a no-op.

When an agent reconnects it may resend a claim or release it is unsure landed.
Without protection, the retry would apply twice — the exact duplicated-claim bug
the bus exists to prevent. This cache remembers the *response* the hub produced
for each client-supplied idempotency key; a repeated key replays that response to
the caller instead of mutating state again.

The cache is bounded (least-recently-used eviction) because keys are only needed
across a short reconnect window, not forever. It is therefore honest about its
scope: it deduplicates within the retained window, not across an unbounded
history or a hub restart.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any

DEFAULT_MAX_KEYS = 1024


class IdempotencyCache:
    """A bounded key-to-response cache with least-recently-used eviction.

    Parameters
    ----------
    max_keys : int, optional
        Maximum number of keys retained; the least-recently-used key is evicted
        once the limit is exceeded. Clamped up to ``1``. Defaults to
        :data:`DEFAULT_MAX_KEYS`.
    """

    def __init__(self, max_keys: int = DEFAULT_MAX_KEYS) -> None:
        self.max_keys = max(int(max_keys), 1)
        self._store: OrderedDict[str, dict[str, Any]] = OrderedDict()

    def __contains__(self, key: str) -> bool:
        """Return whether ``key`` has a remembered response."""
        return key in self._store

    def __len__(self) -> int:
        """Return the number of remembered keys."""
        return len(self._store)

    def get(self, key: str) -> dict[str, Any] | None:
        """Return the remembered response for ``key``, marking it most-recent.

        Parameters
        ----------
        key : str
            The idempotency key to look up.

        Returns
        -------
        dict[str, Any] or None
            The cached response, or ``None`` when the key is unknown.
        """
        if key not in self._store:
            return None
        self._store.move_to_end(key)
        return self._store[key]

    def put(self, key: str, response: dict[str, Any]) -> None:
        """Remember ``response`` under ``key``, evicting the oldest if full.

        Parameters
        ----------
        key : str
            The idempotency key.
        response : dict[str, Any]
            The response message to replay on a future duplicate.
        """
        if key in self._store:
            self._store.move_to_end(key)
        self._store[key] = response
        while len(self._store) > self.max_keys:
            self._store.popitem(last=False)
