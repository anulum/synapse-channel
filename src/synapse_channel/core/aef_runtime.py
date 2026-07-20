# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — opt-in live AEF outbox runtime
"""Drain the durable legacy-to-AEF outbox on a dedicated worker thread."""

from __future__ import annotations

import logging
import math
import threading
from collections.abc import Callable
from dataclasses import dataclass

from synapse_channel.core.aef_emission import AefReceiptLog
from synapse_channel.core.aef_legacy_mapping import AEF_MAPPED_EVENT_KINDS
from synapse_channel.core.aef_outbox import drain_aef_outbox
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.receipt_signing import ReceiptSigningKey

DEFAULT_AEF_DRAIN_INTERVAL_SECONDS = 1.0
DEFAULT_AEF_DRAIN_BATCH_SIZE = 100
DEFAULT_AEF_DRAIN_MAX_RECEIPTS = 1_000

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AefRuntimeConfig:
    """Immutable connection and cadence settings for one hub's AEF route."""

    db_path: str
    hub_id: str
    signing_key: ReceiptSigningKey
    db_key_file: str | None = None
    interval_seconds: float = DEFAULT_AEF_DRAIN_INTERVAL_SECONDS

    def __post_init__(self) -> None:
        """Reject incomplete identity/storage context and unsafe cadences."""
        if not self.db_path:
            raise ValueError("AEF runtime requires a durable database path")
        if not self.hub_id:
            raise ValueError("AEF runtime requires a stable hub id")
        interval = float(self.interval_seconds)
        if not math.isfinite(interval) or interval <= 0:
            raise ValueError("AEF drain interval must be a positive finite number")


def drain_aef_database(
    config: AefRuntimeConfig,
    *,
    batch_size: int = DEFAULT_AEF_DRAIN_BATCH_SIZE,
    max_receipts: int = DEFAULT_AEF_DRAIN_MAX_RECEIPTS,
) -> int:
    """Open the configured stores, settle a bounded backlog, and close cleanly."""
    store = EventStore(
        config.db_path,
        key_file=config.db_key_file,
        aef_outbox_kinds=AEF_MAPPED_EVENT_KINDS,
    )
    try:
        with AefReceiptLog(
            config.db_path,
            hub_id=config.hub_id,
            signing_key=config.signing_key,
            key_file=config.db_key_file,
        ) as receipt_log:
            return _drain_open_stores(
                store,
                receipt_log,
                batch_size=batch_size,
                max_receipts=max_receipts,
            )
    finally:
        store.close()


def drain_aef_startup_backlog(config: AefRuntimeConfig) -> int:
    """Settle the complete pre-start backlog before the hub accepts traffic."""
    settled = 0
    while True:
        batch = drain_aef_database(config)
        settled += batch
        if batch < DEFAULT_AEF_DRAIN_MAX_RECEIPTS:
            return settled


def run_aef_outbox_worker(
    config: AefRuntimeConfig,
    stop: threading.Event,
    *,
    on_error: Callable[[Exception], None] | None = None,
) -> None:
    """Reconcile live outbox rows until ``stop`` is set.

    Each retry opens fresh connections. A failed projection or database write
    therefore leaves the durable outbox row pending, reports the failure, and
    retries without sharing the hub's SQLite connection across threads.
    """
    while not stop.is_set():
        try:
            drain_aef_database(config)
        except Exception as exc:  # noqa: BLE001 — worker must retain pending truth
            if on_error is not None:
                on_error(exc)
            else:
                logger.exception("AEF outbox drain failed; durable rows remain pending")
        stop.wait(config.interval_seconds)


def _drain_open_stores(
    store: EventStore,
    receipt_log: AefReceiptLog,
    *,
    batch_size: int,
    max_receipts: int,
) -> int:
    if isinstance(batch_size, bool) or batch_size < 1 or batch_size > 10_000:
        raise ValueError("AEF drain batch size must be from 1 through 10000")
    if isinstance(max_receipts, bool) or max_receipts < 1:
        raise ValueError("AEF drain receipt limit must be a positive integer")
    settled = 0
    while settled < max_receipts:
        limit = min(batch_size, max_receipts - settled)
        batch = drain_aef_outbox(store, receipt_log, limit=limit)
        settled += batch
        if batch < limit:
            break
    return settled
