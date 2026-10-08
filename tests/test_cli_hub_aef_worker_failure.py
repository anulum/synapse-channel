# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — real AEF worker contention during hub serving
"""Observe real SQLite writer contention through the complete hub command."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sqlite3
import threading
from collections.abc import Coroutine
from pathlib import Path

from synapse_channel.cli import build_parser
from synapse_channel.cli_processes_hub import _cmd_hub
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.hub_config import HubConfig
from synapse_channel.core.journal import EventKind
from synapse_channel.core.receipt_signing import generate_receipt_signing_key


def test_live_aef_worker_reports_contention_and_retains_pending_evidence(tmp_path: Path) -> None:
    """A real blocked receipt writer reports failure without claiming delivery."""
    database = tmp_path / "events.db"
    signing = tmp_path / "receipt-key"
    generate_receipt_signing_key(signing)
    observed = threading.Event()
    hubs: list[SynapseHub] = []
    messages: list[str] = []

    class ObserveError(logging.Handler):
        """Observe the actual worker's operator error without replacing logging."""

        def emit(self, record: logging.LogRecord) -> None:
            """Signal the real failure report issued by the serving worker."""
            message = record.getMessage()
            if "AEF outbox drain failed" in message:
                messages.append(message)
                observed.set()

    def build_hub(**options: object) -> SynapseHub:
        """Retain the actual hub built from the command's complete configuration."""
        hub = SynapseHub.from_config(HubConfig.from_kwargs(options))
        hubs.append(hub)
        return hub

    async def exercise(server: Coroutine[object, object, None]) -> None:
        """Hold a real SQLite writer lock while the actual server remains live."""
        journal = hubs[0].journal
        assert journal is not None
        sequence = journal.append(
            EventKind.CLAIM,
            {
                "task_id": "worker-proof",
                "owner": "agent",
                "claimed_at": 1.0,
                "lease_expires_at": 2.0,
                "epoch": 1,
                "paths": [],
            },
            durable=True,
        )
        blocker = sqlite3.connect(database, isolation_level=None)
        blocker.execute("BEGIN IMMEDIATE")
        task = asyncio.create_task(server)
        try:
            await hubs[0].wait_until_serving()
            assert await asyncio.to_thread(observed.wait, 15.0)
            assert journal.aef_delivery(sequence) is None
            assert messages and "locked" in messages[0].lower()
        finally:
            blocker.rollback()
            blocker.close()
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    def runner(server: Coroutine[object, object, None]) -> None:
        """Drive and reap the actual command server and its AEF thread."""
        asyncio.run(asyncio.wait_for(exercise(server), timeout=25.0))

    args = build_parser().parse_args(
        [
            "hub",
            "--db",
            str(database),
            "--hub-id",
            "worker-hub",
            "--aef-signing-key",
            str(signing),
            "--aef-drain-interval",
            "0.05",
            "--port",
            "0",
            "--identity-pins",
            "",
        ]
    )
    logger = logging.getLogger("synapse_channel.cli_hub_serving")
    handler = ObserveError()
    logger.addHandler(handler)
    try:
        assert _cmd_hub(args, runner=runner, hub_factory=build_hub) == 0
    finally:
        logger.removeHandler(handler)
        handler.close()
    assert observed.is_set()
