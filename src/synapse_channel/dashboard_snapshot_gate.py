# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — per-dashboard snapshot identity serialization
"""Serialize short-lived snapshot fetches that share one hub identity."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Generic, TypeVar

SnapshotT = TypeVar("SnapshotT")


class DashboardSnapshotGate(Generic[SnapshotT]):
    """Serialize one dashboard server's snapshot fetch callback.

    A dashboard identity is a real hub participant and therefore may have only
    one live socket. Threaded HTTP reads can overlap while browser transports
    reconnect, so every server owns one gate around its short-lived fetch.

    Parameters
    ----------
    fetcher : collections.abc.Callable[[], SnapshotT]
        Complete synchronous snapshot operation guarded by the gate.
    """

    def __init__(self, fetcher: Callable[[], SnapshotT]) -> None:
        self._fetcher = fetcher
        self._lock = threading.Lock()

    def fetch(
        self,
        *,
        wait_timeout: float | None = None,
        fetcher: Callable[[], SnapshotT] | None = None,
    ) -> SnapshotT:
        """Fetch under the identity lease, optionally refusing a busy observer.

        Parameters
        ----------
        wait_timeout : float or None, optional
            Maximum seconds awaiting the identity; none preserves blocking reads.
        fetcher : callable or None, optional
            Same-identity fetch with a caller-specific response budget.

        Returns
        -------
        SnapshotT
            The chosen fetcher's result.

        Raises
        ------
        TimeoutError
            If another read retains the identity beyond the wait budget.
        """
        if not self._lock.acquire(timeout=-1 if wait_timeout is None else wait_timeout):
            raise TimeoutError("dashboard snapshot identity is busy")
        try:
            return (self._fetcher if fetcher is None else fetcher)()
        finally:
            self._lock.release()
