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

    def fetch(self) -> SnapshotT:
        """Run one fetch after the prior caller releases the shared identity."""
        with self._lock:
            return self._fetcher()
