#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — release-only real old-wheel rollback gate
"""Prove that released 0.99.26 silently leaves v3 work for roll-forward."""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess  # fixed argv to an operator-supplied isolated Python
import sys
import tempfile
from pathlib import Path

from synapse_channel.core.delivery_modes import parse_delivery_intent
from synapse_channel.core.handlers.delivery_modes import expire_due_deliveries
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.persistence import EventStore

OLD_HUB = """
import asyncio
import contextlib
import sys
import synapse_channel
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.persistence import EventStore

assert synapse_channel.__version__ == '0.99.26', synapse_channel.__version__

async def main():
    with EventStore(sys.argv[1]) as store:
        hub = SynapseHub(journal=store, hub_id='rollback-hub')
        task = asyncio.create_task(hub.serve('127.0.0.1', 0))
        try:
            await hub.wait_until_serving(timeout=5)
            await asyncio.sleep(0.25)
            assert not task.done(), 'old hub stopped unexpectedly'
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

asyncio.run(main())
"""


def _queue_v3_follow_up(path: Path) -> str:
    """Write one queued v3 follow-up and return its operation key."""
    frame = {
        "protocol_version": 3,
        "target": "P/receiver",
        "request_id": "rollback-request",
        "idempotency_key": "rollback-key",
        "target_incarnation": "a" * 64,
        "mode": "follow_up",
        "task_id": "rollback-task",
        "body": "Preserve this queued delivery",
        "deadline": 160.0,
    }
    intent = parse_delivery_intent(frame, sender="P/author", origin_hub="rollback-hub", now=100.0)
    with EventStore(path) as store:
        write = store.delivery.create(
            intent,
            selected_mode="follow_up",
            quality="native",
            offer={
                "type": "delivery_offer",
                "operation_key": intent.operation_key,
                "notification_id": f"delivery:{intent.operation_key}:1",
                "target": intent.target,
                "target_incarnation": intent.target_incarnation,
            },
        )
        assert write.record.stage == "queued"
    return intent.operation_key


def _run_released_hub(old_python: Path, path: Path, temporary: str) -> None:
    """Run the isolated interpreter and refuse a non-zero exit."""
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    result = subprocess.run(  # explicit interpreter and fixed code; no shell
        [str(old_python), "-c", OLD_HUB, str(path)],
        cwd=temporary,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    if result.returncode:
        raise RuntimeError(f"released 0.99.26 hub failed: {result.stderr}")


async def _expire_preserved_work(path: Path, operation_key: str) -> None:
    """Require the queued record, expire it once, and verify the replay."""
    with EventStore(path) as restored:
        record = restored.delivery.get(operation_key)
        assert record is not None and record.stage == "queued", "old hub changed v3 work"
        hub = SynapseHub(journal=restored, hub_id="rollback-hub")
        assert await expire_due_deliveries(hub) == 1
        record = restored.delivery.get(operation_key)
        assert record is not None and record.stage == "expired"
        restored.delivery.verify_replay()
        hub.close()


async def _verify(old_python: Path) -> None:
    """Queue v3 work, run the released hub, then expire that preserved work."""
    with tempfile.TemporaryDirectory(prefix="synapse-rollback-") as temporary:
        path = Path(temporary) / "hub.db"
        operation_key = _queue_v3_follow_up(path)
        _run_released_hub(old_python, path, temporary)
        await _expire_preserved_work(path, operation_key)
        print("real 0.99.26 rollback preserved queued v3 work; 0.99.27 roll-forward expired it")


def main() -> int:
    """Accept only a caller-prepared isolated 0.99.26 interpreter."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("old_python", type=Path)
    args = parser.parse_args()
    if not args.old_python.is_file():
        parser.error("old_python must be an installed 0.99.26 interpreter")
    asyncio.run(_verify(args.old_python.absolute()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
